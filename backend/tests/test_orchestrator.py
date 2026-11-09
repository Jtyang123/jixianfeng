import asyncio
import json
import time
from pathlib import Path

from app import orchestrator as orchestrator_module
from app.database import Database
from app.knowledge_base import KnowledgeRepository
from app.registry import AgentRegistryService
from app.scheduler import AGENTS
from app.workflow_engine import WorkflowRepository


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
        if payload.get("task_type") == "rework_strategy":
            text = json.dumps({
                "selected_policy": payload["allowed_policies"][0]["id"],
                "rationale": "测试环境：从允许策略中选择路线故障转移。",
            })
        elif agent_id == "matching_agent":
            text = json.dumps({"recommended_plan_id": payload["plans"][0]["plan_id"]})
        elif agent_id == "delivery_agent":
            text = json.dumps({"recommended_plan_id": "PLAN-STALE", "summary": "stale delivery summary", "risk_alerts": ["stale delivery risk"]})
        elif agent_id == "verifier_agent":
            text = json.dumps({"verdict": "pass", "score": 100})
        elif agent_id == "risk_extension":
            text = json.dumps({"risks": ["扩展风险：需核对夜间运输"]})
        else:
            text = json.dumps({"summary": "ok"})
        return {"mode": "agentarts", "agent_id": agent_id, "text": text, "stream_events": 1}


class BlockingDemandAgentArts(FakeAgentArts):
    def __init__(self):
        super().__init__()
        self.demand_started = False
        self.demand_cancelled = False

    async def invoke(self, agent_id, payload, session_id="farmmatch", client=None):
        if agent_id == "demand_agent":
            self.demand_started = True
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                self.demand_cancelled = True
                raise
        return await super().invoke(agent_id, payload, session_id=session_id, client=client)


class FakeLLM:
    @property
    def status(self):
        return {"enabled": False}

    async def reflect(self, agent_id, payload, evidence):
        return {"agent_id": agent_id, "source": "test"}


