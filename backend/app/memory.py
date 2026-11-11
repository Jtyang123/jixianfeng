from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from .config import (
    MEMORY_CONTEXT_BUDGET_CHARS,
    MEMORY_CONTEXT_BUDGET_ITEMS,
    MEMORY_DECAY_DAYS,
    MEMORY_ENTRY_SUMMARY_CHARS,
    MEMORY_MIN_CONFIDENCE,
    MEMORY_RETENTION_DAYS,
)
from .database import Database, now_iso
from .knowledge_base import get_embedding_service


logger = logging.getLogger(__name__)
_MEMORY_BACKFILL_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="memory-embedding-backfill")
_MEMORY_BACKFILL_LOCK = threading.Lock()
_MEMORY_BACKFILL_PENDING: set[int] = set()


class MemoryRepository:
    """Small, auditable cross-run memory store for agent decisions.

    Memory is a tie-breaker and context input only. It never overrides inventory,
    route, capacity, deadline or verification hard rules.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self.embedding = get_embedding_service()

    def _semantic_active(self) -> bool:
        return bool(getattr(self.embedding, "local_model", None) or getattr(self.embedding, "remote", False))

    @staticmethod
    def _memory_class(memory_key: str) -> str:
        return "repair_experience" if str(memory_key or "").startswith("repair_experience:") else "fact"

    @staticmethod
    def _embedding_text(memory_key: str, value: dict | list | str | int | float | bool) -> str:
        scenario = ""
        if isinstance(value, dict):
            scenario = str(value.get("scenario_tag") or value.get("scenario_id") or "")
        return f"{memory_key}\n{json.dumps(value, ensure_ascii=False)}\n{scenario}"

    def _embed_for(self, memory_key: str, value: Any) -> tuple[list[float], dict]:
        if not self._semantic_active():
            return [], {"provider": "", "model": "", "dimensions": 0}
        try:
            return self.embedding.embed(self._embedding_text(memory_key, value))
        except Exception:
            logger.warning("Memory embedding failed; keyword fallback remains available", exc_info=True)
            return [], {"provider": "", "model": "", "dimensions": 0}

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
        try:
            item["embedding"] = json.loads(item.pop("embedding_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            item["embedding"] = []
        confidence = round(float(item.get("confidence") or 0.0), 4)
        now = datetime.now().astimezone()
        try:
            updated = datetime.fromisoformat(str(item.get("updated_at") or ""))
            if updated.tzinfo is None:
                updated = updated.astimezone()
            age_days = max(0.0, (now - updated).total_seconds() / 86400)
        except (TypeError, ValueError):
            age_days = 3650.0
        try:
            last_hit = datetime.fromisoformat(str(item.get("last_accessed_at") or item.get("updated_at") or ""))
            if last_hit.tzinfo is None:
                last_hit = last_hit.astimezone()
            last_hit_days = max(0.0, (now - last_hit).total_seconds() / 86400)
        except (TypeError, ValueError):
            last_hit_days = 3650.0
        recency = math.exp(-age_days / MEMORY_DECAY_DAYS)
        streak = max(0, int(item.get("low_confidence_streak") or 0))
        miss_penalty = min(3, int(last_hit_days / MEMORY_DECAY_DAYS)) * 0.02
        importance = confidence * 0.7 + recency * 0.3 - min(streak, 3) * 0.08 - miss_penalty
        item["confidence"] = round(confidence, 2)
        item["recency_score"] = round(recency, 4)
        item["importance"] = round(max(0.0, importance), 4)
        item["age_days"] = round(age_days, 2)
        item["last_hit_days"] = round(last_hit_days, 2)
        item["memory_class"] = cls._memory_class(item.get("memory_key", ""))
        item["embedding_dim"] = int(item.get("embedding_dim") or 0)
        item["hit_count"] = int(item.get("hit_count") or 0)
        item["low_confidence_streak"] = streak
        return item

    def list_memories(self, agent_id: str | None = None, limit: int = 100, cleanup_expired: bool = False) -> list[dict]:
        if cleanup_expired:
            self.cleanup_expired()
        sql = "SELECT * FROM agent_memory"
        params: list[Any] = []
        if agent_id:
            sql += " WHERE agent_id = ?"
            params.append(agent_id)
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self.db.connect() as conn:
            return [self._public(dict(row)) for row in conn.execute(sql, tuple(params)).fetchall()]

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        numerator = sum(float(a) * float(b) for a, b in zip(left, right))
        left_norm = math.sqrt(sum(float(a) * float(a) for a in left))
        right_norm = math.sqrt(sum(float(b) * float(b) for b in right))
        if left_norm <= 0 or right_norm <= 0:
            return 0.0
        return max(0.0, min(1.0, numerator / (left_norm * right_norm)))

    def _query_embedding(self, query: str) -> tuple[list[float], dict, str]:
        if not query:
            return [], {"provider": "", "dimensions": 0}, "empty-query"
        if not self._semantic_active():
            return [], {"provider": "", "dimensions": 0}, "keyword-fallback"
        try:
            vector, meta = self.embedding.embed(query)
            mode = "keyword-fallback" if meta.get("provider") == "hash" else "hybrid-vector-keyword"
            return vector, meta, mode
        except Exception:
            logger.warning("Memory query embedding failed; keyword fallback remains available", exc_info=True)
            return [], {"provider": "", "dimensions": 0}, "keyword-fallback"

    def _rank_rows(self, rows: list[dict], query: str) -> tuple[list[dict], str]:
        query_terms = self._terms(query)
        query_vector, query_meta, retrieval_mode = self._query_embedding(query)
        ranked: list[dict] = []
        stale_ids: list[int] = []
        for row in rows:
            item = dict(row)
            if "value" not in item:
                item = self._public(item)
            content_text = f"{item.get('memory_key', '')} {json.dumps(item.get('value'), ensure_ascii=False)}"
            content_terms = self._terms(content_text)
            keyword_relevance = len(query_terms & content_terms) / max(len(query_terms), 1) if query_terms else 0.0
            embedding = item.get("embedding") or []
            stale = bool(self._semantic_active() and (
                not embedding
                or item.get("embedding_provider") != query_meta.get("provider")
                or int(item.get("embedding_dim") or 0) != int(query_meta.get("dimensions") or 0)
            ))
            if stale and item.get("id") is not None:
                stale_ids.append(int(item["id"]))
            vector_similarity = 0.0 if stale else self._cosine(query_vector, embedding)
            if retrieval_mode == "hybrid-vector-keyword" and not stale and embedding:
                retrieval_score = vector_similarity * 0.6 + keyword_relevance * 0.4
            elif not query:
                retrieval_score = float(item.get("importance") or 0.0)
            else:
                retrieval_score = float(item.get("importance") or 0.0) * 0.7 + keyword_relevance * 0.3
            value = item.get("value")
            ranked.append({
                **item,
                "keyword_relevance": round(keyword_relevance, 4),
                "vector_similarity": round(vector_similarity, 4),
                "retrieval_score": round(retrieval_score, 4),
                "context_priority": round(float(item.get("importance") or 0.0) * 0.6 + retrieval_score * 0.4, 4),
                "retrieval_mode": "keyword-fallback" if stale or retrieval_mode == "keyword-fallback" else retrieval_mode,
                "embedding_stale": stale,
                "content_chars": len(json.dumps(value, ensure_ascii=False)),
            })
        self._schedule_embedding_backfill(stale_ids)
        return ranked, retrieval_mode

    @staticmethod
    def _experience_sort_key(item: dict) -> tuple:
        value = item.get("value") if isinstance(item.get("value"), dict) else {}
        return (
            float(value.get("success_rate") or 0.0),
            int(value.get("adoptions") or 0),
            float(value.get("score_gain", value.get("effect", 0.0)) or 0.0),
            float(item.get("retrieval_score") or 0.0),
            float(item.get("importance") or 0.0),
        )

    @staticmethod
    def _fact_sort_key(item: dict) -> tuple:
        return (
            float(item.get("context_priority") or 0.0),
            float(item.get("importance") or 0.0),
            float(item.get("retrieval_score") or 0.0),
            float(item.get("recency_score") or 0.0),
            int(item.get("hit_count") or 0),
        )

    @staticmethod
    def _rule_summary(value: Any) -> str:
        if isinstance(value, dict):
            preferred = [
                "summary", "policy", "policy_id", "issue_code", "scenario_tag", "route_id",
                "material_id", "quantity", "plan_id", "name", "score", "blockers",
            ]
            parts = []
            for key in preferred:
                if key in value and value[key] not in (None, "", [], {}):
                    raw = value[key]
                    rendered = json.dumps(raw, ensure_ascii=False) if isinstance(raw, (dict, list)) else str(raw)
                    parts.append(f"{key}={rendered[:80]}")
                if len(parts) >= 4:
                    break
            if parts:
                return "规则摘要：" + "；".join(parts)
            return f"规则摘要：对象字段 {', '.join(list(value)[:6])}"
        if isinstance(value, list):
            first = value[0] if value else ""
            return f"规则摘要：列表共 {len(value)} 项，首项 {str(first)[:100]}"
        return f"规则摘要：{str(value or '')[:160]}"

    def _touch_memories(self, items: list[dict]) -> None:
        if not items:
            return
        now = now_iso()
        with self.db.connect() as conn:
            for item in items:
                conn.execute(
                    """UPDATE agent_memory
                       SET last_accessed_at=?,
                           hit_count=COALESCE(hit_count, 0) + 1,
                           low_confidence_streak=CASE WHEN confidence < ? THEN COALESCE(low_confidence_streak, 0) + 1 ELSE 0 END
                       WHERE id=?""",
                    (now, MEMORY_MIN_CONFIDENCE, int(item["id"])),
                )

    def summary(self, agent_ids: list[str] | None = None, query: str = "", per_agent_limit: int = 20) -> dict[str, dict[str, dict]]:
        rows = self.list_memories(limit=500)
        allowed = set(agent_ids or [])
        ranked, retrieval_mode = self._rank_rows(rows, query)
        visible = [item for item in ranked if not allowed or item["agent_id"] in allowed]
        experiences = [item for item in visible if item.get("memory_class") == "repair_experience"]
        facts = [item for item in visible if item.get("memory_class") != "repair_experience"]
        experiences.sort(key=self._experience_sort_key, reverse=True)
        facts.sort(key=self._fact_sort_key, reverse=True)
        candidates = [(item, "experience") for item in experiences] + [(item, "fact") for item in facts]

        selected: list[dict] = []
        result: dict[str, dict[str, dict]] = {}
        omitted_by_agent: dict[str, int] = {}
        per_agent_counts: dict[str, int] = {}
        total_chars = 0
        for item, _kind in candidates:
            agent_id = str(item.get("agent_id") or "")
            if per_agent_counts.get(agent_id, 0) >= max(1, int(per_agent_limit)):
                omitted_by_agent[agent_id] = omitted_by_agent.get(agent_id, 0) + 1
                continue
            if len(selected) >= MEMORY_CONTEXT_BUDGET_ITEMS:
                omitted_by_agent[agent_id] = omitted_by_agent.get(agent_id, 0) + 1
                continue
            value = item.get("value")
            rendered_size = len(json.dumps(value, ensure_ascii=False))
            truncated = rendered_size > MEMORY_ENTRY_SUMMARY_CHARS
            payload_value = {"summary": self._rule_summary(value), "truncated": True, "original_chars": rendered_size} if truncated else value
            payload_size = len(json.dumps(payload_value, ensure_ascii=False))
            if total_chars + payload_size > MEMORY_CONTEXT_BUDGET_CHARS:
                omitted_by_agent[agent_id] = omitted_by_agent.get(agent_id, 0) + 1
                continue
            payload = {
                "value": payload_value,
                "confidence": item.get("confidence", 0.0),
                "importance": item.get("importance", 0.0),
                "recency_score": item.get("recency_score", 0.0),
                "relevance": item.get("keyword_relevance", 0.0),
                "keyword_relevance": item.get("keyword_relevance", 0.0),
                "vector_similarity": item.get("vector_similarity", 0.0),
                "retrieval_score": item.get("retrieval_score", 0.0),
                "context_priority": item.get("context_priority", 0.0),
                "retrieval_mode": item.get("retrieval_mode", retrieval_mode),
                "memory_class": item.get("memory_class", "fact"),
                "embedding_provider": item.get("embedding_provider", ""),
                "truncated": truncated,
                "source_run_id": item.get("source_run_id", ""),
                "updated_at": item.get("updated_at"),
            }
            result.setdefault(agent_id, {})[item["memory_key"]] = payload
            selected.append(item)
            per_agent_counts[agent_id] = per_agent_counts.get(agent_id, 0) + 1
            total_chars += payload_size
        for agent_id, omitted in omitted_by_agent.items():
            result.setdefault(agent_id, {})["__budget_summary__"] = {
                "value": {"summary": f"另有 {omitted} 条记忆因上下文预算被截断", "omitted": omitted},
                "memory_class": "budget_notice",
                "retrieval_mode": retrieval_mode,
            }
        self._touch_memories(selected)
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
        vector, meta = self._embed_for(memory_key, value)
        embedding_json = json.dumps(vector)
        with self.db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM agent_memory WHERE agent_id = ? AND memory_key = ?",
                (agent_id, memory_key),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE agent_memory
                       SET value_json=?, embedding_json=?, embedding_provider=?, embedding_model=?, embedding_dim=?,
                           confidence=?, source_run_id=?, updated_at=?
                       WHERE agent_id=? AND memory_key=?""",
                    (payload, embedding_json, meta.get("provider", ""), meta.get("model", ""), int(meta.get("dimensions") or 0),
                     confidence, source_run_id, now, agent_id, memory_key),
                )
            else:
                conn.execute(
                    """INSERT INTO agent_memory
                       (agent_id, memory_key, value_json, embedding_json, embedding_provider, embedding_model,
                        embedding_dim, confidence, source_run_id, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (agent_id, memory_key, payload, embedding_json, meta.get("provider", ""), meta.get("model", ""),
                     int(meta.get("dimensions") or 0), confidence, source_run_id, now, now),
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
        effect: float = 0.0,
        superseded: bool = False,
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
                previous_adoptions = max(0, int(previous.get("adoptions") or 0))
                adoptions = previous_adoptions + 1
                successes = max(0, int(previous.get("successes") or 0)) + (1 if success else 0)
                rate = round(successes / adoptions, 4)
                previous_gain = float(previous.get("score_gain", previous.get("effect", 0.0)) or 0.0)
                aggregate_gain = round((previous_gain * previous_adoptions + float(effect)) / adoptions, 2)
                value = {
                    "issue_code": code,
                    "scenario_tag": scenario,
                    "policy_id": policy_id,
                    "strategy": str(strategy or "")[:500],
                    "prompt_fragment": str(prompt_fragment or "")[:1000],
                    "adoptions": adoptions,
                    "successes": successes,
                    "success_rate": rate,
                    "effect": round(float(effect), 2),
                    "score_gain": aggregate_gain,
                    "superseded": bool(superseded),
                    "last_outcome": "success" if success else "failure",
                }
                now = now_iso()
                payload = json.dumps(value, ensure_ascii=False)
                confidence = round(max(0.0, min(1.0, max(0.5, rate))), 2)
                vector, meta = self._embed_for(memory_key, value)
                embedding_json = json.dumps(vector)
                if row:
                    conn.execute(
                        """UPDATE agent_memory
                           SET value_json=?, embedding_json=?, embedding_provider=?, embedding_model=?, embedding_dim=?,
                               confidence=?, source_run_id=?, updated_at=?
                           WHERE agent_id=? AND memory_key=?""",
                        (payload, embedding_json, meta.get("provider", ""), meta.get("model", ""), int(meta.get("dimensions") or 0),
                         confidence, source_run_id, now, agent_id, memory_key),
                    )
                else:
                    conn.execute(
                        """INSERT INTO agent_memory
                           (agent_id, memory_key, value_json, embedding_json, embedding_provider, embedding_model,
                            embedding_dim, confidence, source_run_id, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (agent_id, memory_key, payload, embedding_json, meta.get("provider", ""), meta.get("model", ""),
                         int(meta.get("dimensions") or 0), confidence, source_run_id, now, now),
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
        wanted = {str(code).strip() for code in issue_codes if str(code).strip()}
        if not wanted:
            return []
        scenario = str(scenario_tag or "generic").strip() or "generic"
        query = f"{' '.join(sorted(wanted))} {scenario} 修复策略 经验"
        rows, _mode = self._rank_rows(self.list_memories("matching_agent", limit=500), query)
        matches: list[dict] = []
        for item in rows:
            if item.get("memory_class") != "repair_experience":
                continue
            value = item.get("value") if isinstance(item.get("value"), dict) else {}
            if str(value.get("issue_code") or "") not in wanted:
                continue
            if str(value.get("scenario_tag") or "") not in {scenario, "generic"}:
                continue
            rate = float(value.get("success_rate") or 0.0)
            if rate < min_success_rate:
                continue
            experience_score = (
                rate * 0.3
                + min(int(value.get("adoptions") or 0) / 10.0, 1.0) * 0.2
                + min(max(float(value.get("score_gain", value.get("effect", 0.0)) or 0.0), 0.0) / 30.0, 1.0) * 0.1
                + float(item.get("retrieval_score") or 0.0) * 0.4
            )
            matches.append({
                **value,
                "id": item.get("id"),
                "agent_id": item.get("agent_id", "matching_agent"),
                "memory_key": item.get("memory_key", ""),
                "confidence": item.get("confidence", 0.0),
                "recency_score": item.get("recency_score", 0.0),
                "importance": item.get("importance", 0.0),
                "keyword_relevance": item.get("keyword_relevance", 0.0),
                "vector_similarity": item.get("vector_similarity", 0.0),
                "retrieval_score": item.get("retrieval_score", 0.0),
                "experience_score": round(experience_score, 4),
                "memory_class": "repair_experience",
            })
        matches.sort(
            key=lambda item: (
                float(item.get("success_rate") or 0.0),
                int(item.get("adoptions") or 0),
                float(item.get("score_gain", item.get("effect", 0.0)) or 0.0),
                float(item.get("experience_score") or 0.0),
            ),
            reverse=True,
        )
        selected = matches[:max(1, min(int(limit), 10))]
        self._touch_memories(selected)
        return selected

    def _schedule_embedding_backfill(self, memory_ids: list[int]) -> None:
        if not memory_ids or not self._semantic_active():
            return
        with _MEMORY_BACKFILL_LOCK:
            new_ids = [memory_id for memory_id in dict.fromkeys(memory_ids) if memory_id not in _MEMORY_BACKFILL_PENDING]
            _MEMORY_BACKFILL_PENDING.update(new_ids)
        if new_ids:
            _MEMORY_BACKFILL_EXECUTOR.submit(self._backfill_embeddings, new_ids)

    def _backfill_embeddings(self, memory_ids: list[int]) -> None:
        try:
            placeholders = ",".join("?" for _ in memory_ids)
            with self.db.connect() as conn:
                rows = conn.execute(
                    f"SELECT * FROM agent_memory WHERE id IN ({placeholders}) ORDER BY id",
                    tuple(memory_ids),
                ).fetchall()
            items = [dict(row) for row in rows]
            texts = []
            for item in items:
                try:
                    value = json.loads(item.get("value_json") or "{}")
                except (TypeError, json.JSONDecodeError):
                    value = {}
                texts.append(self._embedding_text(str(item.get("memory_key") or ""), value))
            embedded = self.embedding.embed_many(texts)
            updates = []
            for item, (vector, meta) in zip(items, embedded):
                updates.append((
                    json.dumps(vector), meta.get("provider", ""), meta.get("model", ""),
                    int(meta.get("dimensions") or 0), item["id"],
                ))
            if updates:
                with self.db.connect() as conn:
                    conn.executemany(
                        "UPDATE agent_memory SET embedding_json=?, embedding_provider=?, embedding_model=?, embedding_dim=? WHERE id=?",
                        updates,
                    )
        except Exception:
            logger.exception("Memory embedding backfill failed for %s records", len(memory_ids))
        finally:
            with _MEMORY_BACKFILL_LOCK:
                _MEMORY_BACKFILL_PENDING.difference_update(memory_ids)

    def wait_for_embedding_backfill(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with _MEMORY_BACKFILL_LOCK:
                if not _MEMORY_BACKFILL_PENDING:
                    return True
            time.sleep(0.01)
        return False

    def cleanup_expired(self) -> dict:
        cutoff = (datetime.now().astimezone() - timedelta(days=MEMORY_RETENTION_DAYS)).isoformat()
        deleted: list[dict] = []
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM agent_memory").fetchall()
            for raw in rows:
                row = dict(raw)
                try:
                    value = json.loads(row.get("value_json") or "{}")
                except (TypeError, json.JSONDecodeError):
                    value = {}
                reason = ""
                if float(row.get("confidence") or 0.0) < MEMORY_MIN_CONFIDENCE and str(row.get("updated_at") or "") < cutoff:
                    reason = "low-confidence-retention-expired"
                elif (
                    str(row.get("memory_key") or "").startswith("repair_experience:")
                    and isinstance(value, dict) and value.get("superseded")
                ):
                    reason = "superseded-repair-experience"
                if not reason:
                    continue
                conn.execute(
                    """INSERT INTO memory_cleanup_audit
                       (agent_id, memory_key, reason, value_json, deleted_at) VALUES (?, ?, ?, ?, ?)""",
                    (row["agent_id"], row["memory_key"], reason, row.get("value_json") or "{}", now_iso()),
                )
                conn.execute("DELETE FROM agent_memory WHERE id = ?", (row["id"],))
                deleted.append({"agent_id": row["agent_id"], "memory_key": row["memory_key"], "reason": reason})
        return {"deleted": len(deleted), "items": deleted, "retention_days": MEMORY_RETENTION_DAYS, "min_confidence": MEMORY_MIN_CONFIDENCE}

    def list_cleanup_audit(self, limit: int = 100) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_cleanup_audit ORDER BY deleted_at DESC, id DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        result = []
        for raw in rows:
            item = dict(raw)
            try:
                item["value"] = json.loads(item.pop("value_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                item["value"] = {}
            result.append(item)
        return result

    def delete(self, agent_id: str, memory_key: str) -> bool:
        with self.db.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM agent_memory WHERE agent_id = ? AND memory_key = ?",
                (agent_id, memory_key),
            ).fetchone()
            if not existing:
                return False
            row = dict(existing)
            conn.execute(
                """INSERT INTO memory_cleanup_audit
                   (agent_id, memory_key, reason, value_json, deleted_at) VALUES (?, ?, ?, ?, ?)""",
                (agent_id, memory_key, "manual-delete", row.get("value_json") or "{}", now_iso()),
            )
            conn.execute(
                "DELETE FROM agent_memory WHERE agent_id = ? AND memory_key = ?",
                (agent_id, memory_key),
            )
        return True
