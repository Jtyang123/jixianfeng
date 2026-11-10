from pathlib import Path

from app import api as api_module
from benchmark.run_perf_benchmark import (
    DEFAULT_CONCURRENCY_LEVELS,
    load_latest_benchmark_report,
    run_perf_benchmark,
)
from app.database import Database


def test_benchmark_report_structure_and_default_levels(tmp_path: Path):
    assert DEFAULT_CONCURRENCY_LEVELS == (1, 5, 10, 20)
    db = Database(tmp_path / "benchmark.db", database_url="")
    db.initialize()
    output = tmp_path / "benchmark_latest.json"
    report = run_perf_benchmark(db, levels=(1,), repeat_count=2, output_path=output)
    assert report["status"] == "ready"
    assert report["runtime_mode"] == "deterministic-core"
    assert report["levels"] == [1]
    assert report["repeat_count"] == 2
    assert report["scenario_count"] >= 1
    assert output.exists()
    level = report["results"][0]
    for field in (
        "concurrency", "samples", "success_rate", "safety_rate",
        "average_latency_ms", "p95_latency_ms", "average_score",
        "llm_calls", "tool_calls", "consistency", "baseline",
    ):
        assert field in level
    assert level["samples"] == 2
    assert level["consistency"] == 1.0
    assert level["llm_calls"] == 0
    assert level["tool_calls"] >= 4
    for field in ("success_rate", "average_latency_ms", "p95_latency_ms", "average_score", "tool_calls"):
        assert field in level["baseline"]
    comparison = report["comparison"]
    assert {"multi_agent", "single_agent_greedy", "delta"} <= set(comparison)
    assert {"safety_rate", "average_latency_ms", "average_score"} <= set(comparison["multi_agent"])
    assert {"safety_rate", "average_latency_ms", "average_score"} <= set(comparison["single_agent_greedy"])


def test_benchmark_report_loader_and_api_return_latest_result(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "benchmark-api.db", database_url="")
    db.initialize()
    output = tmp_path / "benchmark_api.json"
    expected = run_perf_benchmark(db, levels=(1,), repeat_count=2, output_path=output)
    monkeypatch.setenv("PERF_BENCHMARK_REPORT", str(output))
    loaded = load_latest_benchmark_report()
    assert loaded["status"] == "ready"
    assert loaded["report_version"] == expected["report_version"]
    response = api_module.benchmark_report()
    assert response["comparison"]["multi_agent"]["safety_rate"] == loaded["comparison"]["multi_agent"]["safety_rate"]
