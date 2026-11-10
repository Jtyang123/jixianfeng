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

    @staticmethod
    def _repair_experience_key(scenario_tag: str, issue_code: str, policy_id: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.:-]+", "-", f"{scenario_tag}:{issue_code}:{policy_id}").strip("-")
        return f"repair_experience:{safe}"[:240]

    def record_repair_experience(
        self,
        issue_codes: list[str],
        scenario_tag: str,
        policy_id: str,
        strategy: str,
        prompt_fragment: str,
        success: bool,
        source_run_id: str,
        agent_id: str = "matching_agent",
    ) -> list[dict]:
        """Persist successful/failed repair outcomes for future prompt context only."""
        policy_id = str(policy_id or "").strip()
        codes = sorted({str(code).strip() for code in issue_codes if str(code).strip()})
        if not codes or not policy_id:
            return []
        scenario = str(scenario_tag or "generic").strip() or "generic"
        results: list[dict] = []
        with self.db.connect() as conn:
            for code in codes:
                memory_key = self._repair_experience_key(scenario, code, policy_id)
                row = conn.execute(
                    "SELECT * FROM agent_memory WHERE agent_id = ? AND memory_key = ?",
                    (agent_id, memory_key),
                ).fetchone()
                try:
                    previous = json.loads(row["value_json"] or "{}") if row else {}
                except (TypeError, json.JSONDecodeError):
                    previous = {}
                adoptions = max(0, int(previous.get("adoptions") or 0)) + 1
                successes = max(0, int(previous.get("successes") or 0)) + (1 if success else 0)
                rate = round(successes / adoptions, 4)
                value = {
                    "issue_code": code,
                    "scenario_tag": scenario,
                    "policy_id": policy_id,
                    "strategy": str(strategy or "")[:500],
                    "prompt_fragment": str(prompt_fragment or "")[:1000],
                    "adoptions": adoptions,
                    "successes": successes,
                    "success_rate": rate,
                    "last_outcome": "success" if success else "failure",
                }
                now = now_iso()
                payload = json.dumps(value, ensure_ascii=False)
                confidence = round(max(0.0, min(1.0, max(0.5, rate))), 2)
                if row:
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
                saved = conn.execute(
                    "SELECT * FROM agent_memory WHERE agent_id = ? AND memory_key = ?",
                    (agent_id, memory_key),
                ).fetchone()
                results.append(self._public(dict(saved)))
        return results

    def find_repair_experiences(
        self,
        issue_codes: list[str],
        scenario_tag: str,
        min_success_rate: float = 0.6,
        limit: int = 3,
    ) -> list[dict]:
        """Return high-confidence historical repair strategies for prompt injection."""
        wanted = {str(code).strip() for code in issue_codes if str(code).strip()}
        if not wanted:
            return []
        scenario = str(scenario_tag or "generic").strip() or "generic"
        matches: list[dict] = []
        for item in self.list_memories("matching_agent", limit=500):
            if not str(item.get("memory_key") or "").startswith("repair_experience:"):
                continue
            value = item.get("value") if isinstance(item.get("value"), dict) else {}
            if str(value.get("issue_code") or "") not in wanted:
                continue
            if str(value.get("scenario_tag") or "") not in {scenario, "generic"}:
                continue
            rate = float(value.get("success_rate") or 0.0)
            if rate < min_success_rate:
                continue
            matches.append({
                **value,
                "memory_key": item.get("memory_key", ""),
                "confidence": item.get("confidence", 0.0),
                "recency_score": item.get("recency_score", 0.0),
            })
        matches.sort(
            key=lambda item: (
                float(item.get("success_rate") or 0.0),
                int(item.get("adoptions") or 0),
                float(item.get("recency_score") or 0.0),
            ),
            reverse=True,
        )
        return matches[:max(1, min(int(limit), 10))]

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