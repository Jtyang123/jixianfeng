import asyncio
import json
import time
from pathlib import Path

import httpx

import app.llm as llm_module
import app.llm_planner as planner_module
from app import api as api_module
from app.database import Database
from app.operations import OperationsRepository
from app.llm import LLMService
from app.llm_calls import LLMCallRepository
from app.llm_planner import LLMPlanner


class _ResponseClient:
    def __init__(self, content: str, status_code: int = 200):
        self.content = content
        self.status_code = status_code
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        request = httpx.Request("POST", url)
        return httpx.Response(
            self.status_code,
            request=request,
            json={"choices": [{"message": {"content": self.content}}]},
        )


def test_llm_planner_status_reports_misconfigured_fields():
    planner = LLMPlanner(db=None, run_id="", enabled=True, base_url="https://example.com/v1", api_key="real-key", model="")
    assert planner.state == "misconfigured"
    assert planner.enabled is False
    assert planner.missing == ["LLM_MODEL"]
    assert "缺少 LLM_MODEL" in planner.status["message"]


def test_llm_planner_invalid_json_retries_and_records_two_calls(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "llm-retry.db", database_url="")
    db.initialize()
    client = _ResponseClient("not-json")
    monkeypatch.setattr(planner_module.httpx, "AsyncClient", lambda **kwargs: client)
    planner = LLMPlanner(db, "RUN-LLM-RETRY", enabled=True, base_url="https://example.com/v1", api_key="real-key", model="test-model")
    plan = asyncio.run(planner.create_plan({"event": {"severity": "II级"}}, ["demand_plan", "generate_plans", "verify_plans", "delivery_package"]))
    calls = LLMCallRepository(db).list_calls("RUN-LLM-RETRY")
    assert plan["mode"] == "deterministic-fallback"
    assert len(calls) == 2
    assert {item["status"] for item in calls} == {"invalid_json"}
    assert calls[0]["fallback"] is True
    assert calls[1]["fallback"] is False
    assert all("Authorization" not in item["input_summary"] for item in calls)


def test_llm_planner_budget_exhaustion_is_recorded(tmp_path: Path):
    db = Database(tmp_path / "llm-budget.db", database_url="")
    db.initialize()
    planner = LLMPlanner(db, "RUN-LLM-BUDGET", enabled=True, base_url="https://example.com/v1", api_key="real-key", model="test-model")
    planner._run_started_at = time.monotonic() - 31
    result = asyncio.run(planner.create_plan({"event": {}}, ["demand_plan", "generate_plans", "verify_plans", "delivery_package"]))
    calls = LLMCallRepository(db).list_calls("RUN-LLM-BUDGET")
    assert result["mode"] == "deterministic-fallback"
    assert calls[0]["status"] == "budget_exhausted"
    assert calls[0]["fallback"] is True


def test_llm_reflect_success_is_recorded_without_prompt_or_key(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "llm-reflect.db", database_url="")
    db.initialize()
    client = _ResponseClient('{"summary":"ok"}')
    monkeypatch.setattr(llm_module.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(llm_module, "LLM_ENABLED", True)
    monkeypatch.setattr(llm_module, "LLM_BASE_URL", "https://example.com/v1")
    monkeypatch.setattr(llm_module, "LLM_API_KEY", "real-secret-key")
    monkeypatch.setattr(llm_module, "LLM_MODEL", "test-model")
    service = LLMService(db, "RUN-LLM-REFLECT")
    result = asyncio.run(service.reflect("verifier_agent", {"verdict": "rework", "summary": "库存不足"}, []))
    calls = LLMCallRepository(db).list_calls("RUN-LLM-REFLECT")
    assert result["mode"] == "llm"
    assert len(calls) == 1
    call = calls[0]
    assert call["stage"] == "reflect"
    assert call["status"] == "success"
    assert call["model"] == "test-model"
    assert call["input_digest"]
    summary = json.loads(call["input_summary"])
    assert summary["task"] == "reflect"
    assert summary["agent_id"] == "verifier_agent"
    assert "real-secret-key" not in str(call)
    assert "system" not in call["input_summary"]


def test_system_status_reports_misconfigured_llm_explicitly(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "llm-status.db", database_url="")
    db.initialize()
    monkeypatch.setattr(api_module, "db", db)
    monkeypatch.setattr(api_module, "ops", OperationsRepository(db))
    monkeypatch.setattr(llm_module, "LLM_ENABLED", True)
    monkeypatch.setattr(llm_module, "LLM_BASE_URL", "https://example.com/v1")
    monkeypatch.setattr(llm_module, "LLM_API_KEY", "real-key")
    monkeypatch.setattr(llm_module, "LLM_MODEL", "")
    status = api_module.system_status()["llm"]
    assert status["state"] == "misconfigured"
    assert status["missing"] == ["LLM_MODEL"]
    assert "当前走确定性基线" in status["message"]


class _SequenceClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append(json)
        status, payload = self.responses.pop(0)
        return httpx.Response(status, request=httpx.Request("POST", url), json=payload)


def test_llm_planner_retries_without_response_format_on_400(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "llm-format-retry.db", database_url="")
    db.initialize()
    client = _SequenceClient([
        (400, {"error": "response_format unsupported"}),
        (200, {"choices": [{"message": {"content": "{\"summary\":\"ok\",\"steps\":[]}"}}]}),
    ])
    monkeypatch.setattr(planner_module.httpx, "AsyncClient", lambda **kwargs: client)
    planner = LLMPlanner(db, "RUN-FORMAT-RETRY", enabled=True, base_url="https://example.com/v1", api_key="real-key", model="test-model")
    asyncio.run(planner.create_plan({"event": {}}, ["demand_plan", "generate_plans", "verify_plans", "delivery_package"]))
    assert len(client.calls) == 2
    assert "response_format" in client.calls[0]
    assert "response_format" not in client.calls[1]
    calls = LLMCallRepository(db).list_calls("RUN-FORMAT-RETRY")
    assert [item["status"] for item in calls] == ["success", "http_error"]


def test_llm_json_mode_false_omits_response_format(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "llm-json-off.db", database_url="")
    db.initialize()
    client = _ResponseClient('{"summary":"ok","steps":[]}')
    monkeypatch.setattr(planner_module.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(planner_module, "LLM_JSON_MODE", False)
    planner = LLMPlanner(db, "RUN-JSON-OFF", enabled=True, base_url="https://example.com/v1", api_key="real-key", model="test-model")
    asyncio.run(planner.create_plan({"event": {}}, ["demand_plan", "generate_plans", "verify_plans", "delivery_package"]))
    assert len(client.calls) == 1
    assert "response_format" not in client.calls[0]["json"]
