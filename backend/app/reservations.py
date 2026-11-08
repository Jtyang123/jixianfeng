from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from uuid import uuid4

from .config import RESERVATION_SWEEP_INTERVAL_SECONDS, RESERVATION_TTL_HOURS
from .database import Database, now_iso


class InventoryReservationError(ValueError):
    def __init__(self, message: str, shortages: list[dict] | None = None) -> None:
        super().__init__(message)
        self.shortages = shortages or []


class InventoryReservationService:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _summary(rows: list[dict]) -> dict:
        return {
            "count": len(rows),
            "total_quantity": round(sum(float(item.get("quantity") or 0) for item in rows), 2),
            "items": rows,
        }

    def _release_expired(self, conn) -> list[dict]:
        now = datetime.now().astimezone()
        rows = conn.execute(
            "SELECT * FROM inventory_reservations WHERE status='active'"
        ).fetchall()
        released: list[dict] = []
        affected_runs: set[str] = set()
        for raw in rows:
            row = dict(raw)
            try:
                expired = datetime.fromisoformat(row["expires_at"]) <= now
            except (TypeError, ValueError):
                expired = True
            if not expired:
                continue
            conn.execute(
                "UPDATE inventory SET locked_quantity=MAX(0, locked_quantity-?) WHERE id=?",
                (float(row["quantity"]), row["inventory_id"]),
            )
            conn.execute(
                "UPDATE inventory_reservations SET status='expired', released_at=? WHERE id=?",
                (now_iso(), row["id"]),
            )
            released.append(row)
            affected_runs.add(row["run_id"])
        for run_id in affected_runs:
            run_row = conn.execute("SELECT result_json, status FROM runs WHERE id=?", (run_id,)).fetchone()
            result = json.loads(run_row["result_json"] or "{}") if run_row else {}
            approval = result.get("approval") if isinstance(result.get("approval"), dict) else {}
            approval.update({"status": "expired", "expired_at": now_iso()})
            result["approval"] = approval
            next_status = "reservation_expired" if run_row and run_row["status"] == "approved" else (run_row["status"] if run_row else "reservation_expired")
            conn.execute(
                "UPDATE runs SET approved=0, approved_at=NULL, reserved_at=NULL, status=?, result_json=? WHERE id=?",
                (next_status, json.dumps(result, ensure_ascii=False), run_id),
            )
        return released

    def cleanup_expired(self) -> dict:
        with self.db.connect() as conn:
            released = self._release_expired(conn)
        runs = sorted({item["run_id"] for item in released})
        for run_id in runs:
            self.db.add_event(
                run_id, "inventory.reservation_expired", "orchestrator", "库存预留",
                "库存预留已过期释放", f"释放 {sum(1 for item in released if item['run_id'] == run_id)} 条库存预留。",
                {"released_count": sum(1 for item in released if item['run_id'] == run_id)},
            )
        return {"released_count": len(released), "runs": runs, "items": released}

    def active_for_run(self, run_id: str) -> list[dict]:
        self.cleanup_expired()
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory_reservations WHERE run_id=? AND status='active' ORDER BY id",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def reserve(self, run_id: str, plan: dict, user: dict, note: str = "", ttl_hours: int = RESERVATION_TTL_HOURS) -> dict:
        existing = self.active_for_run(run_id)
        if existing:
            return self._summary(existing)
        assignments = plan.get("assignments") or []
        required: dict[tuple[str, int], float] = {}
        for item in assignments:
            key = (str(item["warehouse_id"]), int(item["material_id"]))
            required[key] = required.get(key, 0.0) + float(item.get("quantity") or 0)
        if not required:
            raise InventoryReservationError("推荐方案没有可预留的物资分配")

        with self.db.connect() as conn:
            if self.db.engine == "sqlite":
                conn.execute("BEGIN IMMEDIATE")
            self._release_expired(conn)
            snapshot = conn.execute(
                "SELECT id FROM inventory_snapshots WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not snapshot:
                raise InventoryReservationError("没有可用库存快照，无法审批")
            reservations: list[dict] = []
            shortages: list[dict] = []
            expires_at = (datetime.now().astimezone() + timedelta(hours=ttl_hours)).isoformat(timespec="seconds")
            today = datetime.now().date().isoformat()
            for (warehouse_id, material_id), quantity in required.items():
                remaining = quantity
                rows = conn.execute(
                    """SELECT id, quantity, locked_quantity, batch_no, expiry_date
                       FROM inventory
                       WHERE snapshot_id=? AND warehouse_id=? AND material_id=? AND status='available'
                         AND (expiry_date IS NULL OR expiry_date>=?)
                       ORDER BY CASE WHEN expiry_date IS NULL THEN 1 ELSE 0 END, expiry_date, id""",
                    (snapshot["id"], warehouse_id, material_id, today),
                ).fetchall()
                for raw in rows:
                    row = dict(raw)
                    available = max(0.0, float(row["quantity"]) - float(row["locked_quantity"] or 0) - float(row.get("reserved_here") or 0))
                    if available <= 0.001:
                        continue
                    take = min(remaining, available)
                    conn.execute(
                        """UPDATE inventory SET locked_quantity=locked_quantity+?, updated_at=?
                           WHERE id=? AND quantity-locked_quantity>=?""",
                        (take, now_iso(), row["id"], take),
                    )
                    reservation_id = f"RES-{uuid4().hex[:12].upper()}"
                    conn.execute(
                        """INSERT INTO inventory_reservations
                           (id, run_id, inventory_id, warehouse_id, material_id, quantity, status, expires_at, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                        (reservation_id, run_id, row["id"], warehouse_id, material_id, round(take, 2), expires_at, now_iso()),
                    )
                    reservations.append({
                        "reservation_id": reservation_id,
                        "inventory_id": row["id"],
                        "warehouse_id": warehouse_id,
                        "material_id": material_id,
                        "quantity": round(take, 2),
                        "batch_no": row.get("batch_no", ""),
                        "expiry_date": row.get("expiry_date"),
                    })
                    remaining -= take
                    if remaining <= 0.001:
                        break
                if remaining > 0.001:
                    shortages.append({
                        "warehouse_id": warehouse_id,
                        "material_id": material_id,
                        "shortage": round(remaining, 2),
                    })
            if shortages:
                raise InventoryReservationError("库存已被其他任务预留，当前审批未通过", shortages)

            run_row = conn.execute("SELECT result_json FROM runs WHERE id=?", (run_id,)).fetchone()
            result = json.loads(run_row["result_json"] or "{}") if run_row else {}
            approval = {
                "approved_by": user.get("id"),
                "approved_by_name": user.get("display_name") or user.get("username", ""),
                "approved_at": now_iso(),
                "note": note,
                "inventory_reservation": self._summary(reservations),
            }
            result["approval"] = approval
            conn.execute(
                """UPDATE runs SET approved=1, approved_at=?, approved_by=?, approved_by_name=?,
                   approval_note=?, reserved_at=?, status='approved', result_json=?
                   WHERE id=?""",
                (approval["approved_at"], approval["approved_by"], approval["approved_by_name"],
                 note, approval["approved_at"], json.dumps(result, ensure_ascii=False), run_id),
            )
        return self._summary(reservations)

    def reject_run(self, run_id: str, user: dict, note: str = "") -> dict:
        released = self.release(run_id)
        with self.db.connect() as conn:
            row = conn.execute("SELECT result_json FROM runs WHERE id=?", (run_id,)).fetchone()
            result = json.loads(row["result_json"] or "{}") if row else {}
            result["approval"] = {
                "status": "rejected",
                "rejected_by": user.get("id"),
                "rejected_by_name": user.get("display_name") or user.get("username", ""),
                "rejected_at": now_iso(),
                "note": note,
                "inventory_reservation": released,
            }
            conn.execute(
                """UPDATE runs SET approved=0, approved_at=NULL, approved_by=?, approved_by_name=?,
                   approval_note=?, reserved_at=NULL, status='rejected', result_json=? WHERE id=?""",
                (user.get("id"), user.get("display_name") or user.get("username", ""), note,
                 json.dumps(result, ensure_ascii=False), run_id),
            )
        return {"rejected": True, "released": released}

    def release(self, run_id: str) -> dict:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory_reservations WHERE run_id=? AND status='active'", (run_id,)
            ).fetchall()
            for raw in rows:
                row = dict(raw)
                conn.execute(
                    "UPDATE inventory SET locked_quantity=MAX(0, locked_quantity-?) WHERE id=?",
                    (float(row["quantity"]), row["inventory_id"]),
                )
                conn.execute(
                    "UPDATE inventory_reservations SET status='released', released_at=? WHERE id=?",
                    (now_iso(), row["id"]),
                )
            if rows:
                run_row = conn.execute("SELECT result_json FROM runs WHERE id=?", (run_id,)).fetchone()
                result = json.loads(run_row["result_json"] or "{}") if run_row else {}
                if isinstance(result.get("approval"), dict):
                    result["approval"]["status"] = "released"
                    result["approval"]["released_at"] = now_iso()
                conn.execute(
                    """UPDATE runs SET approved=0, approved_at=NULL, approved_by=NULL, approved_by_name='',
                       reserved_at=NULL, status='awaiting_approval', result_json=? WHERE id=?""",
                    (json.dumps(result, ensure_ascii=False), run_id),
                )
        return self._summary([dict(row) for row in rows])

class ReservationMaintenanceWorker:
    def __init__(self, db_provider) -> None:
        self.db_provider = db_provider
        self.task: asyncio.Task | None = None
        self.stop_event = asyncio.Event()

    async def start(self) -> None:
        if self.task:
            return
        self.stop_event = asyncio.Event()
        self.task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                InventoryReservationService(self.db_provider()).cleanup_expired()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=RESERVATION_SWEEP_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
