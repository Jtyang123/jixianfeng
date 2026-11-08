from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from uuid import uuid4

from .config import RETRY_POLL_INTERVAL_SECONDS
from .database import Database, now_iso


class RetryQueueRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def enqueue(self, kind: str, payload: dict, error: str, *, run_id: str = "", max_attempts: int = 3) -> dict:
        job_id = f"RETRY-{uuid4().hex[:12].upper()}"
        now = datetime.now().astimezone()
        next_attempt = (now + timedelta(seconds=5)).isoformat(timespec="seconds")
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO retry_jobs
                   (id, kind, run_id, payload_json, status, attempts, max_attempts, next_attempt_at,
                    last_error, result_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?, '{}', ?, ?)""",
                (job_id, kind, run_id, json.dumps(payload, ensure_ascii=False), max_attempts,
                 next_attempt, str(error)[:1000], now_iso(), now_iso()),
            )
            row = conn.execute("SELECT * FROM retry_jobs WHERE id=?", (job_id,)).fetchone()
        return self._row(row)

    @staticmethod
    def _row(row) -> dict:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        item["result"] = json.loads(item.pop("result_json") or "{}")
        return item

    def list_jobs(self, status: str | None = None, limit: int = 100) -> list[dict]:
        sql, params = "SELECT * FROM retry_jobs", []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        with self.db.connect() as conn:
            return [self._row(row) for row in conn.execute(sql, tuple(params)).fetchall()]

    def get_job(self, job_id: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM retry_jobs WHERE id=?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def claim_due(self) -> dict | None:
        now = now_iso()
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM retry_jobs WHERE status='pending' AND next_attempt_at<=? ORDER BY next_attempt_at LIMIT 1",
                (now,),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE retry_jobs SET status='retrying', attempts=attempts+1, updated_at=? WHERE id=? AND status='pending'",
                (now, row["id"]),
            )
            updated = conn.execute("SELECT * FROM retry_jobs WHERE id=?", (row["id"],)).fetchone()
        return self._row(updated)

    def requeue(self, job_id: str) -> dict | None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE retry_jobs SET status='pending', next_attempt_at=?, updated_at=? WHERE id=?",
                (now_iso(), now_iso(), job_id),
            )
            row = conn.execute("SELECT * FROM retry_jobs WHERE id=?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def complete(self, job_id: str, result: dict | None = None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE retry_jobs SET status='succeeded', result_json=?, last_error='', updated_at=? WHERE id=?",
                (json.dumps(result or {}, ensure_ascii=False), now_iso(), job_id),
            )

    def fail(self, job_id: str, error: str) -> str:
        with self.db.connect() as conn:
            row = conn.execute("SELECT attempts, max_attempts FROM retry_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return "missing"
            if int(row["attempts"]) >= int(row["max_attempts"]):
                conn.execute(
                    "UPDATE retry_jobs SET status='dead_letter', last_error=?, updated_at=? WHERE id=?",
                    (str(error)[:1000], now_iso(), job_id),
                )
                return "dead_letter"
            delay = min(300, 5 * (2 ** max(0, int(row["attempts"]) - 1)))
            next_at = (datetime.now().astimezone() + timedelta(seconds=delay)).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE retry_jobs SET status='pending', next_attempt_at=?, last_error=?, updated_at=? WHERE id=?",
                (next_at, str(error)[:1000], now_iso(), job_id),
            )
            return "pending"

    def metrics(self) -> dict:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS c FROM retry_jobs GROUP BY status").fetchall()
        return {row["status"]: int(row["c"]) for row in rows}


class RetryWorker:
    def __init__(self, db_provider, replay_factory) -> None:
        self.db_provider = db_provider
        self.replay_factory = replay_factory
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
            repo = None
            job = None
            try:
                db = self.db_provider()
                repo = RetryQueueRepository(db)
                job = repo.claim_due()
                if not job:
                    await asyncio.sleep(RETRY_POLL_INTERVAL_SECONDS)
                    continue
                if job["kind"] != "agentarts":
                    repo.fail(job["id"], f"暂不支持自动重放的作业类型：{job['kind']}")
                    continue
                replay = self.replay_factory()
                result = await replay(job)
                repo.complete(job["id"], result if isinstance(result, dict) else {"result": result})
            except Exception as exc:
                if repo is not None and job:
                    try:
                        repo.fail(job["id"], str(exc))
                    except Exception:
                        pass
                await asyncio.sleep(RETRY_POLL_INTERVAL_SECONDS)
