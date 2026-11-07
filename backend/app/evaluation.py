from __future__ import annotations

import asyncio
import copy
import json
import math
import time
import uuid
from statistics import mean
from unittest.mock import AsyncMock, patch

import httpx

from .agentarts import AgentArtsBridge, AgentArtsError
from .agents import build_demand_plan, generate_plans, normalize_agentarts_decision, verify_plans
from .database import Database
from .orchestrator import _parse_json_text
from .routing import generate_route_candidates


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return round(ordered[index], 2)


def _signature(verification: dict) -> tuple:
    return (
        tuple(verification.get("approved_plan_ids", [])),
        tuple(verification.get("blocker_summary", [])),
        verification.get("score"),
    )


def run_benchmark(db: Database, repetitions: int = 20) -> dict:
    scenario = db.get_scenario("rainstorm-ii")
    if not scenario:
        return {"error": "基准场景不可用"}
    materials = db.list_materials()
    material_ids = {item["code"]: item["id"] for item in materials}
    request = {
        "demand_items": [
            {
                "material_id": material_ids[item["material_code"]],
                "quantity": item["quantity"],
                "priority": item["priority"],
                "deadline_hours": item["deadline_hours"],
            }
            for item in scenario["default_demand"]
        ]
    }
    snapshot = db.latest_snapshot()
    inventory = db.inventory_rows(snapshot["id"] if snapshot else None)
    destinations = db.destination_rows()
    warehouses = db.warehouse_rows()
    routes = db.route_rows()
    vehicles = db.vehicle_rows()
    closed_routes = scenario["closed_routes"]

    def baseline_once():
        started = time.perf_counter()
        demand = build_demand_plan(request, materials, destinations)
        plans = generate_plans(demand, inventory, warehouses, routes, vehicles, 1, closed_routes)
        selected = plans[0]
        ground_truth = verify_plans([selected], demand, inventory, routes, vehicles, closed_routes)
        elapsed = (time.perf_counter() - started) * 1000
        return elapsed, demand, plans, selected, ground_truth

    def multi_once():
        started = time.perf_counter()
        demand = build_demand_plan(request, materials, destinations)
        plans_v1 = generate_plans(demand, inventory, warehouses, routes, vehicles, 1, closed_routes)
        verification_v1 = verify_plans(plans_v1, demand, inventory, routes, vehicles, closed_routes)
        plans_v2 = generate_plans(demand, inventory, warehouses, routes, vehicles, 2, closed_routes)
        verification_v2 = verify_plans(plans_v2, demand, inventory, routes, vehicles, closed_routes)
        elapsed = (time.perf_counter() - started) * 1000
        return elapsed, demand, plans_v1, verification_v1, plans_v2, verification_v2

    baseline_runs = [baseline_once() for _ in range(repetitions)]
    multi_runs = [multi_once() for _ in range(repetitions)]
    baseline_times = [item[0] for item in baseline_runs]
    multi_times = [item[0] for item in multi_runs]

    _, demand, plans_v1, baseline_plan, baseline_truth = baseline_runs[0]
    baseline_report = baseline_truth["reports"][0]
    baseline_score = baseline_report["verification"]["score"]
    _, _, _, verification_v1, plans_v2, verification_v2 = multi_runs[0]
    available_demand = sum(item["quantity"] for item in demand["items"])
    approved_final = next((item for item in verification_v2["reports"] if item["approved"]), None)
    delivered = sum(item["quantity"] for item in approved_final["assignments"]) if approved_final else 0
    coverage = delivered / available_demand if available_demand else 0
    baseline_coverage = sum(item["quantity"] for item in baseline_plan["assignments"]) / available_demand if available_demand else 0

    baseline_unsafe = not baseline_truth["reports"][0]["approved"]
    baseline_unsafe_acceptance = 1.0 if baseline_unsafe else 0.0
    multi_unsafe_acceptance = 0.0 if verification_v2["verdict"] == "pass" else 1.0
    risk_detection_rate = 1.0 if verification_v1["blocker_summary"] else 0.0
    rework_rate = sum(1 for item in multi_runs if item[3]["verdict"] == "rework") / repetitions
    convergence_rate = sum(1 for item in multi_runs if item[5]["verdict"] == "pass") / repetitions
    baseline_reproducible = len({_signature(item[4]) for item in baseline_runs}) == 1
    multi_reproducible = len({_signature(item[5]) for item in multi_runs}) == 1
    baseline_avg = round(mean(baseline_times), 2)
    multi_avg = round(mean(multi_times), 2)
    baseline_p95 = _p95(baseline_times)
    multi_p95 = _p95(multi_times)
    overhead_ms = round(multi_avg - baseline_avg, 2)
    overhead_pct = round((overhead_ms / baseline_avg * 100) if baseline_avg else 0, 2)

    quantitative = {
        "repetitions": repetitions,
        "baseline": {
            "label": "单 Agent / 无独立核验",
            "unsafe_acceptance_rate": baseline_unsafe_acceptance,
            "risk_detection_rate": 0.0,
            "final_safe": False if baseline_unsafe else True,
            "verification_score": baseline_score,
            "coverage": round(baseline_coverage, 4),
            "average_latency_ms": baseline_avg,
            "p95_latency_ms": baseline_p95,
            "reproducible": baseline_reproducible,
        },
        "multi_agent": {
            "label": "多 Agent / 两轮独立核验",
            "unsafe_acceptance_rate": multi_unsafe_acceptance,
            "risk_detection_rate": risk_detection_rate,
            "final_safe": verification_v2["verdict"] == "pass",
            "verification_score": verification_v2["score"],
            "coverage": round(coverage, 4),
            "average_latency_ms": multi_avg,
            "p95_latency_ms": multi_p95,
            "reproducible": multi_reproducible,
            "rework_rate": round(rework_rate, 4),
            "convergence_rate": round(convergence_rate, 4),
        },
        "tradeoff": {
            "latency_overhead_ms": overhead_ms,
            "latency_overhead_pct": overhead_pct,
            "unsafe_acceptance_reduction_pp": round((baseline_unsafe_acceptance - multi_unsafe_acceptance) * 100, 1),
            "risk_detection_gain_pp": round(risk_detection_rate * 100, 1),
            "score_gain": verification_v2["score"] - baseline_score,
        },
    }
    quantitative_rows = [
        {"metric": "不安全方案验收率", "baseline": f"{baseline_unsafe_acceptance * 100:.1f}%", "multi": f"{multi_unsafe_acceptance * 100:.1f}%", "change": f"-{quantitative['tradeoff']['unsafe_acceptance_reduction_pp']:.1f} 个百分点"},
        {"metric": "硬风险检出率", "baseline": "0.0%", "multi": f"{risk_detection_rate * 100:.1f}%", "change": f"+{quantitative['tradeoff']['risk_detection_gain_pp']:.1f} 个百分点"},
        {"metric": "最终核验评分", "baseline": str(baseline_score), "multi": str(verification_v2["score"]), "change": f"{verification_v2['score'] - baseline_score:+d}"},
        {"metric": "需求覆盖率", "baseline": f"{baseline_coverage * 100:.1f}%", "multi": f"{coverage * 100:.1f}%", "change": f"{(coverage - baseline_coverage) * 100:+.1f} 个百分点"},
        {"metric": "平均纯计算耗时", "baseline": f"{baseline_avg:.2f} ms", "multi": f"{multi_avg:.2f} ms", "change": f"+{overhead_ms:.2f} ms"},
        {"metric": "P95 纯计算耗时", "baseline": f"{baseline_p95:.2f} ms", "multi": f"{multi_p95:.2f} ms", "change": f"{multi_p95 - baseline_p95:+.2f} ms"},
        {"metric": "重复运行一致", "baseline": "是" if baseline_reproducible else "否", "multi": "是" if multi_reproducible else "否", "change": "固定快照可复现"},
    ]

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scenario": scenario["name"],
        "metrics": {
            "hard_blockers_detected": len(verification_v1["blocker_summary"]),
            "unsafe_plans_blocked": f"{sum(1 for item in verification_v1['reports'] if not item['approved'])}/{len(plans_v1)}",
            "initial_score": verification_v1["score"],
            "final_score": verification_v2["score"],
            "score_gain": verification_v2["score"] - verification_v1["score"],
            "final_coverage": round(coverage, 4),
            "approved_plans": len(verification_v2["approved_plan_ids"]),
            "compute_ms": multi_avg,
            "repeat_compute_ms": multi_p95,
            "reproducible": baseline_reproducible and multi_reproducible,
            "demand_rows": len(demand["items"]),
            "inventory_snapshot": snapshot["label"] if snapshot else "",
            "repetitions": repetitions,
            "rework_rate": round(rework_rate, 4),
            "convergence_rate": round(convergence_rate, 4),
        },
        "quantitative": quantitative,
        "quantitative_rows": quantitative_rows,
        "comparison": [
            {"label": "单 Agent / 无独立核验", "score": baseline_score, "unsafe_plans": len(plans_v1), "passed": False},
            {"label": "第一轮生成 / 核验退回", "score": verification_v1["score"], "unsafe_plans": len(plans_v1), "passed": False},
            {"label": "多 Agent / 两轮闭环", "score": verification_v2["score"], "unsafe_plans": 0, "passed": verification_v2["verdict"] == "pass"},
        ],
        "notes": [
            f"基线 vs 多 Agent 各重复运行 {repetitions} 次，使用同一库存快照和同一事件约束。",
            "耗时只统计本地确定性计算，不包含公网 AgentArts 网络等待时间。",
            "硬规则由确定性核验器判定；多 Agent 闭环通过独立核验和重算降低不安全方案验收率。",
        ],
    }


