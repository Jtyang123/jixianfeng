from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from app.agentarts import AgentArtsBridge
from app.config import PROJECT_DIR
from app.database import Database
from app.knowledge_base import KnowledgeRepository
from app.llm import LLMService


async def _llm_probe() -> dict:
    service = LLMService()
    started = time.perf_counter()
    result = await service.reflect(
        "delivery_agent",
        {"summary": "demo preflight", "verdict": "pass"},
        [],
    )
    return {
        "state": service.state,
        "enabled": service.enabled,
        "model": service.status.get("model"),
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "result_mode": result.get("mode"),
    }


def _db_probe() -> tuple[Database | None, dict]:
    try:
        db = Database()
        db.initialize()
        with db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return db, {"ok": True, "engine": db.engine, "path": str(db.path) if db.engine == "sqlite" else None}
    except Exception as exc:
        return None, {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:240]}"}


def _frontend_probe() -> dict:
    index = PROJECT_DIR / "frontend" / "dist" / "index.html"
    return {"ok": index.exists(), "path": str(index)}


async def main() -> int:
    db, database = _db_probe()
    frontend = _frontend_probe()
    blockers: list[str] = []
    if not database["ok"]:
        blockers.append("database unavailable")
    if not frontend["ok"]:
        blockers.append("frontend dist/index.html missing; run npm run build")

    llm = await _llm_probe()
    if llm["state"] != "ready":
        blockers.append(f"LLM state is {llm['state']}; planner demo falls back to deterministic mode")

    if db is not None:
        embedding = KnowledgeRepository(db).embedding_stats()
        agentarts = await AgentArtsBridge(db).ping(force=True)
    else:
        embedding = {"provider": "unavailable", "mode": "unavailable", "dimensions": 0, "stale_chunks": 0}
        agentarts = {"status": "unavailable", "online_rate": 0.0, "checks": []}

    checks = list(agentarts.get("checks") or [])
    latencies = [float(item.get("latency_ms") or 0) for item in checks]
    mean_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
    online_rate = float(agentarts.get("online_rate") or 0.0)
    if online_rate == 1.0 and len(checks) == 4 and mean_latency < 12000:
        recommended = "agentarts-full"
    elif online_rate == 1.0 and len(checks) == 4 and mean_latency >= 12000:
        recommended = "agentarts-mixed"
    else:
        recommended = "local-deterministic"

    report = {
        "llm": llm,
        "agentarts": {
            "status": agentarts.get("status"),
            "online_rate": online_rate,
            "latency_mean_ms": mean_latency,
            "cached": agentarts.get("cached", False),
            "checks": [
                {
                    "agent_id": item.get("agent_id"),
                    "ok": item.get("ok"),
                    "http_status": item.get("http_status"),
                    "latency_ms": item.get("latency_ms"),
                    "error_type": item.get("error_type"),
                }
                for item in checks
            ],
        },
        "embedding": {
            "provider": embedding.get("provider"),
            "mode": embedding.get("mode"),
            "dimensions": embedding.get("dimensions"),
            "semantic_retrieval": embedding.get("semantic_retrieval"),
            "total_chunks": embedding.get("total_chunks"),
            "stale_chunks": embedding.get("stale_chunks"),
        },
        "database": database,
        "frontend": frontend,
        "recommended_demo_mode": recommended,
        "blockers": blockers,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not blockers else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))