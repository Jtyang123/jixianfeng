from __future__ import annotations

import asyncio
import inspect
import math
from datetime import datetime, timedelta
from typing import Awaitable, Callable
from uuid import uuid4

from .config import (
    RUN_LEASE_SECONDS,
    RUN_POLL_INTERVAL_SECONDS,
    RUN_QUEUE_MAX_SIZE,
    RUN_WORKER_CONCURRENCY,
)
from .database import Database, now_iso


class QueueFullError(ValueError):
    pass


def run_priority(request: dict) -> float:
    event = request.get("event") or {}
    severity = str(event.get("severity") or "III级")
    severity_score = {"I级": 100.0, "II级": 82.0, "III级": 62.0, "IV级": 42.0}.get(severity, 50.0)
    demands = request.get("demand_items") or []
    priorities = [str(item.get("priority") or "P2") for item in demands]
    priority_score = max(
        [{"P0": 100.0, "P1": 82.0, "P2": 62.0, "P3": 42.0}.get(item, 50.0) for item in priorities] or [50.0]
    )
    deadlines = [float(item.get("deadline_hours") or 24) for item in demands]
    urgency = max(0.0, min(1.0, (24.0 - min(deadlines or [24.0])) / 24.0))
    population = max(0, int(event.get("affected_population") or 0))
    population_score = min(1.0, math.log10(max(population, 1)) / 6.0)
    return round(severity_score * 0.45 + priority_score * 0.25 + urgency * 100 * 0.20 + population_score * 100 * 0.10, 2)