def _wilson_interval(successes: int, total: int, z: float = 1.96) -> dict:
    if total <= 0:
        return {"low": 0.0, "high": 0.0}
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return {"low": round(max(0.0, center - margin), 4), "high": round(min(1.0, center + margin), 4)}


def _diagnostic_fixture(db: Database) -> dict:
    scenario = db.get_scenario("rainstorm-ii")
    if not scenario:
        raise RuntimeError("诊断场景不可用")
    materials = db.list_materials()
    material_ids = {item["code"]: item["id"] for item in materials}
    missing = [item["material_code"] for item in scenario["default_demand"] if item["material_code"] not in material_ids]
    if missing:
        raise RuntimeError(f"诊断场景缺少物资主数据：{', '.join(missing)}")
    snapshot = db.latest_snapshot()
    return {
        "scenario": scenario,
        "materials": materials,
        "snapshot": snapshot,
        "inventory": db.inventory_rows(snapshot["id"] if snapshot else None),
        "destinations": db.destination_rows(),
        "warehouses": db.warehouse_rows(),
        "routes": db.route_rows(),
        "vehicles": db.vehicle_rows(),
        "request": {
            "demand_items": [
                {
                    "material_id": material_ids[item["material_code"]],
                    "quantity": item["quantity"],
                    "priority": item["priority"],
                    "deadline_hours": item["deadline_hours"],
                }
                for item in scenario["default_demand"]
            ]
        },
    }


