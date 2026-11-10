from __future__ import annotations

import os
import threading
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app.database import Database
from app.reservations import InventoryReservationError, InventoryReservationService
from app.run_queue import RunQueueService


POSTGRES_URL = os.getenv("FARMMATCH_TEST_POSTGRES_URL", "").strip()
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="未配置 FARMMATCH_TEST_POSTGRES_URL")


def _db() -> Database:
    db = Database(Path("unused-pg.db"), database_url=POSTGRES_URL)
    db.initialize()
    return db


def _reset_runtime(db: Database) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM a2a_messages")
        conn.execute("DELETE FROM inventory_reservations")
        conn.execute("DELETE FROM agent_events")
        conn.execute("DELETE FROM dispatch_tasks")
        conn.execute("DELETE FROM runs")
        conn.execute("UPDATE inventory SET locked_quantity=0")


def test_postgres_migration_creates_queue_and_citation_columns():
    db = _db()
    assert db.engine == "postgresql"
    with db.connect() as conn:
        run_columns = {
            row["column_name"] for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?", ("runs",)
            ).fetchall()
        }
        chunk_columns = {
            row["column_name"] for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?", ("knowledge_chunks",)
            ).fetchall()
        }
        reservation_table = conn.execute(
            "SELECT to_regclass(?) AS name", ("inventory_reservations",)
        ).fetchone()["name"]
    assert {"priority_score", "lease_owner", "attempts", "idempotency_key", "approved_by"} <= run_columns
    assert {"page_no", "section", "char_start", "char_end", "embedding_provider"} <= chunk_columns
    assert reservation_table == "inventory_reservations"
    assert db.list_materials() and db.warehouse_rows()


def test_postgres_skip_locked_claims_a_different_run_while_first_row_is_locked():
    db = _db()
    _reset_runtime(db)
    service = RunQueueService(db)
    first = f"PG-LOCK-A-{uuid4().hex[:8]}"
    second = f"PG-LOCK-B-{uuid4().hex[:8]}"
    db.create_run(first, "rainstorm-ii", {"scenario_id": "rainstorm-ii"})
    db.create_run(second, "rainstorm-ii", {"scenario_id": "rainstorm-ii"})
    service.enqueue(first, 100)
    service.enqueue(second, 99)

    with psycopg.connect(POSTGRES_URL, row_factory=dict_row) as locked_conn:
        locked = locked_conn.execute(
            """SELECT id FROM runs WHERE id=%s AND status='queued'
               FOR UPDATE SKIP LOCKED""",
            (first,),
        ).fetchone()
        assert locked["id"] == first
        claimed = service.claim_next("pg-worker-skip-locked", lease_seconds=30)
        assert claimed is not None
        assert claimed["id"] == second
        locked_conn.rollback()

    service.finish(second)


def test_postgres_reservation_allows_only_one_of_two_concurrent_approvals():
    db = _db()
    _reset_runtime(db)
    inventory = db.inventory_rows(db.latest_snapshot()["id"])
    row = next(item for item in inventory if item["quantity"] > 10)
    plan = {
        "plan_id": "PLAN-PG-RESERVE",
        "assignments": [{
            "warehouse_id": row["warehouse_id"], "warehouse_name": row["warehouse_name"],
            "material_id": row["material_id"], "quantity": row["quantity"],
        }],
        "metrics": {},
    }
    run_ids = [f"PG-RES-{uuid4().hex[:10]}-{index}" for index in range(2)]
    for run_id in run_ids:
        db.create_run(run_id, "rainstorm-ii", {"scenario_id": "rainstorm-ii"})
        db.update_run(run_id, "awaiting_approval", {
            "delivery": {"recommended_plan": {"plan_id": plan["plan_id"]}},
            "rounds": [{"plans": [plan]}],
        })

    barrier = threading.Barrier(2)
    results: list[tuple[str, str]] = []

    def reserve(run_id: str) -> None:
        barrier.wait(timeout=5)
        try:
            InventoryReservationService(db).reserve(run_id, plan, {"id": 1, "username": "pg-test"}, "并发审批")
            results.append((run_id, "ok"))
        except InventoryReservationError:
            results.append((run_id, "conflict"))

    threads = [threading.Thread(target=reserve, args=(run_id,)) for run_id in run_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(result for _, result in results) == ["conflict", "ok"]
    with db.connect() as conn:
        active = conn.execute(
            "SELECT COUNT(*) AS c FROM inventory_reservations WHERE run_id IN (?,?) AND status='active'",
            tuple(run_ids),
        ).fetchone()["c"]
    assert active == 1
