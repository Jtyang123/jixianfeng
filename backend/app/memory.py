from __future__ import annotations

import json
from typing import Any

from .database import Database, now_iso


class MemoryRepository:
    """Small, auditable cross-run memory store for agent decisions.

    Memory is a tie-breaker and context input only. It never overrides inventory,
    route, capacity, deadline or verification hard rules.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _public(row: dict) -> dict:
        item = dict(row)
        try:
            item["value"] = json.loads(item.pop("value_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            item["value"] = {}
        item["confidence"] = round(float(item.get("confidence") or 0.0), 2)
        return item

    def list_memories(self, agent_id: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM agent_memory"
        params: list[Any] = []
        if agent_id:
            sql += " WHERE agent_id = ?"
            params.append(agent_id)
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self.db.connect() as conn:
            return [self._public(dict(row)) for row in conn.execute(sql, tuple(params)).fetchall()]

    def summary(self, agent_ids: list[str] | None = None) -> dict[str, dict[str, dict]]:
        rows = self.list_memories(limit=500)
        allowed = set(agent_ids or [])
        result: dict[str, dict[str, dict]] = {}
        for row in rows:
            if allowed and row["agent_id"] not in allowed:
                continue
            result.setdefault(row["agent_id"], {})[row["memory_key"]] = {
                "value": row["value"],
                "confidence": row["confidence"],
                "source_run_id": row["source_run_id"],
                "updated_at": row["updated_at"],
            }
        return result

    def remember(
        self,
        agent_id: str,
        memory_key: str,
        value: dict | list | str | int | float | bool,
        confidence: float,
        source_run_id: str,
    ) -> dict:
        now = now_iso()
        confidence = round(max(0.0, min(1.0, float(confidence))), 2)
        payload = json.dumps(value, ensure_ascii=False)
        with self.db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM agent_memory WHERE agent_id = ? AND memory_key = ?",
                (agent_id, memory_key),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE agent_memory
                       SET value_json=?, confidence=?, source_run_id=?, updated_at=?
                       WHERE agent_id=? AND memory_key=?""",
                    (payload, confidence, source_run_id, now, agent_id, memory_key),
                )
            else:
                conn.execute(
                    """INSERT INTO agent_memory
                       (agent_id, memory_key, value_json, confidence, source_run_id, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (agent_id, memory_key, payload, confidence, source_run_id, now, now),
                )
            row = conn.execute(
                "SELECT * FROM agent_memory WHERE agent_id = ? AND memory_key = ?",
                (agent_id, memory_key),
            ).fetchone()
        return self._public(dict(row))

    def delete(self, agent_id: str, memory_key: str) -> bool:
        with self.db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM agent_memory WHERE agent_id = ? AND memory_key = ?",
                (agent_id, memory_key),
            ).fetchone()
            if not existing:
                return False
            conn.execute(
                "DELETE FROM agent_memory WHERE agent_id = ? AND memory_key = ?",
                (agent_id, memory_key),
            )
        return True