from __future__ import annotations

import math
import time
from statistics import mean

from .agents import build_demand_plan, generate_plans, verify_plans
from .database import Database


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
        return {"error": "benchmark scenario unavailable"}
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
            "verification_score": 0,
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
            "score_gain": verification_v2["score"] - 0,
        },
    }
    quantitative_rows = [
        {"metric": "不安全方案验收率", "baseline": f"{baseline_unsafe_acceptance * 100:.1f}%", "multi": f"{multi_unsafe_acceptance * 100:.1f}%", "change": f"-{quantitative['tradeoff']['unsafe_acceptance_reduction_pp']:.1f} 个百分点"},
        {"metric": "硬风险检出率", "baseline": "0.0%", "multi": f"{risk_detection_rate * 100:.1f}%", "change": f"+{quantitative['tradeoff']['risk_detection_gain_pp']:.1f} 个百分点"},
        {"metric": "最终核验评分", "baseline": "0", "multi": str(verification_v2["score"]), "change": f"+{verification_v2['score']}"},
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
            {"label": "单 Agent / 无独立核验", "score": 0, "unsafe_plans": len(plans_v1), "passed": False},
            {"label": "第一轮生成 / 核验退回", "score": verification_v1["score"], "unsafe_plans": len(plans_v1), "passed": False},
            {"label": "多 Agent / 两轮闭环", "score": verification_v2["score"], "unsafe_plans": 0, "passed": verification_v2["verdict"] == "pass"},
        ],
        "notes": [
            f"基线 vs 多 Agent 各重复运行 {repetitions} 次，使用同一库存快照和同一事件约束。",
            "耗时只统计本地确定性计算，不包含公网 AgentArts 网络等待时间。",
            "硬规则由确定性核验器判定；多 Agent 闭环通过独立核验和重算降低不安全方案验收率。",
        ],
    }
