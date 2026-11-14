from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402
from app.agentarts import AgentArtsBridge  # noqa: E402


async def probe_agent(client: httpx.AsyncClient, bridge: AgentArtsBridge, agent_id: str, endpoint: str) -> dict:
    started = time.perf_counter()
    result = {
        "agent_id": agent_id,
        "http_status": 0,
        "latency_ms": 0.0,
        "sse_parsed": False,
        "ok": False,
        "error_type": "",
    }
    try:
        response = await client.post(
            endpoint,
            headers=bridge._headers("agentarts-probe", agent_id),
            json={"query": "你好"},
            timeout=30.0,
        )
        result["http_status"] = response.status_code
        result["ok"] = response.status_code < 400
        content_type = response.headers.get("content-type", "")
        body = response.text or ""
        if "text/event-stream" in content_type and "data:" in body:
            result["sse_parsed"] = True
        elif body.lstrip().startswith("{"):
            try:
                json.loads(body)
                result["sse_parsed"] = True
            except json.JSONDecodeError:
                result["sse_parsed"] = False
    except Exception as exc:
        result["error_type"] = type(exc).__name__
    finally:
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result


async def main() -> int:
    bridge = AgentArtsBridge()
    targets = [(agent_id, endpoint) for agent_id, endpoint in bridge.endpoints.items()]
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(probe_agent(client, bridge, agent_id, endpoint) for agent_id, endpoint in targets))
    online = sum(1 for item in results if item["ok"] and item["sse_parsed"])
    payload = {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "coverage": f"{online}/{len(results)}",
        "results": results,
    }
    target = ROOT / "docs" / f"agentarts_probe_{datetime.now():%Y%m%d}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if online == len(results) and len(results) == 4 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))