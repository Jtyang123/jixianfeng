import asyncio
import json as json_module
import httpx

from app.database import Database
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

class _TimeoutThenSuccessClient:
    def __init__(self):
        self.calls = 0
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls += 1
        self.requests.append(json)
        if self.calls == 1:
            raise httpx.ReadTimeout("first attempt timed out", request=httpx.Request("POST", url))
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"choices": [{"message": {"content": json_module.dumps({
                "summary": "retry succeeded",
                "steps": [
                    {"id": "S1", "tool": "demand_plan", "reason": "demand"},
                    {"id": "S2", "tool": "generate_plans", "reason": "generate"},
                    {"id": "S3", "tool": "verify_plans", "reason": "verify"},
                    {"id": "S4", "tool": "delivery_package", "reason": "deliver"},
                ],
            })}}]},
        )


class _AlwaysTimeoutClient:
    def __init__(self):
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls += 1
        raise httpx.ReadTimeout("timed out", request=httpx.Request("POST", url))


def _planner_with_db(tmp_path, run_id, budget_seconds=45):
    db = Database(tmp_path / "planner-timeout.db", database_url="")
    db.initialize()
    planner = LLMPlanner(
        db, run_id, enabled=True, base_url="https://example.com/v1",
        api_key="test-key", model="test-model", budget_seconds=budget_seconds,
    )
    return db, planner


def test_read_timeout_retries_once_then_succeeds_and_truncates_evidence(tmp_path, monkeypatch):
    import app.llm_planner as module
    client = _TimeoutThenSuccessClient()
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: client)
    db, planner = _planner_with_db(tmp_path, "RUN-TIMEOUT-RETRY")
    evidence = [{"id": f"KB-{index}", "title": f"doc-{index}"} for index in range(1, 8)]

    plan = asyncio.run(planner.create_plan({"event": {}, "evidence": evidence}, TOOLS))

    assert plan["mode"] == "llm-planner"
    assert client.calls == 2
    first_payload = json_module.loads(client.requests[1]["messages"][1]["content"])
    assert len(first_payload["user_request"]["evidence"]) == 5
    assert first_payload["evidence_truncated"] is True
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT status, fallback, input_summary FROM llm_calls WHERE run_id=? ORDER BY id",
            ("RUN-TIMEOUT-RETRY",),
        ).fetchall()
    assert [row["status"] for row in rows] == ["timeout_retry", "success"]
    assert [row["fallback"] for row in rows] == [0, 0]
    summary = json_module.loads(rows[0]["input_summary"])
    assert summary["evidence_count"] == 5
    assert summary["evidence_truncated"] is True


def test_read_timeout_twice_logs_timeout_and_falls_back(tmp_path, monkeypatch):
    import app.llm_planner as module
    client = _AlwaysTimeoutClient()
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: client)
    db, planner = _planner_with_db(tmp_path, "RUN-TIMEOUT-FINAL")

    plan = asyncio.run(planner.create_plan({"event": {}, "evidence": []}, TOOLS))

    assert plan["mode"] == "deterministic-fallback"
    assert client.calls == 2
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT status, fallback FROM llm_calls WHERE run_id=? ORDER BY id",
            ("RUN-TIMEOUT-FINAL",),
        ).fetchall()
    assert [row["status"] for row in rows] == ["timeout_retry", "timeout"]
    assert [row["fallback"] for row in rows] == [0, 1]