class RunQueueService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def enqueue(self, run_id: str, priority_score: float) -> dict:
        with self.db.connect() as conn:
            active = conn.execute(
                "SELECT COUNT(*) AS c FROM runs WHERE status IN ('queued','running')"
            ).fetchone()["c"]
            if int(active) >= RUN_QUEUE_MAX_SIZE:
                raise QueueFullError(f"运行队列已满（上限 {RUN_QUEUE_MAX_SIZE}），请稍后重试")
            conn.execute(
                "UPDATE runs SET priority_score=?, queued_at=COALESCE(queued_at, ?) WHERE id=?",
                (float(priority_score), now_iso(), run_id),
            )
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            return dict(row) if row else {}

    def recover_stale(self) -> dict:
        now = datetime.now().astimezone()
        recovered = 0
        dead_lettered = 0
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT id, attempts, max_attempts, lease_expires_at FROM runs WHERE status='running'"
            ).fetchall()
            for raw in rows:
                row = dict(raw)
                expired = True
                if row.get("lease_expires_at"):
                    try:
                        expired = datetime.fromisoformat(row["lease_expires_at"]) <= now
                    except ValueError:
                        expired = True
                if not expired:
                    continue
                if int(row.get("attempts") or 0) < int(row.get("max_attempts") or 3):
                    conn.execute(
                        """UPDATE runs SET status='queued', lease_owner='', lease_expires_at=NULL,
                           last_error='租约过期，已自动重新入队' WHERE id=? AND status='running'""",
                        (row["id"],),
                    )
                    recovered += 1
                else:
                    conn.execute(
                        """UPDATE runs SET status='failed', lease_owner='', lease_expires_at=NULL,
                           completed_at=?, last_error='租约过期且达到最大重试次数'
                           WHERE id=? AND status='running'""",
                        (now_iso(), row["id"]),
                    )
                    dead_lettered += 1
        return {"recovered": recovered, "dead_lettered": dead_lettered}

    def claim_next(self, worker_id: str, lease_seconds: int = RUN_LEASE_SECONDS) -> dict | None:
        now = datetime.now().astimezone()
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
        with self.db.connect() as conn:
            if self.db.engine == "postgresql":
                row = conn.execute(
                    """SELECT id FROM runs WHERE status='queued'
                       ORDER BY priority_score DESC, queued_at, created_at
                       LIMIT 1 FOR UPDATE SKIP LOCKED"""
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT id FROM runs WHERE status='queued'
                       ORDER BY priority_score DESC, queued_at, created_at LIMIT 1"""
                ).fetchone()
            if not row:
                return None
            run_id = row["id"]
            conn.execute(
                """UPDATE runs SET status='running', lease_owner=?, lease_expires_at=?,
                   attempts=attempts+1, started_at=COALESCE(started_at, ?), last_error=''
                   WHERE id=? AND status='queued'""",
                (worker_id, lease_until, now_iso(), run_id),
            )
            claimed = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if not claimed or claimed["lease_owner"] != worker_id:
                return None
            return dict(claimed)

    def heartbeat(self, run_id: str, worker_id: str, lease_seconds: int = RUN_LEASE_SECONDS) -> bool:
        lease_until = (datetime.now().astimezone() + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE runs SET lease_expires_at=? WHERE id=? AND status='running' AND lease_owner=?",
                (lease_until, run_id, worker_id),
            )
            row = conn.execute("SELECT lease_owner FROM runs WHERE id=?", (run_id,)).fetchone()
            return bool(row and row["lease_owner"] == worker_id)

    def finish(self, run_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE runs SET lease_owner='', lease_expires_at=NULL WHERE id=?", (run_id,))

    def retry_or_fail(self, run_id: str, error: str) -> str:
        with self.db.connect() as conn:
            row = conn.execute("SELECT attempts, max_attempts FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return "missing"
            if int(row["attempts"]) < int(row["max_attempts"]):
                conn.execute(
                    """UPDATE runs SET status='queued', queued_at=?, lease_owner='', lease_expires_at=NULL,
                       completed_at=NULL, result_json=NULL, last_error=? WHERE id=?""",
                    (now_iso(), str(error)[:900], run_id),
                )
                return "queued"
            conn.execute(
                """UPDATE runs SET status='failed', completed_at=?, lease_owner='', lease_expires_at=NULL,
                   last_error=? WHERE id=?""",
                (now_iso(), str(error)[:900], run_id),
            )
            return "failed"

    def metrics(self) -> dict:
        with self.db.connect() as conn:
            counts = {row["status"]: int(row["c"]) for row in conn.execute(
                "SELECT status, COUNT(*) AS c FROM runs GROUP BY status"
            ).fetchall()}
            oldest = conn.execute(
                "SELECT queued_at FROM runs WHERE status='queued' ORDER BY queued_at LIMIT 1"
            ).fetchone()
            oldest = dict(oldest) if oldest else None
            workers = conn.execute(
                "SELECT lease_owner, COUNT(*) AS c FROM runs WHERE status='running' AND lease_owner<>'' GROUP BY lease_owner"
            ).fetchall()
        oldest_wait_ms = 0.0
        if oldest and oldest.get("queued_at"):
            try:
                oldest_wait_ms = max(0.0, (datetime.now().astimezone() - datetime.fromisoformat(oldest["queued_at"])).total_seconds() * 1000)
            except ValueError:
                pass
        return {
            "engine": "persistent DB queue with lease heartbeat",
            "concurrency_limit": RUN_WORKER_CONCURRENCY,
            "max_queue_size": RUN_QUEUE_MAX_SIZE,
            "queued": counts.get("queued", 0), "running": counts.get("running", 0),
            "awaiting_approval": counts.get("awaiting_approval", 0), "approved": counts.get("approved", 0),
            "failed": counts.get("failed", 0), "oldest_wait_ms": round(oldest_wait_ms, 2),
            "active_workers": {row["lease_owner"]: int(row["c"]) for row in workers},
        }


class RunWorkerPool:
    def __init__(
        self, db_provider: Callable[[], Database],
        orchestrator: Callable[[Database, str, dict], Awaitable[None]],
        concurrency: int = RUN_WORKER_CONCURRENCY,
        poll_interval: float = RUN_POLL_INTERVAL_SECONDS,
        lease_seconds: int = RUN_LEASE_SECONDS,
    ) -> None:
        self.db_provider = db_provider
        self.orchestrator = orchestrator
        self.concurrency = max(1, min(concurrency, 16))
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self.instance_id = f"worker-{uuid4().hex[:8]}"
        self.tasks: list[asyncio.Task] = []
        self.stop_event = asyncio.Event()

    async def start(self) -> None:
        if self.tasks:
            return
        self.stop_event = asyncio.Event()
        RunQueueService(self.db_provider()).recover_stale()
        for index in range(self.concurrency):
            self.tasks.append(asyncio.create_task(self._worker_loop(f"{self.instance_id}-{index + 1}")))

    async def stop(self) -> None:
        self.stop_event.set()
        tasks = list(self.tasks)
        self.tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _worker_loop(self, worker_id: str) -> None:
        while not self.stop_event.is_set():
            db = self.db_provider()
            queue = RunQueueService(db)
            try:
                claimed = queue.claim_next(worker_id, self.lease_seconds)
            except Exception:
                await asyncio.sleep(self.poll_interval)
                continue
            if not claimed:
                await asyncio.sleep(self.poll_interval)
                continue
            run_id = claimed["id"]
            run = db.get_run(run_id) or {}
            request = run.get("request") or {}
            wait_ms = 0.0
            if claimed.get("queued_at"):
                try:
                    wait_ms = max(0.0, (datetime.now().astimezone() - datetime.fromisoformat(claimed["queued_at"])).total_seconds() * 1000)
                except ValueError:
                    pass
            db.add_event(
                run_id, "queue.claimed", "run_scheduler", "平台运行队列", "运行任务已被平台调度器领取",
                f"Worker {worker_id} 已获得租约，排队 {wait_ms:.0f} ms。",
                {"worker_id": worker_id, "wait_ms": round(wait_ms, 2), "attempt": claimed.get("attempts")},
            )
            heartbeat = asyncio.create_task(self._heartbeat_loop(queue, run_id, worker_id))
            try:
                result = self.orchestrator(db, run_id, request)
                if inspect.isawaitable(result):
                    await result
                finished = db.get_run(run_id)
                if finished and finished["status"] == "failed":
                    queue.retry_or_fail(run_id, finished.get("last_error") or "运行执行失败")
                else:
                    queue.finish(run_id)
            except Exception as exc:
                queue.retry_or_fail(run_id, str(exc))
                db.add_event(run_id, "queue.worker_failed", "run_scheduler", "平台运行队列",
                             "Worker 执行异常", str(exc)[:240], {"worker_id": worker_id})
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat_loop(self, queue: RunQueueService, run_id: str, worker_id: str) -> None:
        while True:
            await asyncio.sleep(max(5, self.lease_seconds // 3))
            if not queue.heartbeat(run_id, worker_id, self.lease_seconds):
                return
