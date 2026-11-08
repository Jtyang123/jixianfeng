from datetime import datetime, timedelta
from pathlib import Path

from app.database import Database
from app.run_queue import RunQueueService, run_priority


def test_persistent_queue_priority_claim_and_stale_recovery(tmp_path: Path):
    db = Database(tmp_path / "queue.db", database_url="")
    db.initialize()
    service = RunQueueService(db)
    for run_id, priority in (("RUN-LOW", 20.0), ("RUN-HIGH", 95.0)):
        db.create_run(run_id, "rainstorm-ii", {"scenario_id": "rainstorm-ii"})
        service.enqueue(run_id, priority)

    claimed = service.claim_next("worker-a", lease_seconds=1)
    assert claimed is not None
    assert claimed["id"] == "RUN-HIGH"
    assert claimed["status"] == "running"
    assert claimed["attempts"] == 1
    assert service.claim_next("worker-b", lease_seconds=1)["id"] == "RUN-LOW"

    with db.connect() as conn:
        conn.execute(
            "UPDATE runs SET lease_expires_at=? WHERE id=?",
            ((datetime.now().astimezone() - timedelta(seconds=5)).isoformat(timespec="seconds"), "RUN-HIGH"),
        )
    recovered = service.recover_stale()
    assert recovered["recovered"] >= 1
    claimed_again = service.claim_next("worker-c", lease_seconds=30)
    assert claimed_again["id"] == "RUN-HIGH"
    assert claimed_again["attempts"] == 2
    assert service.heartbeat("RUN-HIGH", "worker-c", 30)
    service.finish("RUN-HIGH")
    metrics = service.metrics()
    assert metrics["queued"] + metrics["running"] >= 1
    assert metrics["engine"].startswith("persistent DB queue")


def test_run_priority_reacts_to_severity_priority_and_deadline():
    urgent = run_priority({
        "event": {"severity": "I级", "affected_population": 500000},
        "demand_items": [{"priority": "P0", "deadline_hours": 2}],
    })
    routine = run_priority({
        "event": {"severity": "III级", "affected_population": 2000},
        "demand_items": [{"priority": "P2", "deadline_hours": 24}],
    })
    assert urgent > routine


def test_idempotency_key_reuses_existing_run(tmp_path: Path):
    db = Database(tmp_path / "idempotency.db", database_url="")
    db.initialize()
    db.create_run("RUN-IDEMPOTENT", "rainstorm-ii", {"scenario_id": "rainstorm-ii"}, idempotency_key="client-001")
    existing = db.get_run_by_idempotency("client-001")
    assert existing["id"] == "RUN-IDEMPOTENT"
