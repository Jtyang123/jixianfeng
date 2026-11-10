from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from app import api as api_module
from app.database import Database
from app.main import app
from app.operations import OperationsRepository
from app.reservations import InventoryReservationError, InventoryReservationService


def _plan(inventory_row: dict) -> dict:
    return {
        "plan_id": "PLAN-APPROVAL-TEST",
        "assignments": [{
            "warehouse_id": inventory_row["warehouse_id"],
            "warehouse_name": inventory_row["warehouse_name"],
            "material_id": inventory_row["material_id"],
            "quantity": inventory_row["quantity"],
        }],
        "metrics": {},
    }


def test_reservation_prevents_double_commit_and_release_restores_inventory(tmp_path: Path):
    db = Database(tmp_path / "reservations.db", database_url="")
    db.initialize()
    inventory = db.inventory_rows(db.latest_snapshot()["id"])
    row = next(item for item in inventory if item["quantity"] > 10 and item["locked_quantity"] == 0)
    plan = _plan(row)
    result = {
        "delivery": {"recommended_plan": {"plan_id": plan["plan_id"]}},
        "rounds": [{"plans": [plan]}],
    }
    for run_id in ("RUN-RESERVE-1", "RUN-RESERVE-2"):
        db.create_run(run_id, "rainstorm-ii", {"scenario_id": "rainstorm-ii"})
        db.update_run(run_id, "awaiting_approval", result)

    service = InventoryReservationService(db)
    first = service.reserve("RUN-RESERVE-1", plan, {"id": 7, "username": "dispatcher", "display_name": "值班员"}, "同意")
    assert first["count"] == 1
    with pytest.raises(InventoryReservationError):
        service.reserve("RUN-RESERVE-2", plan, {"id": 8, "username": "admin"}, "同意")

    stored = db.get_run("RUN-RESERVE-1")
    assert stored["status"] == "approved"
    assert stored["approved_by_name"] == "值班员"
    assert stored["result"]["approval"]["inventory_reservation"]["count"] == 1
    summary_row = next(item for item in db.inventory_summary() if item["material_id"] == row["material_id"])
    assert summary_row["locked_quantity"] >= row["quantity"]
    assert summary_row["available_quantity"] <= summary_row["usable_quantity"] - row["quantity"]
    active = db.list_active_reservations()
    assert any(item["run_id"] == "RUN-RESERVE-1" for item in active)
    with db.connect() as conn:
        locked = conn.execute("SELECT locked_quantity FROM inventory WHERE id=?", (row["id"],)).fetchone()["locked_quantity"]
    assert locked >= row["quantity"]

    released = service.release("RUN-RESERVE-1")
    assert released["count"] == 1
    second = service.reserve("RUN-RESERVE-2", plan, {"id": 8, "username": "admin"}, "重试审批")
    assert second["count"] == 1


def test_expired_reservation_is_released_and_run_marked(tmp_path: Path):
    db = Database(tmp_path / "reservation-expiry.db", database_url="")
    db.initialize()
    inventory = db.inventory_rows(db.latest_snapshot()["id"])
    row = next(item for item in inventory if item["quantity"] > 10 and item["locked_quantity"] == 0)
    plan = _plan(row)
    db.create_run("RUN-RESERVE-EXPIRED", "rainstorm-ii", {"scenario_id": "rainstorm-ii"})
    db.update_run("RUN-RESERVE-EXPIRED", "awaiting_approval", {"delivery": {"recommended_plan": {"plan_id": plan["plan_id"]}}, "rounds": [{"plans": [plan]}]})
    service = InventoryReservationService(db)
    service.reserve("RUN-RESERVE-EXPIRED", plan, {"id": 9, "username": "admin"}, "同意", ttl_hours=1)
    with db.connect() as conn:
        conn.execute("UPDATE inventory_reservations SET expires_at='2000-01-01T00:00:00+08:00' WHERE run_id=?", ("RUN-RESERVE-EXPIRED",))
    cleaned = service.cleanup_expired()
    assert cleaned["released_count"] == 1
    stored = db.get_run("RUN-RESERVE-EXPIRED")
    assert stored["status"] == "reservation_expired"
    assert stored["approved"] == 0
    assert stored["result"]["approval"]["status"] == "expired"
    with db.connect() as conn:
        locked = conn.execute("SELECT locked_quantity FROM inventory WHERE id=?", (row["id"],)).fetchone()["locked_quantity"]
    assert locked == 0


