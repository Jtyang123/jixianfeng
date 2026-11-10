from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Iterable

from app.agents import build_delivery_package, build_demand_plan, verify_plans
from app.config import PROJECT_DIR
from app.database import Database
from app.evaluation import _benchmark_contexts, _generate_baseline_plan, _solve_adaptively

DEFAULT_CONCURRENCY_LEVELS = (1, 5, 10, 20)
DEFAULT_REPORT_PATH = PROJECT_DIR / "data" / "benchmark_latest.json"


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)], 2)


def _task_success(feasible: bool, verdict: str) -> bool:
    return verdict == "pass" if feasible else verdict != "pass"


def _run_case(context: dict) -> dict:
    demand_started = time.perf_counter()
    multi_started = time.perf_counter()
    final_plans, verification, rounds, _directives = _solve_adaptively(
        build_demand_plan(context["request"], context["materials"], context["destinations"]),
        context["inventory"], context["warehouses"], context["routes"],
        context["vehicles"], context["closed_routes"],
    )
    delivery = None
    if verification.get("verdict") == "pass":
        delivery = build_delivery_package(
            verification, context["scenario"],
            {"id": "benchmark", "label": "benchmark", "uploaded_at": datetime.now().isoformat(timespec="seconds")},
            {"speed": 0.38, "cost": 0.27, "risk": 0.35},
        )
    multi_ms = (time.perf_counter() - multi_started) * 1000
    demand_ms = (time.perf_counter() - demand_started) * 1000
    recommended = (delivery or {}).get("recommended_plan") or {}
    multi_key_fields = {
        "verdict": verification.get("verdict"),
        "score": verification.get("score"),
        "approved_plan_ids": verification.get("approved_plan_ids", []),
        "recommended_plan_id": recommended.get("plan_id"),
        "blockers": verification.get("blocker_summary", []),
    }

    baseline_started = time.perf_counter()
    baseline_demand = build_demand_plan(context["request"], context["materials"], context["destinations"])
    baseline_plan = _generate_baseline_plan(
        baseline_demand, context["inventory"], context["warehouses"], context["routes"],
        context["vehicles"], context["closed_routes"], "nearest",
    )
    baseline_verification = verify_plans(
        [baseline_plan], baseline_demand, context["inventory"], context["routes"],
        context["vehicles"], context["closed_routes"],
    )
    baseline_ms = (time.perf_counter() - baseline_started) * 1000
    return {
        "scenario_id": context["id"],
        "feasible": bool(context["feasible"]),
        "multi_agent": {
            "elapsed_ms": round(multi_ms, 2),
            "demand_latency_ms": round(demand_ms, 2),
            "verdict": verification.get("verdict"),
            "score": verification.get("score"),
            "task_success": _task_success(bool(context["feasible"]), verification.get("verdict")),
            "rounds": len(rounds),
            "tool_calls": 2 + len(rounds) * 2,
            "llm_calls": 0,
            "key_fields": multi_key_fields,
        },
        "single_agent_greedy": {
            "elapsed_ms": round(baseline_ms, 2),
            "verdict": baseline_verification.get("verdict"),
            "score": baseline_verification.get("score"),
            "task_success": baseline_verification.get("verdict") == "pass",
            "independent_verification": False,
            "audit_note": "核验器仅作外部真值审计，不参与基线生成。",
            "tool_calls": 2,
            "llm_calls": 0,
            "key_fields": {
                "verdict": baseline_verification.get("verdict"),
                "score": baseline_verification.get("score"),
                "approved_plan_ids": baseline_verification.get("approved_plan_ids", []),
                "blockers": baseline_verification.get("blocker_summary", []),
            },
        },
    }


async def _run_once(contexts: list[dict], concurrency: int) -> list[dict]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(index: int) -> dict:
        async with semaphore:
            context = contexts[index % len(contexts)]
            return await asyncio.to_thread(_run_case, context)

    started = time.perf_counter()
    results = await asyncio.gather(*(one(index) for index in range(concurrency)), return_exceptions=True)
    wall_ms = round((time.perf_counter() - started) * 1000, 2)
    completed = [item for item in results if isinstance(item, dict)]
    errors = [str(item)[:240] for item in results if isinstance(item, Exception)]
    return completed, wall_ms, errors


