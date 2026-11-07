import asyncio
import time
from pathlib import Path

import pytest

from app.database import Database
from app.workflow_engine import WorkflowEngine, WorkflowRepository, WorkflowValidationError, validate_workflow


def test_workflow_rejects_cycle():
    with pytest.raises(WorkflowValidationError, match="循环依赖"):
        validate_workflow({
            "nodes": [
                {"id": "A", "type": "agent", "agent_id": "a", "depends_on": ["B"]},
                {"id": "B", "type": "agent", "agent_id": "b", "depends_on": ["A"]},
            ]
        })


def test_workflow_parallel_retry_condition_and_subworkflow(tmp_path: Path):
    db = Database(tmp_path / "workflow.db", database_url="")
    db.initialize()
    repo = WorkflowRepository(db)
    repo.save_workflow("child", {
        "name": "子工作流",
        "stage": "standalone",
        "definition": {
            "nodes": [
                {"id": "CHILD", "type": "agent", "agent_id": "child_agent", "depends_on": []},
            ]
        },
    })
    parent_definition = {
        "nodes": [
            {"id": "A", "type": "agent", "agent_id": "agent_a", "depends_on": [], "priority": 90},
            {"id": "B", "type": "agent", "agent_id": "agent_b", "depends_on": [], "priority": 80},
            {
                "id": "A05",
                "type": "agent",
                "agent_id": "retry_agent",
                "depends_on": ["A", "B"],
                "retry": {"max_attempts": 2, "when": {"field": "needs_retry", "operator": "truthy"}},
            },
            {
                "id": "SKIP",
                "type": "agent",
                "agent_id": "never_run",
                "depends_on": ["A05"],
                "when": {"field": "verification.verdict", "operator": "eq", "value": "rework"},
            },
            {
                "id": "SUB",
                "type": "subworkflow",
                "workflow_id": "child",
                "depends_on": ["A05"],
            },
        ]
    }
    starts = {}
    attempts = {"retry_agent": 0}

    async def invoke(agent_id, context, node):
        starts.setdefault(agent_id, time.monotonic())
        if agent_id == "agent_a":
            await asyncio.sleep(0.08)
            return {"agent_id": agent_id, "value": "a"}
        if agent_id == "agent_b":
            await asyncio.sleep(0.08)
            return {"agent_id": agent_id, "value": "b"}
        if agent_id == "retry_agent":
            attempts[agent_id] += 1
            return {"agent_id": agent_id, "needs_retry": attempts[agent_id] == 1}
        if agent_id == "child_agent":
            return {"agent_id": agent_id, "value": "child"}
        if agent_id == "never_run":
            raise AssertionError("条件节点不应执行")
        return {"agent_id": agent_id}

    engine = WorkflowEngine(
        db,
        invoke_agent=invoke,
        load_workflow=repo.get_workflow,
        max_workers=3,
        agent_metrics={},
    )
    result = asyncio.run(engine.execute(
        "parent",
        parent_definition,
        {"verification": {"verdict": "pass"}},
    ))

    assert result["status"] == "success"
    assert abs(starts["agent_a"] - starts["agent_b"]) < 0.04
    assert attempts["retry_agent"] == 2
    assert "SKIP" in result["skipped"]
    assert result["outputs"]["SUB"]["status"] == "success"
    assert result["outputs"]["SUB"]["outputs"]["CHILD"]["value"] == "child"
    assert result["metrics"]["peak_concurrency"] >= 2
    assert result["metrics"]["end_to_end_ms"] >= 80
    stored = repo.get_run(result["run_id"])
    assert stored["status"] == "success"
    assert stored["metrics"]["peak_concurrency"] >= 2
    assert any(node["node_id"] == "SUB.CHILD" for node in stored["nodes"])
