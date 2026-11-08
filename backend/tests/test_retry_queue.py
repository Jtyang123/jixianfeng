from pathlib import Path

from app.database import Database
from app.retry_queue import RetryQueueRepository


def test_persistent_retry_queue_moves_failures_to_dead_letter(tmp_path: Path):
    db = Database(tmp_path / "retry.db", database_url="")
    db.initialize()
    repo = RetryQueueRepository(db)
    job = repo.enqueue("agentarts", {"agent_id": "demand_agent", "payload": {}}, "timeout", run_id="RUN-RETRY", max_attempts=1)
    with db.connect() as conn:
        conn.execute("UPDATE retry_jobs SET next_attempt_at='2000-01-01T00:00:00+08:00' WHERE id=?", (job["id"],))
    claimed = repo.claim_due()
    assert claimed["id"] == job["id"]
    assert claimed["attempts"] == 1
    assert repo.fail(job["id"], "第二次仍超时") == "dead_letter"
    dead = repo.get_job(job["id"])
    assert dead["status"] == "dead_letter"
    assert repo.requeue(job["id"])["status"] == "pending"