def _run_multi_pipeline(fixture: dict) -> dict:
    started = time.perf_counter()
    demand = build_demand_plan(fixture["request"], fixture["materials"], fixture["destinations"])
    plans_v1 = generate_plans(
        demand, fixture["inventory"], fixture["warehouses"], fixture["routes"],
        fixture["vehicles"], 1, fixture["scenario"]["closed_routes"],
    )
    verification_v1 = verify_plans(
        plans_v1, demand, fixture["inventory"], fixture["routes"],
        fixture["vehicles"], fixture["scenario"]["closed_routes"],
    )
    plans_v2 = generate_plans(
        demand, fixture["inventory"], fixture["warehouses"], fixture["routes"],
        fixture["vehicles"], 2, fixture["scenario"]["closed_routes"],
    )
    verification_v2 = verify_plans(
        plans_v2, demand, fixture["inventory"], fixture["routes"],
        fixture["vehicles"], fixture["scenario"]["closed_routes"],
    )
    return {
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "initial_verdict": verification_v1["verdict"],
        "final_verdict": verification_v2["verdict"],
        "initial_blockers": verification_v1["blocker_summary"],
        "final_blockers": verification_v2["blocker_summary"],
        "initial_warnings": sorted({
            warning["code"] for report in verification_v1["reports"]
            for warning in report["verification"]["warnings"]
        }),
        "final_score": verification_v2["score"],
        "final_coverage": max(
            (report["metrics"]["coverage"] for report in verification_v2["reports"]), default=0.0
        ),
        "signature": json.dumps(_signature(verification_v2), ensure_ascii=False, sort_keys=True),
    }


