from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from app.agentarts import AgentArtsBridge
from app.config import PROJECT_DIR
from app.database import Database
from app.knowledge_base import KnowledgeRepository
from app.llm import LLMService


CORE_AGENT_IDS = ("demand_agent", "matching_agent", "verifier_agent", "delivery_agent")
PASSED_STATUSES = {"awaiting_approval", "approved", "completed", "passed"}


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
        "planner_available": service.state == "ready",
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


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _run_duration_ms(row: dict) -> float | None:
    created = _parse_time(row.get("created_at"))
    completed = _parse_time(row.get("completed_at"))
    if not created or not completed:
        return None
    return round((completed - created).total_seconds() * 1000, 2)


def _runtime_consistency(result: dict) -> dict:
    runtime = str(result.get("runtime") or "")
    trace = result.get("agentarts_trace") or {}
    trace = trace if isinstance(trace, dict) else {}
    core_cloud = [
        agent_id for agent_id in CORE_AGENT_IDS
        if isinstance(trace.get(agent_id), dict) and trace[agent_id].get("mode") == "agentarts"
    ]
    warning = ""
    if runtime == "llm-planner" and core_cloud:
        warning = "runtime=llm-planner 但核心四角色出现云端 trace，运行模式不自洽"
    elif runtime == "agentarts" and not trace:
        warning = "runtime=agentarts 但 agentarts_trace 为空，运行模式不自洽"
    elif runtime == "local" and trace:
        warning = "runtime=local 但 agentarts_trace 非空，运行模式不自洽"
    elif runtime not in {"llm-planner", "agentarts", "local"}:
        warning = f"未知 runtime：{runtime or '空'}"
    return {
        "consistent": not bool(warning),
        "warning": warning,
        "core_cloud_agents": core_cloud,
        "trace_count": len(trace),
    }


def _recent_run_summary(db: Database) -> dict:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, scenario_id, status, created_at, completed_at, result_json FROM runs ORDER BY rowid DESC LIMIT 20"
        ).fetchall()
    rows = [dict(row) for row in rows]
    warnings: list[str] = []
    scenario_stats: dict[str, dict] = {}
    needs_human = 0
    for row in rows:
        stats = scenario_stats.setdefault(
            str(row.get("scenario_id") or "unknown"),
            {"scenario_id": row.get("scenario_id"), "passed": 0, "needs_human": 0, "other": 0},
        )
        status = str(row.get("status") or "")
        if status == "needs_human":
            stats["needs_human"] += 1
            needs_human += 1
        elif status in PASSED_STATUSES:
            stats["passed"] += 1
        else:
            stats["other"] += 1
    ratio = round(needs_human / max(len(rows), 1), 4)
    if ratio > 0.30:
        warnings.append("warning: 近期人工研判比例偏高，建议演示 rainstorm-ii")
    latest = rows[0] if rows else None
    latest_run = None
    if latest:
        try:
            result = json.loads(latest.get("result_json") or "{}")
        except json.JSONDecodeError:
            result = {}
        consistency = _runtime_consistency(result)
        if consistency["warning"]:
            warnings.append(f"warning: {consistency['warning']}")
        latest_run = {
            "run_id": latest.get("id"),
            "scenario_id": latest.get("scenario_id"),
            "status": latest.get("status"),
            "runtime": result.get("runtime"),
            "total_ms": _run_duration_ms(latest),
            "agentarts_trace_count": consistency["trace_count"],
            "consistent": consistency["consistent"],
            "consistency_warning": consistency["warning"],
        }
    return {
        "sample_size": len(rows),
        "needs_human": needs_human,
        "needs_human_ratio": ratio,
        "scenario_stats": sorted(scenario_stats.values(), key=lambda item: item["scenario_id"] or ""),
        "latest_run": latest_run,
        "warnings": warnings,
    }


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
        recent_runs = _recent_run_summary(db)
    else:
        embedding = {"provider": "unavailable", "mode": "unavailable", "dimensions": 0, "stale_chunks": 0}
        agentarts = {"status": "unavailable", "online_rate": 0.0, "checks": []}
        recent_runs = {"sample_size": 0, "needs_human": 0, "needs_human_ratio": 0.0, "scenario_stats": [], "latest_run": None, "warnings": []}

    checks = list(agentarts.get("checks") or [])
    latencies = [float(item.get("latency_ms") or 0) for item in checks]
    mean_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
    online_rate = float(agentarts.get("online_rate") or 0.0)
    agentarts_full = online_rate == 1.0 and len(checks) == 4
    if llm.get("planner_available"):
        recommended = "llm-planner"
    elif agentarts_full:
        recommended = "agentarts-full"
    else:
        recommended = "local"
    if agentarts_full and mean_latency < 8000:
        evidence_mode = "agentarts-full"
        evidence_note = "AgentArts 四端点在线且均值小于 8 秒，可作为云端证据片段"
    elif agentarts_full:
        evidence_mode = "agentarts-slow"
        evidence_note = "AgentArts 四端点在线但均值不低于 8 秒，仅建议截取证据，不承担主链路"
    else:
        evidence_mode = "local-only"
        evidence_note = "AgentArts 未完整在线，主演示与证据均使用本地确定性链路"

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
        "recent_runs": recent_runs,
        "recommended_demo_mode": recommended,
        "evidence_mode": evidence_mode,
        "evidence_note": evidence_note,
        "blockers": blockers,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not blockers else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))