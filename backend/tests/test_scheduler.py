import asyncio

from app.scheduler import AsyncPriorityExecutor


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