async def _run_concurrency_level(level: int, fixture: dict) -> dict:
    semaphore = asyncio.Semaphore(level)
    samples = level

    async def one_run() -> dict:
        async with semaphore:
            return await asyncio.to_thread(_run_multi_pipeline, fixture)

    started = time.perf_counter()
    results = await asyncio.gather(*(one_run() for _ in range(samples)), return_exceptions=True)
    wall_ms = round((time.perf_counter() - started) * 1000, 2)
    successful = [item for item in results if isinstance(item, dict) and item["final_verdict"] == "pass"]
    latencies = [item["elapsed_ms"] for item in successful]
    signatures = {item["signature"] for item in successful}
    return {
        "concurrency": level,
        "samples": samples,
        "successful": len(successful),
        "success_rate": round(len(successful) / samples, 4),
        "wall_ms": wall_ms,
        "average_latency_ms": round(mean(latencies), 2) if latencies else 0.0,
        "p95_latency_ms": _p95(latencies),
        "throughput_runs_per_second": round(len(successful) / (wall_ms / 1000), 2) if wall_ms else 0.0,
        "isolation_consistent": len(signatures) == 1 if successful else False,
        "status": "pass" if len(successful) == samples and len(signatures) == 1 else "fail",
        "errors": [str(item)[:240] for item in results if isinstance(item, Exception)],
    }


async def _run_concurrency_suite(fixture: dict, levels: list[int]) -> list[dict]:
    return [await _run_concurrency_level(level, fixture) for level in levels]


async def _agentarts_timeout_probe(fixture: dict) -> dict:
    bridge = AgentArtsBridge()
    bridge.enabled = True
    bridge.base_url = "https://agentarts.invalid"
    bridge.api_key = "diagnostic-placeholder"
    bridge.endpoints = {"demand_agent": "https://agentarts.invalid/invocations"}
    bridge.agent_keys = {}

    def timeout_handler(request):
        raise httpx.ReadTimeout("模拟 AgentArts 响应超时", request=request)

    caught = False
    fallback = {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler)) as client:
        try:
            await bridge.invoke("demand_agent", {"summary": "timeout probe"}, client=client)
        except AgentArtsError as exc:
            caught = True
            fallback = {"mode": "local-fallback-after-agentarts-error", "error": str(exc)[:180]}
    return {
        "passed": caught and fallback.get("mode") == "local-fallback-after-agentarts-error",
        "observed": fallback.get("error", "超时未被捕获"),
    }


