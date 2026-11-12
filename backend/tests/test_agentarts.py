import asyncio
import time

import httpx

import app.agentarts as agentarts_module
from app.agentarts import AgentArtsBridge


def test_agentarts_sse_parser():
    stream = '''
data: {"event":"workflow_started","data":{"start_time":1}}

data: {"event":"message","data":{"text":"partial","index":0}}

data: {"event":"workflow_finished","data":{"status":{"code":1},"outputs":{"responseContent":"{\\"summary\\":\\"ok\\"}"}}}

data: {"event":"end"}
'''
    text, count = AgentArtsBridge._parse_stream(stream)
    assert count == 4
    assert text == '{"summary":"ok"}'


def test_agentarts_role_coverage():
    bridge = AgentArtsBridge()
    bridge.endpoints = {"demand_agent": "https://example.com/invocations"}
    status = bridge.status
    assert status["agent_coverage"] == "1/4"
    assert status["configured_agents"] == ["demand_agent"]
    assert status["missing_agents"] == ["matching_agent", "verifier_agent", "delivery_agent"]
    assert status["all_agents_configured"] is False


def test_agentarts_uses_per_agent_api_key():
    bridge = AgentArtsBridge()
    bridge.api_key = "global-key"
    bridge.agent_keys = {"matching_agent": "matching-key"}
    matching_headers = bridge._headers("session", "matching_agent")
    demand_headers = bridge._headers("session", "demand_agent")
    assert matching_headers["Authorization"] == "Bearer matching-key"
    assert demand_headers["Authorization"] == "Bearer global-key"



class _FakeResponse:
    headers = {"content-type": "application/json"}
    text = '{"response":"ok"}'

    def raise_for_status(self):
        return None

    def json(self):
        return {"response": "ok"}


class _FakeClient:
    def __init__(self):
        self.calls = []

    async def post(self, url, json, headers):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse()


def test_agentarts_reuses_injected_http_client():
    bridge = AgentArtsBridge()
    bridge.enabled = True
    bridge.endpoints = {"demand_agent": "https://example.com/invocations"}
    bridge.api_key = "test-key"
    bridge.agent_keys = {}
    client = _FakeClient()

    result = asyncio.run(bridge.invoke(
        "demand_agent", {"query": "health check"}, session_id="shared-client-test", client=client,
    ))

    assert result["mode"] == "agentarts"
    assert result["text"] == "ok"
    assert len(client.calls) == 1
    assert client.calls[0]["headers"]["Authorization"] == "Bearer test-key"


class _PingClient:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.get_calls = 0
        self.post_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        self.get_calls += 1
        await asyncio.sleep(0.05)
        if self.mode == "timeout":
            raise httpx.TimeoutException("timeout")
        if self.mode == "500":
            return httpx.Response(500, request=httpx.Request("GET", url))
        return httpx.Response(200, request=httpx.Request("GET", url))

    async def post(self, url, json=None, headers=None, timeout=None):
        self.post_calls += 1
        return httpx.Response(405, request=httpx.Request("POST", url))


class _InvocationProbeClient:
    def __init__(self):
        self.get_calls = 0
        self.post_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        self.get_calls += 1
        raise AssertionError("GET /invocations must not be used as a health check")

    async def post(self, url, json=None, headers=None, timeout=None):
        self.post_calls += 1
        return httpx.Response(200, request=httpx.Request("POST", url))


def test_agentarts_invocation_probe_uses_post(monkeypatch):
    client = _InvocationProbeClient()
    monkeypatch.setattr(agentarts_module.httpx, "AsyncClient", lambda **kwargs: client)
    bridge = AgentArtsBridge()
    bridge.enabled = True
    bridge.api_key = "test-key"
    bridge.endpoints = {"demand_agent": "https://example.com/invocations"}

    result = asyncio.run(bridge.ping())

    assert result["online_rate"] == 1.0
    assert result["checks"][0]["http_status"] == 200
    assert client.get_calls == 0
    assert client.post_calls == 1


def _probe_bridge() -> AgentArtsBridge:
    bridge = AgentArtsBridge()
    bridge.enabled = True
    bridge.api_key = "test-key"
    bridge.endpoints = {
        "demand_agent": "https://example.com/a",
        "matching_agent": "https://example.com/b",
        "verifier_agent": "https://example.com/c",
        "delivery_agent": "https://example.com/d",
    }
    return bridge


def test_agentarts_ping_success_is_concurrent_and_reports_latency(monkeypatch):
    client = _PingClient("ok")
    monkeypatch.setattr(agentarts_module.httpx, "AsyncClient", lambda **kwargs: client)
    bridge = _probe_bridge()
    started = time.perf_counter()
    result = asyncio.run(bridge.ping())
    elapsed = time.perf_counter() - started
    assert result["status"] == "ok"
    assert result["online_rate"] == 1.0
    assert len(result["checks"]) == 4
    assert all(item["latency_ms"] >= 0 for item in result["checks"])
    assert elapsed < 0.35


def test_agentarts_ping_500_reports_http_5xx(monkeypatch):
    monkeypatch.setattr(agentarts_module.httpx, "AsyncClient", lambda **kwargs: _PingClient("500"))
    result = asyncio.run(_probe_bridge().ping())
    assert result["status"] == "degraded"
    assert result["online_rate"] == 0.0
    assert {item["error_type"] for item in result["checks"]} == {"http_5xx"}


def test_agentarts_ping_timeout_reports_timeout(monkeypatch):
    monkeypatch.setattr(agentarts_module.httpx, "AsyncClient", lambda **kwargs: _PingClient("timeout"))
    result = asyncio.run(_probe_bridge().ping())
    assert result["status"] == "degraded"
    assert result["online_rate"] == 0.0
    assert {item["error_type"] for item in result["checks"]} == {"timeout"}


def test_agentarts_ping_without_endpoint_uses_local_status():
    bridge = AgentArtsBridge()
    bridge.enabled = False
    bridge.endpoints = {}
    result = asyncio.run(bridge.ping())
    assert result["status"] == "not_configured"
    assert result["online_rate"] == 0.0
    assert result["checks"] == []
