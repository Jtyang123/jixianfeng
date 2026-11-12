import asyncio
import json
import time
from pathlib import Path

import httpx

from app import llm_planner as llm_planner_module
from app import orchestrator as orchestrator_module
from app.database import Database
from app.registry import AgentRegistryService
from app.scheduler import AGENTS
from app.workflow_engine import WorkflowRepository


class FakeAgentArts:
    enabled = True
    base_url = ""

    def __init__(self, endpoints=None):
        self.endpoints = endpoints or {
            "demand_agent": "https://example.com/demand/invocations",
            "matching_agent": "https://example.com/matching/invocations",
            "verifier_agent": "https://example.com/verifier/invocations",
            "delivery_agent": "https://example.com/delivery/invocations",
        }

    @property
    def status(self):
        return {"enabled": True, "agent_coverage": "4/4"}

    def override_endpoint(self, agent_id, endpoint):
        if endpoint:
            self.endpoints[agent_id] = endpoint

    def endpoint_for(self, agent_id):
        return self.endpoints.get(agent_id, f"https://example.com/{agent_id}/invocations")

    async def invoke(self, agent_id, payload, session_id="farmmatch", client=None):
        if agent_id == "risk_extension":
            text = json.dumps({"risks": ["Night transport requires manual review"]})
        elif agent_id == "verifier_agent":
            text = json.dumps({"verdict": "pass", "score": 100})
        elif agent_id == "delivery_agent":
            text = json.dumps({"summary": "delivery summary"})
        else:
            text = json.dumps({"summary": "ok"})
        return {"mode": "agentarts", "agent_id": agent_id, "text": text, "stream_events": 1}


class FakeLLM:
    @property
    def status(self):
        return {"enabled": False, "state": "disabled"}

    async def reflect(self, agent_id, payload, evidence):
        return {"agent_id": agent_id, "source": "test"}


class ThreeStepPlanner:
    enabled = True

    @property
    def status(self):
        return {"enabled": True, "role": "planning-and-replanning", "model": "test-planner"}

    async def create_plan(self, user_request, available_tools):
        tools = [tool for tool in ("demand_plan", "generate_plans", "verify_plans") if tool in available_tools]
        return {
            "mode": "llm-planner",
            "summary": "three deterministic tool steps",
            "steps": [
                {"id": f"S{index}", "tool": tool, "reason": "test", "input": {}, "expected_output": tool}
                for index, tool in enumerate(tools, start=1)
            ],
        }

    async def decide(self, step_result, remaining_steps, current_context):
        tool = step_result.get("tool")
        result = step_result.get("result") or {}
        if tool == "verify_plans" and result.get("verdict") != "pass":
            return {
                "mode": "llm-planner", "decision": "replan", "next_tool": "generate_plans",
                "reason": "verification failed", "repair_policy": "route_failover",
            }
        if tool == "verify_plans":
            return {"mode": "llm-planner", "decision": "stop", "next_tool": None, "reason": "verified", "repair_policy": ""}
        next_tool = remaining_steps[0]["tool"] if remaining_steps else None
        return {"mode": "llm-planner", "decision": "continue", "next_tool": next_tool, "reason": "next", "repair_policy": ""}


class DisabledPlanner:
    enabled = False

    @property
    def status(self):
        return {"enabled": False, "state": "disabled", "model": "deterministic-fallback"}


def _request(db, scenario_id="heatwave-iii"):
    scenario = db.get_scenario(scenario_id)
    materials = db.list_materials()
    by_code = {item["code"]: item for item in materials}
    request = {
        "scenario_id": scenario["id"],
        "event": {
            "type": scenario["event_type"], "severity": scenario["severity"],
            "location": scenario["location"], "affected_population": scenario["affected_population"],
            "closed_route_ids": scenario["closed_routes"],
        },
        "demand_items": [
            {
                "material_id": by_code[item["material_code"]]["id"],
                "quantity": item["quantity"], "priority": item["priority"],
                "deadline_hours": item["deadline_hours"],
            }
            for item in scenario["default_demand"]
        ],
        "weights": {"speed": 0.38, "cost": 0.27, "risk": 0.35},
    }
    water_id = by_code["MAT-WATER-001"]["id"]
    with db.connect() as conn:
        conn.execute("UPDATE inventory SET quantity=3000, locked_quantity=0 WHERE material_id=?", (water_id,))
    return scenario, request


def _register_extension(db):
    registry = AgentRegistryService(db)
    registry.sync_builtin_agents(AGENTS)
    registry.register_agent({
        "id": "risk_extension",
        "name": "Risk extension",
        "role": "risk-review",
        "description": "post verification risk review",
        "capabilities": ["risk-review"],
        "version": "v1",
        "endpoint": "https://example.com/risk-extension/invocations",
        "model": "test",
        "prompt_version": "v1",
    })
    WorkflowRepository(db).save_workflow("planner-post-review", {
        "name": "Planner post review",
        "stage": "post_verification",
        "definition": {
            "nodes": [{
                "id": "A05", "type": "agent", "agent_id": "risk_extension",
                "depends_on": [], "on_error": "continue",
            }]
        },
    })


def test_planner_runs_three_tool_steps_and_post_verification_extensions(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "planner-normal.db", database_url="")
    db.initialize()
    scenario, request = _request(db)
    _register_extension(db)
    run_id = "RUN-PLANNER-NORMAL"
    db.create_run(run_id, scenario["id"], request)
    monkeypatch.setattr(orchestrator_module, "AgentArtsBridge", lambda: FakeAgentArts())
    monkeypatch.setattr(orchestrator_module, "LLMService", FakeLLM)
    monkeypatch.setattr(orchestrator_module, "LLMPlanner", lambda: ThreeStepPlanner())

    asyncio.run(orchestrator_module.orchestrate(db, run_id, request))

    run = db.get_run(run_id)
    result = run["result"]
    assert run["status"] == "awaiting_approval", result
    assert result["planner_trace"]["executed_steps"] == 3
    assert result["final_verification"]["verdict"] == "pass"
    assert result["delivery"]
    assert {item["stage"] for item in result["stage_metrics"]} >= set(orchestrator_module.STAGE_NAMES)
    assert result["agentarts_trace"]
    assert result["extension_reviews"]
    assert result["extension_workflow_runs"]


