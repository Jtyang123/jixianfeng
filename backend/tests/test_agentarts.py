import asyncio

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
    client = _FakeClient()

    result = asyncio.run(bridge.invoke(
        "demand_agent", {"query": "health check"}, session_id="shared-client-test", client=client,
    ))

    assert result["mode"] == "agentarts"
    assert result["text"] == "ok"
    assert len(client.calls) == 1
    assert client.calls[0]["headers"]["Authorization"] == "Bearer test-key"
