import asyncio
import json

import httpx
import pytest

from app import tools as tools_module
from app.tools import ToolError, mcp_call_remote, validate_tool_arguments, validate_tool_endpoint


def test_tool_endpoint_rejects_link_local_ssrf(monkeypatch):
    monkeypatch.setattr(tools_module, "TOOL_ALLOWED_HOSTS", ["169.254.169.254"])
    monkeypatch.setattr(tools_module, "TOOL_ALLOW_PRIVATE_HOSTS", False)
    with pytest.raises(ToolError, match="SSRF"):
        validate_tool_endpoint("http://169.254.169.254/latest/meta-data")


def test_mcp_client_performs_initialize_session_and_tools_call(monkeypatch):
    monkeypatch.setattr(tools_module, "TOOL_ALLOWED_HOSTS", ["example.com"])
    monkeypatch.setattr(
        tools_module.socket, "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )
    requests = []

    def handler(request: httpx.Request):
        payload = json.loads(request.content.decode("utf-8"))
        requests.append((payload["method"], dict(request.headers), payload))
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": payload["id"], "result": {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "test"}}},
                headers={"Mcp-Session-Id": "session-123", "content-type": "application/json"},
            )
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202, headers={"content-type": "application/json"})
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload["id"], "result": {"content": [{"type": "text", "text": "ok"}]}},
            headers={"content-type": "application/json"},
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await mcp_call_remote("https://example.com/mcp", "demo_tool", {"value": 1}, client=client)

    result = asyncio.run(scenario())
    assert [item[0] for item in requests] == ["initialize", "notifications/initialized", "tools/call"]
    assert requests[2][1]["mcp-session-id"] == "session-123"
    assert result["protocol_version"] == "2024-11-05"
    assert result["session_id"] == "session-123"
    assert result["response"]["result"]["content"][0]["text"] == "ok"


def test_tool_argument_schema_rejects_coerced_material_id():
    schema = {
        "type": "object",
        "properties": {"material_id": {"type": "integer"}, "status": {"type": "string"}},
        "additionalProperties": False,
    }
    with pytest.raises(ToolError, match="integer"):
        validate_tool_arguments({"material_id": "3"}, schema)
    with pytest.raises(ToolError, match="extra"):
        validate_tool_arguments({"status": "available", "extra": True}, schema)