def test_invalid_or_naive_expiry_does_not_release_active_reservations(tmp_path: Path):
    db = Database(tmp_path / "reservation-invalid-expiry.db", database_url="")
    db.initialize()
    rows = [item for item in db.inventory_rows(db.latest_snapshot()["id"]) if item["quantity"] >= 5][:3]
    plan = {
        "plan_id": "PLAN-INVALID-EXPIRY",
        "assignments": [
            {
                "warehouse_id": item["warehouse_id"],
                "warehouse_name": item["warehouse_name"],
                "material_id": item["material_id"],
                "quantity": 5,
            }
            for item in rows
        ],
        "metrics": {},
    }
    db.create_run("RUN-INVALID-EXPIRY", "rainstorm-ii", {"scenario_id": "rainstorm-ii"})
    db.update_run("RUN-INVALID-EXPIRY", "awaiting_approval", {
        "delivery": {"recommended_plan": {"plan_id": plan["plan_id"]}},
        "rounds": [{"plans": [plan]}],
    })
    service = InventoryReservationService(db)
    reserved = service.reserve("RUN-INVALID-EXPIRY", plan, {"id": 1, "username": "admin"}, "test")
    assert reserved["count"] == 3
    naive_future = (datetime.now() + timedelta(days=1)).replace(tzinfo=None).isoformat(sep=" ")
    values = [naive_future, "not-a-timestamp", ""]
    with db.connect() as conn:
        reservations = conn.execute(
            "SELECT id FROM inventory_reservations WHERE run_id=? ORDER BY id",
            ("RUN-INVALID-EXPIRY",),
        ).fetchall()
        for row, value in zip(reservations, values):
            conn.execute("UPDATE inventory_reservations SET expires_at=? WHERE id=?", (value, row["id"]))
    cleaned = service.cleanup_expired()
    assert cleaned["released_count"] == 0
    assert cleaned["invalid_expiry_count"] == 2
    with db.connect() as conn:
        locked = conn.execute(
            "SELECT SUM(locked_quantity) AS total FROM inventory WHERE id IN (?, ?, ?)",
            tuple(item["id"] for item in rows),
        ).fetchone()["total"]
        statuses = [row["status"] for row in conn.execute(
            "SELECT status FROM inventory_reservations WHERE run_id=?",
            ("RUN-INVALID-EXPIRY",),
        ).fetchall()]
    assert locked == 15
    assert statuses == ["active", "active", "active"]

def test_concurrent_release_only_decrements_locked_quantity_once(tmp_path: Path):
    db = Database(tmp_path / "reservation-concurrent-release.db", database_url="")
    db.initialize()
    inventory = db.inventory_rows(db.latest_snapshot()["id"])
    row = next(item for item in inventory if item["quantity"] > 10 and item["locked_quantity"] == 0)
    plan = _plan(row)
    db.create_run("RUN-CONCURRENT-RELEASE", "rainstorm-ii", {"scenario_id": "rainstorm-ii"})
    db.update_run("RUN-CONCURRENT-RELEASE", "awaiting_approval", {
        "delivery": {"recommended_plan": {"plan_id": plan["plan_id"]}},
        "rounds": [{"plans": [plan]}],
    })
    InventoryReservationService(db).reserve(
        "RUN-CONCURRENT-RELEASE", plan, {"id": 1, "username": "admin"}, "test"
    )
    barrier = Barrier(2)

    def release_once():
        barrier.wait()
        return InventoryReservationService(db).release("RUN-CONCURRENT-RELEASE")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: release_once(), range(2)))
    assert sorted(item["count"] for item in results) == [0, 1]
    with db.connect() as conn:
        locked = conn.execute("SELECT locked_quantity FROM inventory WHERE id=?", (row["id"],)).fetchone()["locked_quantity"]
    assert locked == 0

def test_second_release_request_does_not_duplicate_audit_event(tmp_path: Path):
    db = Database(tmp_path / "reservation-release-audit.db", database_url="")
    db.initialize()
    api_module.db = db
    api_module.ops = OperationsRepository(db)
    inventory = db.inventory_rows(db.latest_snapshot()["id"])
    row = next(item for item in inventory if item["quantity"] > 10 and item["locked_quantity"] == 0)
    plan = _plan(row)
    run_id = "RUN-RELEASE-AUDIT"
    db.create_run(run_id, "rainstorm-ii", {"scenario_id": "rainstorm-ii"})
    db.update_run(run_id, "awaiting_approval", {
        "delivery": {"recommended_plan": {"plan_id": plan["plan_id"]}},
        "rounds": [{"plans": [plan]}],
    })
    InventoryReservationService(db).reserve(run_id, plan, {"id": 1, "username": "admin"}, "test")
    client = TestClient(app)
    with client:
        first = client.post(f"/api/runs/{run_id}/release-reservation")
        second = client.post(f"/api/runs/{run_id}/release-reservation")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["count"] == 1
    assert second.json()["count"] == 0
    release_events = [
        event for event in db.events_after(run_id)
        if event["type"] == "inventory.reservation_released"
    ]
    assert len(release_events) == 1
