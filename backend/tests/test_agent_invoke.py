from pathlib import Path

from fastapi.testclient import TestClient

from app import api as api_module
from app.auth import AuthService
from app.database import Database
from app.main import app
from app.operations import OperationsRepository
from app.registry import AgentRegistryService


class FakeAgentArts:
    fail = False

    def __init__(self):
        self.endpoint = ""

    def override_endpoint(self, agent_id: str, endpoint: str):
        self.endpoint = endpoint

    async def invoke(self, agent_id: str, payload: dict, session_id: str = "farmmatch") -> dict:
        if self.fail:
            raise RuntimeError("simulated cloud failure")
        return {
            "mode": "agentarts",
            "agent_id": agent_id,
            "session_id": session_id,
            "text": "cloud health check ok",
        }


def _client_with_db(tmp_path: Path, name: str):
    test_db = Database(tmp_path / name, database_url="")
    test_db.initialize()
    api_module.db = test_db
    api_module.ops = OperationsRepository(test_db)
    api_module.auth_service = AuthService(test_db)
    api_module.registry_service = AgentRegistryService(test_db)
    return test_db, TestClient(app)


def _register(client: TestClient, agent_id: str, endpoint: str = "https://example.com/invocations"):
    response = client.post("/api/agents", json={
        "id": agent_id,
        "name": f"Test {agent_id}",
        "role": "test role",
        "description": "invocation endpoint test",
        "capabilities": ["health-check"],
        "version": "v1",
        "endpoint": endpoint,
        "model": "test-model",
        "prompt_version": "test-prompt",
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_admin_can_invoke_active_agent_and_audit(tmp_path: Path, monkeypatch):
    FakeAgentArts.fail = False
    monkeypatch.setattr(api_module, "AgentArtsBridge", FakeAgentArts)
    _, client = _client_with_db(tmp_path, "agent-invoke-success.db")
    _register(client, "invoke_test_agent")

    response = client.post("/api/agents/invoke_test_agent/invoke", json={
        "task": {"query": "health check"},
        "session_id": "REGISTRY-TEST-001",
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["agent_id"] == "invoke_test_agent"
    assert payload["response"]["mode"] == "agentarts"
    assert payload["session_id"] == "REGISTRY-TEST-001"

    audit = client.get("/api/auth/audit-logs")
    assert audit.status_code == 200, audit.text
    assert any(item["action"] == "agent_invoked" and item["resource_id"] == "invoke_test_agent"
               for item in audit.json())


def test_agent_without_active_endpoint_returns_400(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(api_module, "AgentArtsBridge", FakeAgentArts)
    _, client = _client_with_db(tmp_path, "agent-invoke-no-endpoint.db")
    _register(client, "no_endpoint_agent", endpoint="")

    response = client.post("/api/agents/no_endpoint_agent/invoke", json={"task": {"query": "test"}})
    assert response.status_code == 400
    assert "没有已激活的调用端点" in response.json()["detail"]


def test_viewer_cannot_invoke_registered_agent(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(api_module, "AgentArtsBridge", FakeAgentArts)
    _, client = _client_with_db(tmp_path, "agent-invoke-forbidden.db")
    _register(client, "admin_only_agent")
    api_module.auth_service.create_user("invoke-viewer", "ViewerPass123!", role="viewer")

    login = client.post("/api/auth/login", json={
        "username": "invoke-viewer",
        "password": "ViewerPass123!",
    })
    assert login.status_code == 200, login.text

    response = client.post("/api/agents/admin_only_agent/invoke", json={"task": {"query": "test"}})
    assert response.status_code == 403


def test_cloud_failure_returns_502(tmp_path: Path, monkeypatch):
    FakeAgentArts.fail = True
    monkeypatch.setattr(api_module, "AgentArtsBridge", FakeAgentArts)
    _, client = _client_with_db(tmp_path, "agent-invoke-failure.db")
    _register(client, "failing_agent")

    response = client.post("/api/agents/failing_agent/invoke", json={"task": {"query": "test"}})
    assert response.status_code == 502
    assert "simulated cloud failure" in response.json()["detail"]

