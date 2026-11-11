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
DEFAULT_REPEAT_COUNT = 3
DEFAULT_REPORT_PATH = PROJECT_DIR / "data" / "benchmark_latest.json"


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)], 2)


def _empty_counter() -> dict:
    return {"tool_calls": 0, "llm_calls": 0, "tool_call_breakdown": {}}


def _count_call(counter: dict, name: str) -> None:
    counter["tool_calls"] = int(counter.get("tool_calls", 0)) + 1
    breakdown = counter.setdefault("tool_call_breakdown", {})
    breakdown[name] = int(breakdown.get(name, 0)) + 1


def _safe_outcome(feasible: bool, verdict: str) -> bool:
    return verdict == "pass" if feasible else verdict != "pass"


def _run_case(context: dict, round_index: int, case_index: int) -> dict:
    multi_counter = _empty_counter()
    multi_started = time.perf_counter()
    _count_call(multi_counter, "demand_plan")
    demand = build_demand_plan(context["request"], context["materials"], context["destinations"])
    final_plans, verification, rounds, _directives = _solve_adaptively(
        demand, context["inventory"], context["warehouses"], context["routes"],
        context["vehicles"], context["closed_routes"], call_counter=multi_counter,
    )
    delivery = None
    if verification.get("verdict") == "pass":
        _count_call(multi_counter, "delivery_package")
        delivery = build_delivery_package(
            verification, context["scenario"],
            {"id": "benchmark", "label": "benchmark", "uploaded_at": datetime.now().isoformat(timespec="seconds")},
            {"speed": 0.38, "cost": 0.27, "risk": 0.35},
        )
    multi_ms = (time.perf_counter() - multi_started) * 1000
    if multi_counter["tool_calls"] != sum(multi_counter["tool_call_breakdown"].values()):
        raise RuntimeError("多 Agent 工具调用计数与实际调用不一致")
    recommended = (delivery or {}).get("recommended_plan") or {}
    multi_key_fields = {
        "verdict": verification.get("verdict"),
        "score": verification.get("score"),
        "approved_plan_ids": verification.get("approved_plan_ids", []),
        "recommended_plan_id": recommended.get("plan_id"),
        "blockers": verification.get("blocker_summary", []),
    }

    baseline_counter = _empty_counter()
    baseline_started = time.perf_counter()
    _count_call(baseline_counter, "demand_plan")
    baseline_demand = build_demand_plan(context["request"], context["materials"], context["destinations"])
    _count_call(baseline_counter, "baseline_generate")
    baseline_plan = _generate_baseline_plan(
        baseline_demand, context["inventory"], context["warehouses"], context["routes"],
        context["vehicles"], context["closed_routes"], "nearest",
    )
    _count_call(baseline_counter, "baseline_audit_verify")
    baseline_verification = verify_plans(
        [baseline_plan], baseline_demand, context["inventory"], context["routes"],
        context["vehicles"], context["closed_routes"],
    )
    baseline_ms = (time.perf_counter() - baseline_started) * 1000
    if baseline_counter["tool_calls"] != sum(baseline_counter["tool_call_breakdown"].values()):
        raise RuntimeError("单 Agent 基线工具调用计数与实际调用不一致")
    baseline_scored_value = float(baseline_verification.get("score") or 0) if context["feasible"] else 0.0
    return {
        "scenario_id": context["id"],
        "case_key": f"{context['id']}:{case_index}",
        "round_index": round_index,
        "feasible": bool(context["feasible"]),
        "multi_agent": {
            "elapsed_ms": round(multi_ms, 2),
            "verdict": verification.get("verdict"),
            "score": verification.get("score"),
            "safe_outcome": _safe_outcome(bool(context["feasible"]), verification.get("verdict")),
            "rounds": len(rounds),
            "tool_calls": multi_counter["tool_calls"],
            "tool_call_breakdown": multi_counter["tool_call_breakdown"],
            "llm_calls": multi_counter["llm_calls"],
            "key_fields": multi_key_fields,
        },
        "single_agent_greedy": {
            "elapsed_ms": round(baseline_ms, 2),
            "verdict": baseline_verification.get("verdict"),
            "score": baseline_scored_value,
            "raw_audit_score": baseline_verification.get("score"),
            "safe_proposal": baseline_verification.get("verdict") == "pass",
            "independent_verification": False,
            "audit_note": "核验器仅作外部真值审计，不参与基线生成。",
            "tool_calls": baseline_counter["tool_calls"],
            "tool_call_breakdown": baseline_counter["tool_call_breakdown"],
            "llm_calls": baseline_counter["llm_calls"],
            "key_fields": {
                "verdict": baseline_verification.get("verdict"),
                "score": baseline_verification.get("score"),
                "approved_plan_ids": baseline_verification.get("approved_plan_ids", []),
                "blockers": baseline_verification.get("blocker_summary", []),
            },
        },
    }