def _measure_level(contexts: list[dict], concurrency: int, repeat_count: int) -> dict:
    passes: list[list[dict]] = []
    wall_times: list[float] = []
    errors: list[str] = []
    for _ in range(repeat_count):
        items, wall_ms, pass_errors = asyncio.run(_run_once(contexts, concurrency))
        passes.append(items)
        wall_times.append(wall_ms)
        errors.extend(pass_errors)
    baseline_pass = passes[0]
    comparable = 0
    consistent = 0
    for other in passes[1:]:
        paired = min(len(baseline_pass), len(other))
        comparable += paired
        consistent += sum(
            1 for index in range(paired)
            if baseline_pass[index]["multi_agent"]["key_fields"] == other[index]["multi_agent"]["key_fields"]
        )
    all_results = [item for batch in passes for item in batch]
    multi_latencies = [item["multi_agent"]["elapsed_ms"] for item in all_results]
    baseline_latencies = [item["single_agent_greedy"]["elapsed_ms"] for item in all_results]
    multi_scores = [
        float(item["multi_agent"]["score"] or 0) for item in all_results if item["feasible"]
    ]
    baseline_scores = [
        float(item["single_agent_greedy"]["score"] or 0) for item in all_results if item["feasible"]
    ]
    samples = max(len(all_results), 1)
    multi_success = sum(1 for item in all_results if item["multi_agent"]["task_success"])
    baseline_success = sum(1 for item in all_results if item["single_agent_greedy"]["task_success"])
    total_wall = sum(wall_times)
    return {
        "concurrency": concurrency,
        "samples": len(all_results),
        "repeat_count": repeat_count,
        "wall_ms": round(total_wall, 2),
        "pass_wall_ms": wall_times,
        "success_rate": round(multi_success / samples, 4),
        "safety_rate": round(multi_success / samples, 4),
        "safe_outcome_rate": round(multi_success / samples, 4),
        "score_samples": len(multi_scores),
        "average_latency_ms": round(mean(multi_latencies), 2) if multi_latencies else 0.0,
        "p95_latency_ms": _p95(multi_latencies),
        "average_score": round(mean(multi_scores), 2) if multi_scores else 0.0,
        "throughput_runs_per_second": round(samples / (total_wall / 1000), 2) if total_wall else 0.0,
        "llm_calls": sum(item["multi_agent"]["llm_calls"] for item in all_results),
        "tool_calls": sum(item["multi_agent"]["tool_calls"] for item in all_results),
        "consistency": round(consistent / comparable, 4) if comparable else 0.0,
        "baseline": {
            "success_rate": round(baseline_success / samples, 4),
            "safety_rate": round(baseline_success / samples, 4),
            "safe_proposal_rate": round(baseline_success / samples, 4),
            "independent_verification": False,
            "average_latency_ms": round(mean(baseline_latencies), 2) if baseline_latencies else 0.0,
            "p95_latency_ms": _p95(baseline_latencies),
            "average_score": round(mean(baseline_scores), 2) if baseline_scores else 0.0,
            "llm_calls": sum(item["single_agent_greedy"]["llm_calls"] for item in all_results),
            "tool_calls": sum(item["single_agent_greedy"]["tool_calls"] for item in all_results),
        },
        "errors": errors,
        "_multi_latencies": multi_latencies,
        "_baseline_latencies": baseline_latencies,
        "_multi_scores": multi_scores,
        "_baseline_scores": baseline_scores,
    }


