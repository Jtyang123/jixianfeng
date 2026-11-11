import asyncio

from app.registry import apply_agent_load
from app.scheduler import AsyncPriorityExecutor, build_dynamic_schedule, rank_agents_for_task


def test_async_priority_executor_runs_higher_priority_first_and_records_metrics():
    async def scenario():
        executor = AsyncPriorityExecutor({"blocker": 90, "low": 10, "high": 99}, max_workers=1)
        order: list[str] = []

        async def work(name: str, delay: float = 0.01):
            await asyncio.sleep(delay)
            order.append(name)
            return name

        blocker = asyncio.create_task(executor.submit("blocker", lambda: work("blocker", 0.05)))
        await asyncio.sleep(0.01)
        low = asyncio.create_task(executor.submit("low", lambda: work("low")))
        high = asyncio.create_task(executor.submit("high", lambda: work("high")))
        await asyncio.gather(blocker, low, high)
        metrics = executor.metrics()
        await executor.close()
        return order, metrics

    order, metrics = asyncio.run(scenario())
    assert order == ["blocker", "high", "low"]
    assert metrics["engine"] == "asyncio.PriorityQueue"
    assert metrics["workers"] == 1
    assert metrics["total_tasks"] == 3
    assert metrics["successful_tasks"] == 3
    assert metrics["peak_concurrency"] == 1
    assert metrics["agents"]["high"]["samples"] == 1


def test_async_priority_executor_limits_parallelism():
    async def scenario():
        executor = AsyncPriorityExecutor({"a": 99, "b": 98, "c": 97}, max_workers=2)
        active = 0
        peak = 0

        async def work():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return True

        await asyncio.gather(*[executor.submit(agent_id, work) for agent_id in ("a", "b", "c")])
        metrics = executor.metrics()
        await executor.close()
        return peak, metrics

    peak, metrics = asyncio.run(scenario())
    assert peak == 2
    assert metrics["peak_concurrency"] == 2
    assert metrics["total_tasks"] == 3
def test_scheduler_uses_latency_failure_and_backlog_feedback():
    schedule = build_dynamic_schedule(
        {"event": {"severity": "II级", "affected_population": 12000}}, [], 6
    )
    loaded = apply_agent_load(schedule, {
        "matching_agent": {
            "average_latency_ms": 4000,
            "failure_rate": 0.5,
            "pending_calls": 3,
            "load_score": 95,
            "success_rate": 0.5,
            "historical_success_rate": 0.5,
            "historical_samples": 10,
        }
    })
    item = next(row for row in loaded["dispatch"] if row["agent_id"] == "matching_agent")
    assert item["base_priority"] > item["effective_priority"]
    assert item["latency_penalty"] > 0
    assert item["failure_penalty"] > 0
    assert item["backlog_penalty"] > 0
    assert item["historical_success_rate"] == 0.5
    assert item["history_success_bonus"] == -5
    assert "capability_match*0.4" in loaded["priority_formula"]
    assert item["allocation_mode"] == "capability-load-match"
    assert item["candidate_ranking"]

def test_capability_profile_ranks_match_then_load_balance():
    agents = [
        {"id": "agent-a", "capabilities": ["需求拆分", "灾害语义理解", "优先级判定"], "priority": 90},
        {"id": "agent-b", "capabilities": ["需求拆分", "灾害语义理解", "优先级判定"], "priority": 80},
        {"id": "agent-c", "capabilities": [], "priority": 99},
    ]
    metrics = {
        "agent-a": {"historical_samples": 10, "historical_success_rate": 0.6, "load_score": 90, "pending_calls": 4},
        "agent-b": {"historical_samples": 10, "historical_success_rate": 0.9, "load_score": 10, "pending_calls": 0},
        "agent-c": {"historical_samples": 0, "load_score": 0, "pending_calls": 0},
    }
    ranked = rank_agents_for_task("demand", agents, metrics)
    assert ranked[0]["agent_id"] == "agent-b"
    assert ranked[0]["capability_match"] == 1.0
    assert ranked[-1]["agent_id"] == "agent-c"


def test_capability_scheduler_uses_cold_start_profile_without_history():
    schedule = build_dynamic_schedule({"event": {"severity": "II级", "affected_population": 1000}}, [], 2)
    loaded = apply_agent_load(schedule, {}, agents=[])
    assert all(item["allocation_mode"] == "capability-load-match" for item in loaded["dispatch"])
    assert all(item["historical_success_rate"] == 0.5 for item in loaded["dispatch"])
    assert all(item["preferred_agent_id"] == item["assigned_agent_id"] for item in loaded["dispatch"])
    assert all(item["preferred_agent_id"] != "rag_retrieve" for item in loaded["dispatch"])
    assert len({item["effective_priority"] for item in loaded["dispatch"]}) > 1


def test_capability_scheduler_can_assign_a_ranked_replacement_agent():
    schedule = build_dynamic_schedule({"event": {"severity": "II级", "affected_population": 1000}}, [], 2)
    agents = [
        {"id": "demand_agent", "capabilities": ["需求拆分", "灾害语义理解", "优先级判定"]},
        {"id": "demand_backup", "capabilities": ["需求拆分", "灾害语义理解", "优先级判定"]},
        {"id": "matching_agent", "capabilities": ["多目标规划", "仓库分配", "备用路线选择"]},
        {"id": "verifier_agent", "capabilities": ["交叉核验", "硬阻断识别", "风险评分"]},
        {"id": "delivery_agent", "capabilities": ["方案比较", "执行摘要", "报告生成"]},
    ]
    metrics = {
        "demand_backup": {
            "historical_samples": 5,
            "historical_success_rate": 1.0,
            "load_score": 0,
            "pending_calls": 0,
        },
    }
    loaded = apply_agent_load(schedule, metrics, agents=agents)
    item = next(row for row in loaded["dispatch"] if row["preferred_agent_id"] == "demand_agent")
    assert item["assigned_agent_id"] == "demand_backup"
    assert item["agent_id"] == "demand_backup"
    assert item["assignment_changed"] is True
    assert item["candidate_ranking"][0]["agent_id"] == "demand_backup"