def _fixed_samples(contexts: list[dict], repeat_count: int) -> list[tuple[int, int, dict]]:
    return [
        (round_index, case_index, context)
        for round_index in range(repeat_count)
        for case_index, context in enumerate(contexts)
    ]


async def _run_samples(samples: list[tuple[int, int, dict]], concurrency: int) -> tuple[list[dict], float, list[str]]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(round_index: int, case_index: int, context: dict) -> dict:
        async with semaphore:
            return await asyncio.to_thread(_run_case, context, round_index, case_index)

    started = time.perf_counter()
    results = await asyncio.gather(*(one(round_index, case_index, context) for round_index, case_index, context in samples), return_exceptions=True)
    wall_ms = round((time.perf_counter() - started) * 1000, 2)
    completed = [item for item in results if isinstance(item, dict)]
    errors = [str(item)[:240] for item in results if isinstance(item, Exception)]
    return completed, wall_ms, errors


def _aggregate_breakdown(items: list[dict]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        for key, value in item.get("tool_call_breakdown", {}).items():
            result[key] = result.get(key, 0) + int(value)
    return result


def _measure_level(contexts: list[dict], concurrency: int, repeat_count: int) -> dict:
    samples = _fixed_samples(contexts, repeat_count)
    warmup_results, warmup_wall_ms, warmup_errors = asyncio.run(_run_samples(samples, concurrency))
    timed_results, wall_ms, errors = asyncio.run(_run_samples(samples, concurrency))
    if len(warmup_results) != len(samples) or len(timed_results) != len(samples):
        raise RuntimeError("固定样本执行数量不完整")

    grouped: dict[str, list[dict]] = {}
    for item in timed_results:
        grouped.setdefault(item["case_key"], []).append(item)
    comparable = 0
    consistent = 0
    for rows in grouped.values():
        rows.sort(key=lambda item: item["round_index"])
        if not rows:
            continue
        baseline_fields = rows[0]["multi_agent"]["key_fields"]
        for row in rows[1:]:
            comparable += 1
            consistent += int(row["multi_agent"]["key_fields"] == baseline_fields)

    multi_rows = [item["multi_agent"] for item in timed_results]
    baseline_rows = [item["single_agent_greedy"] for item in timed_results]
    multi_latencies = [item["elapsed_ms"] for item in multi_rows]
    baseline_latencies = [item["elapsed_ms"] for item in baseline_rows]
    multi_scores = [float(item["score"] or 0) for item in multi_rows]
    baseline_scores = [float(item["score"] or 0) for item in baseline_rows]
    samples_count = len(timed_results)
    multi_safe = sum(1 for item in multi_rows if item["safe_outcome"])
    baseline_safe = sum(1 for item in baseline_rows if item["safe_proposal"])
    return {
        "concurrency": concurrency,
        "samples": samples_count,
        "repeat_count": repeat_count,
        "warmup_samples": len(warmup_results),
        "warmup_wall_ms": warmup_wall_ms,
        "wall_ms": wall_ms,
        "throughput_runs_per_second": round(samples_count / (wall_ms / 1000), 2) if wall_ms else 0.0,
        "safe_outcome_rate": round(multi_safe / max(samples_count, 1), 4),
        "average_latency_ms": round(mean(multi_latencies), 2) if multi_latencies else 0.0,
        "p95_latency_ms": _p95(multi_latencies),
        "average_score": round(mean(multi_scores), 2) if multi_scores else 0.0,
        "llm_calls": sum(item["llm_calls"] for item in multi_rows),
        "tool_calls": sum(item["tool_calls"] for item in multi_rows),
        "tool_call_breakdown": _aggregate_breakdown(multi_rows),
        "consistency": round(consistent / comparable, 4) if comparable else 0.0,
        "baseline": {
            "safe_proposal_rate": round(baseline_safe / max(samples_count, 1), 4),
            "independent_verification": False,
            "average_latency_ms": round(mean(baseline_latencies), 2) if baseline_latencies else 0.0,
            "p95_latency_ms": _p95(baseline_latencies),
            "average_score": round(mean(baseline_scores), 2) if baseline_scores else 0.0,
            "llm_calls": sum(item["llm_calls"] for item in baseline_rows),
            "tool_calls": sum(item["tool_calls"] for item in baseline_rows),
            "tool_call_breakdown": _aggregate_breakdown(baseline_rows),
        },
        "cases": timed_results,
        "errors": warmup_errors + errors,
        "_multi_latencies": multi_latencies,
        "_baseline_latencies": baseline_latencies,
        "_multi_scores": multi_scores,
        "_baseline_scores": baseline_scores,
    }


def run_perf_benchmark(
    db: Database,
    levels: Iterable[int] | None = None,
    repeat_count: int = DEFAULT_REPEAT_COUNT,
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
    fixed_sample_count = len(contexts) * repeat_count
    if any(item["samples"] != fixed_sample_count for item in results):
        raise RuntimeError("不同并发档位使用了不同的固定样本量")

    multi_latencies = [value for item in results for value in item["_multi_latencies"]]
    baseline_latencies = [value for item in results for value in item["_baseline_latencies"]]
    multi_scores = [value for item in results for value in item["_multi_scores"]]
    baseline_scores = [value for item in results for value in item["_baseline_scores"]]
    total_samples = sum(item["samples"] for item in results)
    multi_safe = sum(round(item["safe_outcome_rate"] * item["samples"]) for item in results)
    baseline_safe = sum(round(item["baseline"]["safe_proposal_rate"] * item["samples"]) for item in results)
    public_results = [
        {key: value for key, value in item.items() if not key.startswith("_")}
        for item in results
    ]
    report = {
        "status": "ready",
        "report_version": "perf-benchmark-v2",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "runtime_mode": "deterministic-core",
        "runtime_note": "未启用 LLM；每个样本完整执行需求拆分、多轮方案生成、核验和交付包生成。",
        "database_engine": db.engine,
        "levels": list(levels),
        "repeat_count": repeat_count,
        "scenario_count": len(contexts),
        "fixed_samples_per_level": fixed_sample_count,
        "warmup_policy": "每个并发档位先执行同一组固定样本一轮并丢弃，再开始计时。",
        "results": public_results,
        "comparison": {
            "multi_agent": {
                "samples": total_samples,
                "safe_outcome_rate": round(multi_safe / max(total_samples, 1), 4),
                "average_latency_ms": round(mean(multi_latencies), 2) if multi_latencies else 0.0,
                "p95_latency_ms": _p95(multi_latencies),
                "average_score": round(mean(multi_scores), 2) if multi_scores else 0.0,
                "llm_calls": sum(item["llm_calls"] for item in results),
                "tool_calls": sum(item["tool_calls"] for item in results),
                "tool_call_breakdown": _aggregate_breakdown([
                    row["multi_agent"] for item in results for row in item["cases"]
                ]),
            },
            "single_agent_greedy": {
                "samples": total_samples,
                "safe_proposal_rate": round(baseline_safe / max(total_samples, 1), 4),
                "independent_verification": False,
                "average_latency_ms": round(mean(baseline_latencies), 2) if baseline_latencies else 0.0,
                "p95_latency_ms": _p95(baseline_latencies),
                "average_score": round(mean(baseline_scores), 2) if baseline_scores else 0.0,
                "llm_calls": sum(item["baseline"]["llm_calls"] for item in results),
                "tool_calls": sum(item["baseline"]["tool_calls"] for item in results),
                "tool_call_breakdown": _aggregate_breakdown([
                    row["single_agent_greedy"] for item in results for row in item["cases"]
                ]),
            },
        },
        "limitations": [
            "默认模式基准，不计入 LLM Planner 网络延迟。",
            "并发为单进程 asyncio + to_thread 的任务级并发，不等同于多实例压测。",
            "safe_outcome_rate 表示错误方案未流出：可行场景必须通过核验，不可行场景必须被拦截。",
            "单 Agent 基线使用独立贪心生成器和外部真值审计；不可行场景按 0 分计入平均得分。",
        ],
    }
    report["comparison"]["delta"] = {
        "safe_outcome_rate": round(
            report["comparison"]["multi_agent"]["safe_outcome_rate"]
            - report["comparison"]["single_agent_greedy"]["safe_proposal_rate"], 4
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
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT_COUNT, help="固定样本重复轮次，至少 2")
    parser.add_argument("--output", default=str(DEFAULT_REPORT_PATH), help="JSON 输出路径")
    args = parser.parse_args()
    db = Database()
    db.initialize()
    levels = [int(item.strip()) for item in args.levels.split(",") if item.strip()]
    report = run_perf_benchmark(db, levels=levels, repeat_count=args.repeat, output_path=args.output)
    print(json.dumps({
        "status": report["status"],
        "levels": report["levels"],
        "fixed_samples_per_level": report["fixed_samples_per_level"],
        "comparison": report["comparison"],
        "output": args.output,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
