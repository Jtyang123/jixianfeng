import asyncio
import json
import time
from pathlib import Path

from app import orchestrator as orchestrator_module
from app.database import Database
from app.registry import AgentRegistryService


class FakeAgentArts:
    enabled = True

    def __init__(self):
        self.started_at = {}

    @property
    def status(self):
        return {"enabled": True, "agent_coverage": "4/4"}

    def override_endpoint(self, agent_id, endpoint):
        return None

    def endpoint_for(self, agent_id):
        return f"https://example.com/{agent_id}/invocations"

    async def invoke(self, agent_id, payload, session_id="farmmatch", client=None):
        self.started_at[agent_id] = time.monotonic()
        await asyncio.sleep(0.15)
        if agent_id == "matching_agent":
            text = json.dumps({"recommended_plan_id": payload["plans"][0]["plan_id"]})
        elif agent_id == "verifier_agent":
            text = json.dumps({"verdict": "pass", "score": 100})
        elif agent_id == "risk_extension":
            text = json.dumps({"risks": ["扩展风险：需核对夜间运输"]})
        else:
            text = json.dumps({"summary": "ok"})
        return {"mode": "agentarts", "agent_id": agent_id, "text": text, "stream_events": 1}


class FakeLLM:
    @property
    def status(self):
        return {"enabled": False}

    async def reflect(self, agent_id, payload, evidence):
        return {"agent_id": agent_id, "source": "test"}


def test_matching_and_verifier_cloud_reviews_run_in_parallel(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "parallel-orchestrator.db", database_url="")
    db.initialize()
    scenario = db.get_scenario("rainstorm-ii")
    materials = db.list_materials()
    by_code = {item["code"]: item for item in materials}
    demand_items = [
        {
            "material_id": by_code[item["material_code"]]["id"],
            "quantity": item["quantity"],
            "priority": item["priority"],
            "deadline_hours": item["deadline_hours"],
        }
        for item in scenario["default_demand"]
    ]
    request = {
        "scenario_id": scenario["id"],
        "event": {
            "type": scenario["event_type"],
            "severity": scenario["severity"],
            "location": scenario["location"],
            "affected_population": scenario["affected_population"],
            "closed_route_ids": scenario["closed_routes"],
        },
        "demand_items": demand_items,
        "weights": {"speed": 0.38, "cost": 0.27, "risk": 0.35},
    }
    run_id = "RUN-PARALLEL-TEST"
    AgentRegistryService(db).register_agent({
        "id": "risk_extension",
        "name": "扩展风险审查",
        "role": "risk-review",
        "description": "动态注册的扩展审查节点",
        "capabilities": ["night-transport-risk"],
        "version": "v1",
        "endpoint": "https://example.com/risk-extension/invocations",
        "model": "test",
        "prompt_version": "v1",
    })
    db.create_run(run_id, scenario["id"], request)
    fake = FakeAgentArts()
    monkeypatch.setattr(orchestrator_module, "AgentArtsBridge", lambda: fake)
    monkeypatch.setattr(orchestrator_module, "LLMService", FakeLLM)

    asyncio.run(orchestrator_module.orchestrate(db, run_id, request))

    run = db.get_run(run_id)
    assert run["status"] == "awaiting_approval"
    assert set(run["result"]["agentarts_trace"]) == {
        "demand_agent", "matching_agent", "verifier_agent", "delivery_agent", "risk_extension",
    }
    assert abs(fake.started_at["matching_agent"] - fake.started_at["verifier_agent"]) < 0.2
    assert run["result"]["extension_reviews"][0]["agent_id"] == "risk_extension"
    assert any("扩展风险" in item for item in run["result"]["delivery"]["risk_alerts"])
