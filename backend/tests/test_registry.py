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
def test_fully_cloud_metrics_exclude_fallback_runs(tmp_path: Path):
    db = Database(tmp_path / "cloud-metrics.db", database_url="")
    db.initialize()
    service = AgentRegistryService(db)
    agent_ids = ["demand_agent", "matching_agent", "verifier_agent", "delivery_agent"]
    full_trace = {agent_id: {"mode": "agentarts"} for agent_id in agent_ids}
    full_trace["verification_vote"] = {"mode": "internal-vote"}
    mixed_trace = dict(full_trace)
    mixed_trace["verifier_agent"] = {"mode": "local-fallback-after-agentarts-error"}

    for run_id, trace in (("FULL-CLOUD-001", full_trace), ("MIXED-CLOUD-001", mixed_trace)):
        db.create_run(run_id, "rainstorm-ii", {"scenario_id": "rainstorm-ii"})
        for agent_id in agent_ids:
            db.add_event(run_id, "agentarts.called", agent_id, agent_id, "called")
            event_type = "agentarts.completed" if trace[agent_id]["mode"] == "agentarts" else "agentarts.fallback"
            db.add_event(run_id, event_type, agent_id, agent_id, event_type)
        db.update_run(run_id, "awaiting_approval", {"agentarts_trace": trace})

    metrics = service.fully_cloud_run_metrics()
    assert metrics["samples"] == 1
    assert metrics["successful_runs"] == 1
    assert metrics["agent_call_count"] == 4
    assert metrics["agent_call_success_rate"] == 1.0
    assert metrics["agent_fallback_count"] == 0
    assert metrics["run_ids"] == ["FULL-CLOUD-001"]

    runtime = service.runtime_metrics(run_ids=metrics["run_ids"])
    assert set(runtime) == set(agent_ids)
    assert all(item["sample_count"] == 1 for item in runtime.values())
    assert all(item["success_rate"] == 1.0 for item in runtime.values())
def test_agent_performance_table_aggregates_success_and_latency(tmp_path: Path):
    db = Database(tmp_path / "performance.db", database_url="")
    db.initialize()
    db.record_agent_performance("matching_agent", "RUN-1", "invoke", True, 1200)
    db.record_agent_performance("matching_agent", "RUN-2", "invoke", True, 1800)
    db.record_agent_performance("matching_agent", "RUN-3", "invoke", False, 3000, "TimeoutError")
    summary = db.agent_performance_summary()["matching_agent"]
    assert summary["historical_samples"] == 3
    assert summary["historical_success_rate"] == 0.6667
    assert summary["historical_average_latency_ms"] == 2000
    assert summary["historical_error_types"]["TimeoutError"] == 1