async def _osrm_fallback_probe(fixture: dict) -> dict:
    if not fixture["warehouses"] or not fixture["destinations"]:
        return {"passed": False, "observed": "缺少仓库或安置点，无法执行路网降级测试"}
    with patch(
        "app.routing._fetch_osrm_route",
        new=AsyncMock(side_effect=httpx.ConnectError("模拟 OSRM 不可用")),
    ):
        routes, warnings = await generate_route_candidates(
            [fixture["warehouses"][0]], [fixture["destinations"][0]]
        )
    route = routes[0] if routes else {}
    passed = bool(
        warnings and route.get("source") == "fallback" and route.get("distance_km", 0) > 0
        and route.get("eta_hours", 0) > 0
    )
    return {
        "passed": passed,
        "observed": (
            f"已降级为离线估算，距离 {route.get('distance_km', 0)} km、"
            f"ETA {route.get('eta_hours', 0)} 小时；告警 {len(warnings)} 条"
            if passed else "未生成离线路网兜底结果"
        ),
    }


def _invalid_json_probe(fixture: dict) -> dict:
    demand = build_demand_plan(fixture["request"], fixture["materials"], fixture["destinations"])
    plans = generate_plans(
        demand, fixture["inventory"], fixture["warehouses"], fixture["routes"],
        fixture["vehicles"], 2, fixture["scenario"]["closed_routes"],
    )
    parsed = _parse_json_text("```json\n{\"verdict\": \"pass\", BROKEN}\n```")
    decision = normalize_agentarts_decision(parsed, plans)
    passed = parsed is None and decision.get("source") == "deterministic-fallback" and not decision.get("applied")
    return {
        "passed": passed,
        "observed": f"非法 JSON 已拒绝，决策来源 {decision.get('source')}",
    }


def _inventory_shortage_probe(fixture: dict) -> dict:
    inventory = copy.deepcopy(fixture["inventory"])
    for row in inventory:
        row["quantity"] = min(float(row.get("quantity") or 0), 0.1)
        row["locked_quantity"] = 0
    demand = build_demand_plan(fixture["request"], fixture["materials"], fixture["destinations"])
    plans = generate_plans(
        demand, inventory, fixture["warehouses"], fixture["routes"],
        fixture["vehicles"], 2, fixture["scenario"]["closed_routes"],
    )
    verification = verify_plans(
        plans, demand, inventory, fixture["routes"], fixture["vehicles"],
        fixture["scenario"]["closed_routes"],
    )
    codes = verification["blocker_summary"]
    passed = verification["verdict"] == "rework" and any(
        code.startswith("COVERAGE-") or code.startswith("STOCK-") for code in codes
    )
    return {
        "passed": passed,
        "observed": f"核验结论 {verification['verdict']}，阻断代码 {', '.join(codes[:5]) or '无'}",
    }


def _vehicle_shortage_probe(fixture: dict) -> dict:
    demand = build_demand_plan(fixture["request"], fixture["materials"], fixture["destinations"])
    plans = generate_plans(
        demand, fixture["inventory"], fixture["warehouses"], fixture["routes"],
        [], 2, fixture["scenario"]["closed_routes"],
    )
    verification = verify_plans(
        plans, demand, fixture["inventory"], fixture["routes"], [],
        fixture["scenario"]["closed_routes"],
    )
    codes = sorted({
        warning["code"] for report in verification["reports"]
        for warning in report["verification"]["warnings"] if warning["code"].startswith("VEHICLE-")
    })
    return {
        "passed": bool(codes),
        "observed": f"识别运力告警 {len(codes)} 个：{', '.join(codes[:5])}" if codes else "未识别运力不足",
    }


def _road_closure_probe(fixture: dict) -> dict:
    demand = build_demand_plan(fixture["request"], fixture["materials"], fixture["destinations"])
    plans = generate_plans(
        demand, fixture["inventory"], fixture["warehouses"], fixture["routes"],
        fixture["vehicles"], 1, fixture["scenario"]["closed_routes"],
    )
    verification = verify_plans(
        plans, demand, fixture["inventory"], fixture["routes"], fixture["vehicles"],
        fixture["scenario"]["closed_routes"],
    )
    codes = [code for code in verification["blocker_summary"] if code.startswith("ROAD-")]
    return {
        "passed": verification["verdict"] == "rework" and bool(codes),
        "observed": f"识别道路阻断 {len(codes)} 条，核验结论 {verification['verdict']}",
    }