def test_background_demand_task_is_cancelled_when_pipeline_fails(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "background-cancel.db", database_url="")
    db.initialize()
    scenario = db.get_scenario("rainstorm-ii")
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
    run_id = "RUN-BACKGROUND-CANCEL"
    db.create_run(run_id, scenario["id"], request)
    fake = BlockingDemandAgentArts()
    monkeypatch.setattr(orchestrator_module, "AgentArtsBridge", lambda: fake)
    monkeypatch.setattr(orchestrator_module, "LLMService", FakeLLM)

    def fail_generation(*args, **kwargs):
        raise RuntimeError("forced pipeline failure")

    monkeypatch.setattr(orchestrator_module, "generate_plans", fail_generation)
    asyncio.run(orchestrator_module.orchestrate(db, run_id, request))

    run = db.get_run(run_id)
    assert run["status"] == "failed"
    assert fake.demand_started is True
    assert fake.demand_cancelled is True

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
    AgentRegistryService(db).register_agent({
        "id": "demand_extension",
        "name": "需求后扩展审查",
        "role": "demand-review",
        "description": "需求阶段动态节点",
        "capabilities": ["demand-risk"],
        "version": "v1",
        "endpoint": "https://example.com/demand-extension/invocations",
        "model": "test",
        "prompt_version": "v1",
    })
    WorkflowRepository(db).save_workflow("after-demand-review", {
        "name": "需求后扩展审查",
        "stage": "after_demand",
        "definition": {
            "nodes": [
                {"id": "A01X", "type": "agent", "agent_id": "demand_extension", "depends_on": [], "on_error": "continue"}
            ]
        },
    })
    WorkflowRepository(db).save_workflow("post-review", {
        "name": "核验后扩展审查",
        "stage": "post_verification",
        "definition": {
            "nodes": [
                {
                    "id": "A05",
                    "type": "agent",
                    "agent_id": "risk_extension",
                    "depends_on": [],
                    "on_error": "continue",
                }
            ]
        },
    })
    db.create_run(run_id, scenario["id"], request)
    fake = FakeAgentArts()
    monkeypatch.setattr(orchestrator_module, "AgentArtsBridge", lambda: fake)
    monkeypatch.setattr(orchestrator_module, "LLMService", FakeLLM)

    asyncio.run(orchestrator_module.orchestrate(db, run_id, request))

    run = db.get_run(run_id)
    assert run["status"] == "awaiting_approval", run.get("result")
    assert set(run["result"]["agentarts_trace"]) == {
        "demand_agent", "matching_agent", "verifier_agent", "delivery_agent",
        "risk_extension", "demand_extension", "rework_policy",
    }
    assert abs(fake.started_at["matching_agent"] - fake.started_at["verifier_agent"]) < 0.2
    scheduler_metrics = run["result"]["orchestration"]["runtime_metrics"]
    assert scheduler_metrics["engine"] == "asyncio.PriorityQueue"
    assert scheduler_metrics["total_tasks"] >= 6
    assert scheduler_metrics["peak_concurrency"] >= 2
    decisions = {item["id"]: item for item in run["result"]["ai_decision_matrix"]}
    assert decisions["D1"]["applied"] is False
    assert decisions["D1"]["narrative_applied"] is True
    assert decisions["D1"]["priority_advisory_applied"] is False
    assert decisions["D2"]["applied"] is False
    assert decisions["D2"]["source"] == "deterministic-single-policy"
    assert decisions["D3"]["applied"] is True
    assert run["result"]["prompt_adaptations"]
    assert run["result"]["prompt_adaptations"][0]["active_version"].startswith("v1.4+adaptive")
    assert run["result"]["loop_metrics"]["adaptive_prompt_versions"]
    assert run["result"]["loop_metrics"]["adaptive_tool_overrides"]
    assert all(run["result"]["loop_metrics"]["workflow_paths"])
    assert all("citation" in item for item in run["result"]["retrieval"])
    assert run["result"]["active_prompt_versions"]["matching_agent"].startswith("v1.4+adaptive")
    assert {item["agent_id"] for item in run["result"]["extension_reviews"]} == {"risk_extension", "demand_extension"}
    assert {item["stage"] for item in run["result"]["extension_workflow_runs"]} == {"after_demand", "post_verification"}
    assert all(item["status"] == "success" for item in run["result"]["extension_workflow_runs"])
    assert any("扩展风险" in item for item in run["result"]["delivery"]["risk_alerts"])
    interpretation = run["result"]["delivery"]["agentarts_interpretation"]
    assert interpretation["review_status"] == "audit-only"
    assert interpretation["recommendation_consistency"] == "stale-plan"
    assert "stale delivery risk" not in run["result"]["delivery"]["risk_alerts"]


def test_core_workflow_can_replace_verifier_with_two_agent_vote(tmp_path: Path):
    db = Database(tmp_path / "core-vote.db", database_url="")
    db.initialize()
    scenario = db.get_scenario("rainstorm-ii")
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
    registry = AgentRegistryService(db)
    for agent_id in ("verifier_a", "verifier_b"):
        registry.register_agent({
            "id": agent_id,
            "name": f"独立核验 {agent_id[-1].upper()}",
            "role": "verifier-review",
            "description": "本地与云端均可执行的双核验节点",
            "capabilities": ["inventory-verify", "route-verify", "vote"],
            "version": "v1",
            "endpoint": "",
            "model": "local-rule-agent",
            "prompt_version": "v1",
        })
    WorkflowRepository(db).save_workflow("core-vote", {
        "name": "双核验 Agent 投票主干",
        "stage": "core",
        "enabled": True,
        "definition": {
            "nodes": [
                {"id": "A01", "type": "agent", "agent_id": "demand_agent", "depends_on": []},
                {"id": "TOOL", "type": "tool", "tool_id": "inventory_query", "arguments": {}, "depends_on": ["A01"]},
                {
                    "id": "LOOP", "type": "loop", "depends_on": ["TOOL"], "max_iterations": 3,
                    "until": {"field": "verification.verdict", "operator": "eq", "value": "pass"},
                    "body": {"nodes": [
                        {"id": "A02", "type": "agent", "agent_id": "matching_agent", "depends_on": []},
                        {"id": "VA", "type": "agent", "agent_id": "verifier_a", "depends_on": ["A02"]},
                        {"id": "VB", "type": "agent", "agent_id": "verifier_b", "depends_on": ["A02"]},
                        {"id": "VOTE", "type": "agent", "agent_id": "verification_vote", "depends_on": ["VA", "VB"]},
                        {"id": "REWORK", "type": "agent", "agent_id": "rework_policy", "depends_on": ["VOTE"], "when": {"field": "verification.verdict", "operator": "eq", "value": "rework"}},
                    ]},
                },
            ]
        },
    })
    run_id = "RUN-CORE-VOTE"
    db.create_run(run_id, scenario["id"], request)
    asyncio.run(orchestrator_module.orchestrate(db, run_id, request))
    run = db.get_run(run_id)
    assert run["status"] == "awaiting_approval", run.get("result")
    assert run["result"]["core_workflow"]["id"] == "core-vote"
    assert len(run["result"]["core_workflow_runs"]) == 1
    assert run["result"]["core_workflow_runs"][0]["loop_mode"] is True
    assert set(run["result"]["core_workflow_runs"][0]["completed"]) == {"A01", "TOOL", "LOOP"}
    assert len(run["result"]["rounds"]) == 2
    assert run["result"]["rounds"][0]["verification"]["vote_summary"]["total"] == 2
    assert run["result"]["rounds"][0]["verification"]["verdict"] == "rework"
    assert run["result"]["final_verification"]["vote_summary"] == {"pass": 2, "total": 2, "rule": "majority"}
    assert run["result"]["final_verification"]["verdict"] == "pass"

