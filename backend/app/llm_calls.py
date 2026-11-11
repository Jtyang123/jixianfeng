from __future__ import annotations

import hashlib
import json
from urllib.parse import urlparse

from .database import Database


def llm_configuration_state(enabled: bool, base_url: str, api_key: str, model: str) -> dict:
    missing = []
    if not base_url:
        missing.append("LLM_BASE_URL")
    if not model:
        missing.append("LLM_MODEL")
    if not api_key or api_key in {"sk-xxx", "your-key"}:
        missing.append("LLM_API_KEY")
    if not enabled:
        state = "disabled"
    elif missing:
        state = "misconfigured"
    else:
        state = "ready"
    return {"state": state, "missing": missing}


class LLMCallRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _summary(value, limit: int = 200) -> str:
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=False)
            except Exception:
                text = str(value)
        return text.replace("\n", " ")[:limit]

    def record(
        self,
        *,
        run_id: str,
        stage: str,
        model: str,
        provider: str,
        base_url: str,
        input_value,
        input_summary,
        output_value=None,
        latency_ms: float = 0.0,
        status: str,
        http_status: int = 0,
        error: str = "",
        fallback: bool = False,
    ) -> dict:
        digest_source = input_value if isinstance(input_value, str) else json.dumps(input_value, ensure_ascii=False, sort_keys=True)
        input_digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]
        host = ""
        try:
            host = urlparse(str(base_url or "")).hostname or ""
        except Exception:
            host = ""
        item = {
            "run_id": str(run_id or "")[:120],
            "stage": str(stage or "")[:32],
            "model": str(model or "")[:160],
            "provider": str(provider or "")[:120],
            "base_url_host": host[:200],
            "input_digest": input_digest,
            "input_summary": self._summary(input_summary),
            "output_summary": self._summary(output_value),
            "latency_ms": round(float(latency_ms), 2),
            "status": str(status or "")[:40],
            "http_status": int(http_status or 0),
            "error": str(error or "")[:500],
            "fallback": bool(fallback),
        }
        with self.db.connect() as conn:
            from .database import now_iso
            conn.execute(
                """INSERT INTO llm_calls
                   (run_id, stage, model, provider, base_url_host, input_digest, input_summary,
                    output_summary, latency_ms, status, http_status, error, fallback, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item["run_id"], item["stage"], item["model"], item["provider"], item["base_url_host"],
                    item["input_digest"], item["input_summary"], item["output_summary"], item["latency_ms"],
                    item["status"], item["http_status"], item["error"], 1 if item["fallback"] else 0, now_iso(),
                ),
            )
        return item

    def list_calls(self, run_id: str = "", limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM llm_calls"
        params = []
        if run_id:
            sql += " WHERE run_id = ?"
            params.append(run_id)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
        with self.db.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        result=[]
        for row in rows:
            item=dict(row)
            item["fallback"]=bool(item.get("fallback"))
            result.append(item)
        return result