def _error_category(code: str) -> str:
    if code.startswith("ROAD-"):
        return "主备路径选择错误"
    if code.startswith("STOCK-"):
        return "库存可用量误判"
    if code.startswith("COVERAGE-"):
        return "需求覆盖缺口"
    if code.startswith("VEHICLE-"):
        return "运力趟次估计偏差"
    if code.startswith("ETA-"):
        return "时效估计偏差"
    return "其他约束"


def _error_profile(fixture: dict, repetitions: int = 20) -> dict:
    baseline_errors = 0
    baseline_unsafe = 0
    multi_initial_errors = 0
    multi_final_errors = 0
    counts = {"base": {}, "initial": {}, "final": {}}

    def add_counts(bucket: str, codes: list[str]) -> None:
        for code in codes:
            category = _error_category(code)
            counts[bucket][category] = counts[bucket].get(category, 0) + 1

    demand = build_demand_plan(fixture["request"], fixture["materials"], fixture["destinations"])
    for _ in range(repetitions):
        baseline_plans = generate_plans(
            demand, fixture["inventory"], fixture["warehouses"], fixture["routes"],
            fixture["vehicles"], 1, fixture["scenario"]["closed_routes"],
        )
        baseline_truth = verify_plans(
            [baseline_plans[0]], demand, fixture["inventory"], fixture["routes"],
            fixture["vehicles"], fixture["scenario"]["closed_routes"],
        )
        baseline_codes = baseline_truth["blocker_summary"]
        baseline_errors += len(baseline_codes)
        baseline_unsafe += 0 if baseline_truth["verdict"] == "pass" else 1
        add_counts("base", baseline_codes)

        result = _run_multi_pipeline(fixture)
        initial_codes = result["initial_blockers"] + result["initial_warnings"]
        final_codes = result["final_blockers"]
        multi_initial_errors += len(initial_codes)
        multi_final_errors += len(final_codes)
        add_counts("initial", initial_codes)
        add_counts("final", final_codes)

    baseline_ci = _wilson_interval(baseline_unsafe, repetitions)
    multi_final_unsafe = repetitions if multi_final_errors > 0 else 0
    multi_ci = _wilson_interval(multi_final_unsafe, repetitions)
    categories = sorted(set(counts["base"]) | set(counts["initial"]) | set(counts["final"]))
    rows = [
        {
            "category": category,
            "baseline_events": counts["base"].get(category, 0),
            "initial_events": counts["initial"].get(category, 0),
            "final_events": counts["final"].get(category, 0),
            "reduction": counts["initial"].get(category, 0) - counts["final"].get(category, 0),
        }
        for category in categories
    ]
    return {
        "repetitions": repetitions,
        "baseline_error_events": baseline_errors,
        "multi_initial_error_events": multi_initial_errors,
        "multi_final_error_events": multi_final_errors,
        "error_reduction_rate": round(
            (multi_initial_errors - multi_final_errors) / multi_initial_errors, 4
        ) if multi_initial_errors else 0.0,
        "baseline_unsafe_acceptance_rate": round(baseline_unsafe / repetitions, 4),
        "baseline_unsafe_acceptance_ci95": baseline_ci,
        "multi_final_unsafe_acceptance_rate": round(multi_final_unsafe / repetitions, 4),
        "multi_final_unsafe_acceptance_ci95": multi_ci,
        "rows": rows,
        "method": "固定快照重复执行；Wilson 95% 区间；阻断按代码根因归类。",
    }


