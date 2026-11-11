from pathlib import Path

from app import api as api_module
from benchmark.run_perf_benchmark import (
    DEFAULT_CONCURRENCY_LEVELS,
    DEFAULT_REPEAT_COUNT,
    load_latest_benchmark_report,
    run_perf_benchmark,
)
from app.database import Database


def test_benchmark_report_structure_and_default_levels(tmp_path: Path):
    assert DEFAULT_CONCURRENCY_LEVELS == (1, 5, 10, 20)
    assert DEFAULT_REPEAT_COUNT == 3
    db = Database(tmp_path / "benchmark.db", database_url="")
    db.initialize()
    output = tmp_path / "benchmark_latest.json"
    report = run_perf_benchmark(db, levels=(1, 5), repeat_count=2, output_path=output)
    assert report["status"] == "ready"
    assert report["runtime_mode"] == "deterministic-degraded"
    assert report["levels"] == [1, 5]
    assert report["repeat_count"] == 2
    assert report["scenario_count"] >= 1
    assert report["fixed_samples_per_level"] == report["scenario_count"] * 2
    assert {item["samples"] for item in report["results"]} == {report["fixed_samples_per_level"]}
    assert output.exists()
    for level in report["results"]:
        for field in (
            "concurrency", "samples", "safe_outcome_rate", "average_latency_ms",
            "p95_latency_ms", "throughput_runs_per_second", "average_score",
            "llm_calls", "llm_latency_avg_ms", "llm_latency_p95_ms", "llm_calls_per_run",
            "tool_calls", "tool_call_breakdown", "consistency", "baseline",
        ):
            assert field in level
        assert level["consistency"] == 1.0
        assert level["llm_calls"] == 0
        assert level["tool_calls"] == sum(level["tool_call_breakdown"].values())
        assert level["tool_calls"] >= 4
        assert level["warmup_samples"] == level["samples"]
        assert level["baseline"]["average_score"] < 100
        for field in ("safe_proposal_rate", "average_latency_ms", "p95_latency_ms", "average_score", "tool_calls", "tool_call_breakdown"):
            assert field in level["baseline"]
        assert level["baseline"]["tool_calls"] == sum(level["baseline"]["tool_call_breakdown"].values())
    comparison = report["comparison"]
    assert {"multi_agent", "single_agent_greedy", "delta"} <= set(comparison)
    assert {"safe_outcome_rate", "average_latency_ms", "average_score"} <= set(comparison["multi_agent"])
    assert {"safe_proposal_rate", "average_latency_ms", "average_score"} <= set(comparison["single_agent_greedy"])


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
    assert response["comparison"]["multi_agent"]["safe_outcome_rate"] == loaded["comparison"]["multi_agent"]["safe_outcome_rate"]