def test_inventory_tool_result_changes_matching_decision(tmp_path: Path):
    db = Database(tmp_path / "inventory-tool-impact.db", database_url="")
    db.initialize()
    scenario = db.get_scenario("heatwave-iii")
    materials = db.list_materials()
    water = next(item for item in materials if item["code"] == "MAT-WATER-001")
    request = {
        "scenario_id": scenario["id"],
        "event": {
            "type": scenario["event_type"], "severity": scenario["severity"],
            "location": scenario["location"], "affected_population": scenario["affected_population"],
            "closed_route_ids": [],
        },
        "demand_items": [{"material_id": water["id"], "quantity": 1000, "priority": "P0", "deadline_hours": 8}],
        "weights": {"speed": 0.38, "cost": 0.27, "risk": 0.35},
    }
    WorkflowRepository(db).save_workflow("tool-impact", {
        "name": "库存工具影响测试",
        "stage": "core",
        "enabled": True,
        "definition": {"nodes": [
            {"id": "A01", "type": "agent", "agent_id": "demand_agent", "depends_on": []},
            {"id": "TOOL", "type": "tool", "tool_id": "inventory_query", "arguments": {"material_id": 6}, "depends_on": ["A01"]},            {"id": "LOOP", "type": "loop", "depends_on": ["TOOL"], "max_iterations": 2,
             "until": {"field": "verification.verdict", "operator": "eq", "value": "pass"},
             "body": {"nodes": [
                 {"id": "A02", "type": "agent", "agent_id": "matching_agent", "depends_on": []},
                 {"id": "A03", "type": "agent", "agent_id": "verifier_agent", "depends_on": ["A02"]},
                 {"id": "REWORK", "type": "agent", "agent_id": "rework_policy", "depends_on": ["A03"],
                  "when": {"field": "verification.verdict", "operator": "eq", "value": "rework"}},
             ]},
            },
        ]},
    })
    run_id = "RUN-TOOL-IMPACT"
    db.create_run(run_id, scenario["id"], request)
    asyncio.run(orchestrator_module.orchestrate(db, run_id, request))
    run = db.get_run(run_id)
    assert run["status"] == "needs_human"
    assert run["result"]["inventory_source"] == "inventory_query tool"
    assert run["result"]["execution_inventory_rows"] > 0
    assert run["result"]["final_verification"]["verdict"] == "rework"
    unmet_material_ids = {item["material_id"] for plan in run["result"]["final_plans"] for item in plan.get("unmet", [])}
    assert water["id"] in unmet_material_ids