def run_perf_benchmark(
    db: Database,
    levels: Iterable[int] | None = None,
    repeat_count: int = 2,
    output_path: Path | str | None = None,
) -> dict:
    levels = tuple(int(item) for item in (levels or DEFAULT_CONCURRENCY_LEVELS))
    unsupported = [item for item in levels if item not in DEFAULT_CONCURRENCY_LEVELS]
    if unsupported:
        raise ValueError(f"不支持的并发档位：{unsupported}；允许 1/5/10/20")
    if repeat_count < 2:
        raise ValueError("repeat_count 至少为 2，才能计算结果一致性")
    contexts = _benchmark_contexts(db)
    results = [_measure_level(contexts, level, repeat_count) for level in levels]
    total_samples = max(sum(item["samples"] for item in results), 1)
    multi_success = sum(round(item["success_rate"] * item["samples"]) for item in results)
    baseline_success = sum(round(item["baseline"]["success_rate"] * item["samples"]) for item in results)
    multi_latencies = [value for item in results for value in item["_multi_latencies"]]
    baseline_latencies = [value for item in results for value in item["_baseline_latencies"]]
    multi_scores = [value for item in results for value in item["_multi_scores"]]
    baseline_scores = [value for item in results for value in item["_baseline_scores"]]
    public_results = [
        {key: value for key, value in item.items() if not key.startswith("_")}
        for item in results
    ]
    report = {
        "status": "ready",
        "report_version": "perf-benchmark-v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "runtime_mode": "deterministic-core",
        "runtime_note": "未启用 LLM；每个样本完整执行需求拆分、多轮方案生成、核验和交付包生成。",
        "database_engine": db.engine,
        "levels": list(levels),
        "repeat_count": repeat_count,
        "scenario_count": len(contexts),
        "results": public_results,
        "comparison": {
            "multi_agent": {
                "samples": total_samples,
                "success_rate": round(multi_success / total_samples, 4),
                "safety_rate": round(multi_success / total_samples, 4),
                "safe_outcome_rate": round(multi_success / total_samples, 4),
                "score_samples": len(multi_scores),
                "average_latency_ms": round(mean(multi_latencies), 2) if multi_latencies else 0.0,
                "p95_latency_ms": _p95(multi_latencies),
                "average_score": round(mean(multi_scores), 2) if multi_scores else 0.0,
                "llm_calls": sum(item["llm_calls"] for item in results),
                "tool_calls": sum(item["tool_calls"] for item in results),
            },
            "single_agent_greedy": {
                "samples": total_samples,
                "success_rate": round(baseline_success / total_samples, 4),
                "safety_rate": round(baseline_success / total_samples, 4),
                "safe_proposal_rate": round(baseline_success / total_samples, 4),
                "independent_verification": False,
                "average_latency_ms": round(mean(baseline_latencies), 2) if baseline_latencies else 0.0,
                "p95_latency_ms": _p95(baseline_latencies),
                "average_score": round(mean(baseline_scores), 2) if baseline_scores else 0.0,
                "llm_calls": sum(item["baseline"]["llm_calls"] for item in results),
                "tool_calls": sum(item["baseline"]["tool_calls"] for item in results),
            },
        },
        "limitations": [
            "默认模式基准，不计入 LLM Planner 网络延迟。",
            "并发为单进程 asyncio + to_thread 的任务级并发，不等同于多实例压测。",
            "多 Agent 安全率按可行交付、不可行转人工是否正确处置计算；单 Agent 安全率按其生成方案是否通过外部真值审计计算。",
            "平均得分只统计可行情景，避免不可行情景的处置结果扭曲方案质量对比。",
        ],
    }
    report["comparison"]["delta"] = {
        "success_rate": round(
            report["comparison"]["multi_agent"]["success_rate"]
            - report["comparison"]["single_agent_greedy"]["success_rate"], 4
        ),
        "average_latency_ms": round(
            report["comparison"]["multi_agent"]["average_latency_ms"]
            - report["comparison"]["single_agent_greedy"]["average_latency_ms"], 2
        ),
        "average_score": round(
            report["comparison"]["multi_agent"]["average_score"]
            - report["comparison"]["single_agent_greedy"]["average_score"], 2
        ),
    }
    target = Path(output_path or os.getenv("PERF_BENCHMARK_REPORT", DEFAULT_REPORT_PATH))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def load_latest_benchmark_report(path: Path | str | None = None) -> dict:
    target = Path(path or os.getenv("PERF_BENCHMARK_REPORT", DEFAULT_REPORT_PATH))
    if not target.exists():
        return {
            "status": "empty",
            "message": "暂无性能基准报告，请运行 python -m benchmark.run_perf_benchmark",
            "report_path": str(target),
        }
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "error", "message": f"性能基准报告读取失败：{exc}", "report_path": str(target)}
    return data if isinstance(data, dict) else {"status": "error", "message": "性能基准报告格式无效"}


def main() -> None:
    parser = argparse.ArgumentParser(description="急先锋端到端并发性能基准")
    parser.add_argument("--levels", default="1,5,10,20", help="并发档位，逗号分隔")
    parser.add_argument("--repeat", type=int, default=2, help="每个档位重复轮次，至少 2")
    parser.add_argument("--output", default=str(DEFAULT_REPORT_PATH), help="JSON 输出路径")
    args = parser.parse_args()
    db = Database()
    db.initialize()
    levels = [int(item.strip()) for item in args.levels.split(",") if item.strip()]
    report = run_perf_benchmark(db, levels=levels, repeat_count=args.repeat, output_path=args.output)
    print(json.dumps({
        "status": report["status"],
        "levels": report["levels"],
        "comparison": report["comparison"],
        "output": args.output,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