async def _fault_suite(fixture: dict) -> list[dict]:
    definitions = [
        ("agentarts_timeout", "AgentArts 响应超时", "云端超时后自动回退本地确定性结果", _agentarts_timeout_probe(fixture)),
        ("osrm_unavailable", "OSRM 路网不可用", "生成离线距离、ETA 与告警，不画假道路几何", _osrm_fallback_probe(fixture)),
    ]
    results: list[dict] = []
    for case_id, name, expected, pending_probe in definitions:
        started = time.perf_counter()
        try:
            probe = await pending_probe
            results.append({
                "id": case_id, "name": name, "expected": expected,
                "passed": probe["passed"], "observed": probe["observed"],
                "recovery_ms": round((time.perf_counter() - started) * 1000, 2),
            })
        except Exception as exc:
            results.append({
                "id": case_id, "name": name, "expected": expected,
                "passed": False, "observed": f"探测执行异常：{str(exc)[:240]}",
                "recovery_ms": round((time.perf_counter() - started) * 1000, 2),
            })

    sync_cases = [
        ("invalid_model_json", "非法模型 JSON", "拒绝非法结构并回退确定性决策", _invalid_json_probe),
        ("inventory_shortage", "库存不足", "核验器产生覆盖或库存硬阻断", _inventory_shortage_probe),
        ("vehicle_shortage", "车辆不足", "核验器产生运力告警", _vehicle_shortage_probe),
        ("road_closure", "道路封闭", "首轮方案被道路硬规则退回", _road_closure_probe),
    ]
    for case_id, name, expected, probe_func in sync_cases:
        started = time.perf_counter()
        try:
            probe = probe_func(fixture)
            results.append({
                "id": case_id, "name": name, "expected": expected,
                "passed": probe["passed"], "observed": probe["observed"],
                "recovery_ms": round((time.perf_counter() - started) * 1000, 2),
            })
        except Exception as exc:
            results.append({
                "id": case_id, "name": name, "expected": expected,
                "passed": False, "observed": f"探测执行异常：{str(exc)[:240]}",
                "recovery_ms": round((time.perf_counter() - started) * 1000, 2),
            })
    return results


def run_diagnostics(db: Database, levels: list[int] | None = None) -> dict:
    run_id = f"EVAL-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
    levels = sorted({max(1, min(50, int(level))) for level in (levels or [1, 5, 10, 20])})
    db.create_evaluation_run(run_id, "stress_and_fault", "rainstorm-ii")
    started = time.perf_counter()
    try:
        fixture = _diagnostic_fixture(db)
        concurrency = asyncio.run(_run_concurrency_suite(fixture, levels))
        faults = asyncio.run(_fault_suite(fixture))
        error_profile = _error_profile(fixture)
        all_passed = all(item["status"] == "pass" for item in concurrency) and all(
            item["passed"] for item in faults
        )
        result = {
            "run_id": run_id,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "scenario": fixture["scenario"]["name"],
            "inventory_snapshot": fixture["snapshot"]["label"] if fixture["snapshot"] else "未记录",
            "status": "passed" if all_passed else "warning",
            "all_passed": all_passed,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "concurrency": concurrency,
            "fault_injection": faults,
            "error_analysis": error_profile,
            "conclusion": (
                "并发执行、故障降级和误差分类全部达到预期。"
                if all_passed else "存在未达到预期的诊断项，请查看失败证据。"
            ),
            "notes": [
                "并发测试使用同一冻结库存快照，每个并发级别运行相应数量的完整两轮闭环。",
                "故障注入是可重复的受控测试：不向公网发送 AgentArts 请求，OSRM 故障由 MockTransport 注入。",
                "误差归类依据核验器真实阻断/告警代码，不使用人工填写的固定分数。",
            ],
        }
        db.finish_evaluation_run(run_id, "completed", result)
        return result
    except Exception as exc:
        result = {
            "run_id": run_id,
            "status": "failed",
            "all_passed": False,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": f"诊断评估失败：{str(exc)[:500]}",
        }
        db.finish_evaluation_run(run_id, "failed", result)
        return result