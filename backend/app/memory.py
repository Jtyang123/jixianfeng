from __future__ import annotations

import json
import math
import re
from datetime import datetime
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
    def _terms(text: str) -> set[str]:
        lowered = str(text or "").lower()
        terms = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,}", lowered))
        for block in re.findall(r"[\u4e00-\u9fff]{2,}", lowered):
            terms.update(block[index:index + 2] for index in range(max(0, len(block) - 1)))
        return terms

    @classmethod
    def _public(cls, row: dict) -> dict:
        item = dict(row)
        try:
            item["value"] = json.loads(item.pop("value_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            item["value"] = {}
        confidence = round(float(item.get("confidence") or 0.0), 4)
        try:
            updated = datetime.fromisoformat(str(item.get("updated_at") or ""))
            if updated.tzinfo is None:
                updated = updated.astimezone()
            age_days = max(0.0, (datetime.now().astimezone() - updated).total_seconds() / 86400)
        except (TypeError, ValueError):
            age_days = 3650.0
        recency = math.exp(-age_days / 30.0)
        item["confidence"] = round(confidence, 2)
        item["recency_score"] = round(recency, 4)
        item["importance"] = round(confidence * 0.7 + recency * 0.3, 4)
        item["age_days"] = round(age_days, 2)
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

    def summary(self, agent_ids: list[str] | None = None, query: str = "", per_agent_limit: int = 20) -> dict[str, dict[str, dict]]:
        rows = self.list_memories(limit=500)
        allowed = set(agent_ids or [])
        query_terms = self._terms(query)
        ranked: dict[str, list[tuple[float, dict]]] = {}
        for row in rows:
            if allowed and row["agent_id"] not in allowed:
                continue
            content_terms = self._terms(f"{row.get('memory_key', '')} {json.dumps(row.get('value'), ensure_ascii=False)}")
            relevance = len(query_terms & content_terms) / max(len(query_terms), 1) if query_terms else 0.0
            retrieval_score = round(row.get("importance", 0.0) * 0.7 + relevance * 0.3, 4)
            payload = {
                "value": row["value"],
                "confidence": row["confidence"],
                "importance": row.get("importance", 0.0),
                "recency_score": row.get("recency_score", 0.0),
                "relevance": round(relevance, 4),
                "retrieval_score": retrieval_score,
                "source_run_id": row["source_run_id"],
                "updated_at": row["updated_at"],
            }
            ranked.setdefault(row["agent_id"], []).append((retrieval_score, {row["memory_key"]: payload}))
        result: dict[str, dict[str, dict]] = {}
        for agent_id, items in ranked.items():
            items.sort(key=lambda item: item[0], reverse=True)
            merged: dict[str, dict] = {}
            for _, payload in items[:max(1, per_agent_limit)]:
                merged.update(payload)
            result[agent_id] = merged
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