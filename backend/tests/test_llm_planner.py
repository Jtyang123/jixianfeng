import asyncio

from app.llm_planner import LLMPlanner


TOOLS = [
    "demand_plan", "inventory_query", "route_query", "vehicle_query", "knowledge_search",
    "generate_plans", "verify_plans", "delivery_package", "escalate_human",
]


def test_create_plan_returns_structured_llm_steps(monkeypatch):
    planner = LLMPlanner(enabled=True, base_url="https://example.com/v1", api_key="real-key", model="test-model")

    async def fake_request(system_prompt, payload):
        assert "决策层" in system_prompt
        assert payload["task"] == "create_plan"
        return {"summary": "先标准化需求，再检索并生成核验", "steps": [
            {"id": "S1", "tool": "demand_plan", "reason": "标准化", "input": {}, "expected_output": "demand"},
            {"id": "S2", "tool": "knowledge_search", "reason": "检索规则", "input": {}, "expected_output": "evidence"},
            {"id": "S3", "tool": "generate_plans", "reason": "生成方案", "input": {}, "expected_output": "plans"},
            {"id": "S4", "tool": "verify_plans", "reason": "核验", "input": {}, "expected_output": "verification"},
            {"id": "S5", "tool": "delivery_package", "reason": "交付", "input": {}, "expected_output": "delivery"},
        ]}

    monkeypatch.setattr(planner, "_request_json", fake_request)
    plan = asyncio.run(planner.create_plan({"event": {"severity": "II级"}}, TOOLS))
    assert plan["mode"] == "llm-planner"
    assert [item["tool"] for item in plan["steps"]] == [
        "demand_plan", "knowledge_search", "generate_plans", "verify_plans", "delivery_package"
    ]


def test_create_plan_filters_unknown_tools_and_adds_mandatory_tools(monkeypatch):
    planner = LLMPlanner(enabled=True, base_url="https://example.com/v1", api_key="real-key", model="test-model")

    async def fake_request(system_prompt, payload):
        return {"summary": "测试", "steps": [{"id": "S1", "tool": "shell", "reason": "非法工具"}]}

    monkeypatch.setattr(planner, "_request_json", fake_request)
    plan = asyncio.run(planner.create_plan({"event": {}}, TOOLS))
    tools = [item["tool"] for item in plan["steps"]]
    assert "shell" not in tools
    assert tools[:4] == ["demand_plan", "generate_plans", "verify_plans", "delivery_package"]


def test_decide_returns_replan_decision(monkeypatch):
    planner = LLMPlanner(enabled=True, base_url="https://example.com/v1", api_key="real-key", model="test-model")

    async def fake_request(system_prompt, payload):
        assert payload["task"] == "decide_next_step"
        return {"decision": "replan", "next_tool": "generate_plans", "reason": "道路封闭", "repair_policy": "route_failover"}

    monkeypatch.setattr(planner, "_request_json", fake_request)
    decision = asyncio.run(planner.decide({"tool": "verify_plans", "result": {"verdict": "rework"}}, [], {"attempt": 1}))
    assert decision["decision"] == "replan"
    assert decision["next_tool"] == "generate_plans"
    assert decision["repair_policy"] == "route_failover"


def test_planner_falls_back_without_network():
    planner = LLMPlanner(enabled=False)
    plan = asyncio.run(planner.create_plan({"event": {}}, TOOLS))
    decision = asyncio.run(planner.decide({"tool": "demand_plan", "result": {}}, [{"tool": "generate_plans"}], {}))
    assert plan["mode"] == "deterministic-fallback"
    assert [item["tool"] for item in plan["steps"]] == ["demand_plan", "generate_plans", "verify_plans", "delivery_package"]
    assert decision["decision"] == "continue"
    assert decision["next_tool"] == "generate_plans"