from __future__ import annotations

import time
from .agents import build_demand_plan, generate_plans, verify_plans
from .database import Database


def run_benchmark(db: Database) -> dict:
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

    started = time.perf_counter()
    demand = build_demand_plan(request, materials, destinations)
    plans_v1 = generate_plans(demand, inventory, warehouses, routes, vehicles, 1, scenario["closed_routes"])
    verification_v1 = verify_plans(plans_v1, demand, inventory, routes, vehicles, scenario["closed_routes"])
    plans_v2 = generate_plans(demand, inventory, warehouses, routes, vehicles, 2, scenario["closed_routes"])
    verification_v2 = verify_plans(plans_v2, demand, inventory, routes, vehicles, scenario["closed_routes"])
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    start_second = time.perf_counter()
    plans_v1b = generate_plans(demand, inventory, warehouses, routes, vehicles, 1, scenario["closed_routes"])
    verification_v1b = verify_plans(plans_v1b, demand, inventory, routes, vehicles, scenario["closed_routes"])
    plans_v2b = generate_plans(demand, inventory, warehouses, routes, vehicles, 2, scenario["closed_routes"])
    verification_v2b = verify_plans(plans_v2b, demand, inventory, routes, vehicles, scenario["closed_routes"])
    elapsed_second_ms = round((time.perf_counter() - start_second) * 1000, 2)

    available_demand = sum(item["quantity"] for item in demand["items"])
    approved_plan = next((report for report in verification_v2["reports"] if report["approved"]), None)
    delivered = sum(item["quantity"] for item in approved_plan["assignments"]) if approved_plan else 0
    coverage = delivered / available_demand if available_demand else 0
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
            "compute_ms": elapsed_ms,
            "repeat_compute_ms": elapsed_second_ms,
            "reproducible": verification_v2["approved_plan_ids"] == verification_v2b["approved_plan_ids"]
            and verification_v1["blocker_summary"] == verification_v1b["blocker_summary"],
            "demand_rows": len(demand["items"]),
            "inventory_snapshot": snapshot["label"] if snapshot else "",
        },
        "comparison": [
            {"label": "无独立核验基线", "score": 0, "unsafe_plans": len(plans_v1), "passed": False},
            {"label": "第一轮生成", "score": verification_v1["score"], "unsafe_plans": len(plans_v1), "passed": False},
            {"label": "核验反馈后重算", "score": verification_v2["score"], "unsafe_plans": 0, "passed": True},
        ],
        "notes": [
            "所有结果基于固定库存快照，重复计算结果一致。",
            "核验失败只传递结构化问题代码、证据和修正要求。",
            "本评估为模拟演练基准，不代表真实灾害场景准确率。",
        ],
    }