def test_cross_run_memory_is_saved_without_unsafe_override(tmp_path: Path):
    db = Database(tmp_path / "cross-run-memory.db", database_url="")
    db.initialize()
    scenario = db.get_scenario("rainstorm-ii")
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
                "quantity": item["quantity"],
                "priority": item["priority"],
                "deadline_hours": item["deadline_hours"],
            }
            for item in scenario["default_demand"]
        ],
        "weights": {"speed": 0.38, "cost": 0.27, "risk": 0.35},
    }
    for run_id in ("RUN-MEMORY-1", "RUN-MEMORY-2"):
        db.create_run(run_id, scenario["id"], request)
        asyncio.run(orchestrator_module.orchestrate(db, run_id, request))

    first = db.get_run("RUN-MEMORY-1")["result"]
    second = db.get_run("RUN-MEMORY-2")["result"]
    assert first["rework_directives"][0]["policy_selection"]["source"] == "deterministic-single-policy"
    assert second["rework_directives"][0]["policy_selection"]["source"] == "deterministic-single-policy"
    assert second["memory_context"]["matching_agent"]["successful_repair_policy"]["value"]["policy"] == "route_failover"
    memory_events = [event["type"] for event in db.events_after("RUN-MEMORY-2")]
    assert "memory.loaded" in memory_events
    assert "memory.saved" in memory_events

def test_builtin_dual_verifier_template_runs_without_manual_registration(tmp_path: Path):
    db = Database(tmp_path / "builtin-dual-template.db", database_url="")
    db.initialize()
    AgentRegistryService(db).sync_builtin_agents(AGENTS)
    scenario = db.get_scenario("rainstorm-ii")
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
                "quantity": item["quantity"],
                "priority": item["priority"],
                "deadline_hours": item["deadline_hours"],
            }
            for item in scenario["default_demand"]
        ],
        "weights": {"speed": 0.38, "cost": 0.27, "risk": 0.35},
    }
    workflow = WorkflowRepository(db).instantiate_template(
        "TPL-DUAL-VERIFY-VOTE", {"id": "dual-template-runtime", "name": "双核验模板实例"}
    )
    assert workflow["id"] == "dual-template-runtime"
    run_id = "RUN-DUAL-TEMPLATE"
    db.create_run(run_id, scenario["id"], request)
    asyncio.run(orchestrator_module.orchestrate(db, run_id, request))
    run = db.get_run(run_id)
    assert run["status"] == "awaiting_approval", run.get("result")
    assert run["result"]["core_workflow"]["id"] == "dual-template-runtime"
    assert run["result"]["rounds"][0]["verification"]["vote_summary"]["total"] == 2
    assert run["result"]["final_verification"]["vote_summary"]["total"] == 2


def test_knowledge_tool_result_is_consumed_by_downstream_context(tmp_path: Path):
    db = Database(tmp_path / "knowledge-tool-consumption.db", database_url="")
    db.initialize()
    KnowledgeRepository(db).insert_document(
        "道路封闭处置规则", "应急规则", "test",
        [{"text": "暴雨导致道路封闭时，运输方案必须排除封闭路线并优先选择开放备用通道。", "section": "道路封闭", "char_start": 0, "char_end": 38}],
    )
    scenario = db.get_scenario("heatwave-iii")
    materials = db.list_materials()
    by_code = {item["code"]: item for item in materials}
    request = {
        "scenario_id": scenario["id"],
        "event": {
            "type": scenario["event_type"], "severity": scenario["severity"],
            "location": scenario["location"], "affected_population": scenario["affected_population"],
            "closed_route_ids": [],
        },
        "demand_items": [
            {
                "material_id": by_code[item["material_code"]]["id"], "quantity": item["quantity"],
                "priority": item["priority"], "deadline_hours": item["deadline_hours"],
            }
            for item in scenario["default_demand"]
        ],
        "weights": {"speed": 0.38, "cost": 0.27, "risk": 0.35},
    }
    WorkflowRepository(db).instantiate_template("TPL-EVIDENCE-DELIVERY", {"id": "knowledge-consumer", "name": "知识工具消费测试"})
    db.create_run("RUN-KNOWLEDGE-TOOL", scenario["id"], request)
    asyncio.run(orchestrator_module.orchestrate(db, "RUN-KNOWLEDGE-TOOL", request))
    run = db.get_run("RUN-KNOWLEDGE-TOOL")
    assert run["status"] == "awaiting_approval", run.get("result")
    assert run["result"]["execution_evidence"]
    consumed = [event for event in db.events_after("RUN-KNOWLEDGE-TOOL") if event["type"] == "tool.consumed"]
    assert any(event["payload"].get("tool_id") == "knowledge_search" for event in consumed)
