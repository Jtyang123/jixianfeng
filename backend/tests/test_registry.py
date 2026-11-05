from pathlib import Path

from app.database import Database
from app.registry import AgentRegistryService


def test_agent_registry_versioning(tmp_path: Path):
    db = Database(tmp_path / "registry.db", database_url="")
    db.initialize()
    service = AgentRegistryService(db)
    service.sync_builtin_agents([
        {"id": "demand_agent", "name": "Demand Agent", "role": "Demand analysis", "version": "v1", "model": "test", "skills": ["split"]}
    ], {"demand_agent": "https://example.com/invocations"})
    agents = service.list_agents()
    assert agents[0]["active_version"] == "v1"
    assert agents[0]["endpoint_configured"] is True
    service.add_version("demand_agent", {
        "version": "v2", "endpoint": "https://example.com/v2/invocations",
        "model": "test2", "prompt_version": "p2", "activate": True,
    })
    assert service.active_endpoints()["demand_agent"].endswith("/v2/invocations")
    service.activate_version("demand_agent", "v1")
    assert service.active_endpoints()["demand_agent"].endswith("/invocations")
    assert len(service.discover("demand_agent")) == 1