def test_planner_replans_after_verification_failure(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "planner-replan.db", database_url="")
    db.initialize()
    scenario, request = _request(db)
    run_id = "RUN-PLANNER-REPLAN"
    db.create_run(run_id, scenario["id"], request)
    original_verify = orchestrator_module.verify_plans
    verify_calls = {"count": 0}

    def flaky_verify(*args, **kwargs):
        verify_calls["count"] += 1
        if verify_calls["count"] == 1:
            return {
                "verdict": "rework", "score": 40.0, "reports": [],
                "blocker_summary": ["ROAD-CLOSED"], "required_fixes": ["Use a backup route"],
            }
        return original_verify(*args, **kwargs)

    monkeypatch.setattr(orchestrator_module, "verify_plans", flaky_verify)
    monkeypatch.setattr(orchestrator_module, "AgentArtsBridge", lambda: FakeAgentArts())
    monkeypatch.setattr(orchestrator_module, "LLMService", FakeLLM)
    monkeypatch.setattr(orchestrator_module, "LLMPlanner", lambda: ThreeStepPlanner())

    asyncio.run(orchestrator_module.orchestrate(db, run_id, request))

    result = db.get_run(run_id)["result"]
    assert len(result["rounds"]) >= 2
    assert result["loop_metrics"]["rework_rounds"] >= 1
    assert result["final_verification"]["verdict"] == "pass"
    assert result["delivery"]["status"] == "ready"


def test_invalid_planner_json_retries_once_then_uses_deterministic_fallback(tmp_path: Path, monkeypatch):
    class InvalidJsonClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"choices": [{"message": {"content": "not-json"}}]},
            )

    db = Database(tmp_path / "planner-invalid-json.db", database_url="")
    db.initialize()
    scenario, request = _request(db)
    run_id = "RUN-PLANNER-INVALID"
    db.create_run(run_id, scenario["id"], request)
    monkeypatch.setattr(llm_planner_module.httpx, "AsyncClient", lambda **kwargs: InvalidJsonClient())
    monkeypatch.setattr(
        orchestrator_module, "LLMPlanner",
        lambda: llm_planner_module.LLMPlanner(
            db, run_id, enabled=True, base_url="https://example.com/v1",
            api_key="test-key", model="test-model",
        ),
    )
    monkeypatch.setattr(orchestrator_module, "LLMService", FakeLLM)

    asyncio.run(orchestrator_module.orchestrate(db, run_id, request))

    run = db.get_run(run_id)
    assert run["status"] != "failed"
    assert run["result"]["delivery"]["status"] == "ready"
    with db.connect() as conn:
        rows = conn.execute("SELECT status, fallback FROM llm_calls WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
    assert len(rows) == 2
    assert [row["status"] for row in rows] == ["invalid_json", "invalid_json"]


def test_planner_budget_exhaustion_uses_deterministic_steps(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "planner-budget.db", database_url="")
    db.initialize()
    scenario, request = _request(db)
    run_id = "RUN-PLANNER-BUDGET"
    db.create_run(run_id, scenario["id"], request)

    def exhausted_planner():
        planner = llm_planner_module.LLMPlanner(
            db, run_id, enabled=True, base_url="https://example.com/v1",
            api_key="test-key", model="test-model", budget_seconds=1,
        )
        planner._run_started_at = time.monotonic() - 10
        return planner

    monkeypatch.setattr(orchestrator_module, "LLMPlanner", exhausted_planner)
    monkeypatch.setattr(orchestrator_module, "LLMService", FakeLLM)

    asyncio.run(orchestrator_module.orchestrate(db, run_id, request))

    result = db.get_run(run_id)["result"]
    assert result["planner_trace"]["initial_plan"]["mode"] == "deterministic-fallback"
    assert result["planner_trace"]["executed_steps"] >= 4
    assert result["delivery"]["status"] == "ready"
    with db.connect() as conn:
        rows = conn.execute("SELECT status FROM llm_calls WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
    assert [row["status"] for row in rows] == ["budget_exhausted"]


def test_partial_agentarts_endpoints_are_local_configured_without_fallback_event(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "planner-partial-agentarts.db", database_url="")
    db.initialize()
    scenario, request = _request(db)
    run_id = "RUN-PARTIAL-AGENTARTS"
    db.create_run(run_id, scenario["id"], request)
    endpoints = {
        "demand_agent": "https://example.com/demand/invocations",
        "verifier_agent": "https://example.com/verifier/invocations",
    }
    monkeypatch.setattr(orchestrator_module, "AgentArtsBridge", lambda: FakeAgentArts(endpoints))
    monkeypatch.setattr(orchestrator_module, "LLMService", FakeLLM)
    monkeypatch.setattr(orchestrator_module, "LLMPlanner", lambda: DisabledPlanner())

    asyncio.run(orchestrator_module.orchestrate(db, run_id, request))

    result = db.get_run(run_id)["result"]
    assert result["agentarts_trace"]["matching_agent"]["mode"] == "local-configured"
    assert result["agentarts_trace"]["delivery_agent"]["mode"] == "local-configured"
    events = db.events_after(run_id)
    assert not [event for event in events if event["type"] == "agentarts.fallback"]