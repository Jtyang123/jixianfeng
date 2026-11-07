from pathlib import Path

from app.agents import build_delivery_package, build_demand_plan, generate_plans, normalize_agentarts_decision, verify_plans
from app.database import Database


def test_two_round_verification_loop(tmp_path: Path):
    db = Database(tmp_path / "engine.db", database_url="")
    db.initialize()
    scenario = db.get_scenario("rainstorm-ii")
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
    inventory = db.inventory_rows(db.latest_snapshot()["id"])
    demand = build_demand_plan(request, materials, db.destination_rows())
    warehouses = db.warehouse_rows()
    routes = db.route_rows()
    vehicles = db.vehicle_rows()

    plans_v1 = generate_plans(demand, inventory, warehouses, routes, vehicles, 1, scenario["closed_routes"])
    verification_v1 = verify_plans(plans_v1, demand, inventory, routes, vehicles, scenario["closed_routes"])
    assert verification_v1["verdict"] == "rework"
    assert any(code.startswith("ROAD-") for code in verification_v1["blocker_summary"])
    assert all(not report["approved"] for report in verification_v1["reports"])

    plans_v2 = generate_plans(demand, inventory, warehouses, routes, vehicles, 2, scenario["closed_routes"])
    metrics_by_strategy = {plan["strategy"]: plan["metrics"] for plan in plans_v2}
    assert len({tuple(sorted(metrics.items())) for metrics in metrics_by_strategy.values()}) == 3
    assert metrics_by_strategy["speed"]["max_eta_hours"] == min(metrics["max_eta_hours"] for metrics in metrics_by_strategy.values())
    assert metrics_by_strategy["cost"]["total_cost"] == min(metrics["total_cost"] for metrics in metrics_by_strategy.values())
    assert metrics_by_strategy["cost"]["max_eta_hours"] > metrics_by_strategy["speed"]["max_eta_hours"]
    assert all(plan.get("policy_note") for plan in plans_v2)
    verification_v2 = verify_plans(plans_v2, demand, inventory, routes, vehicles, scenario["closed_routes"])
    assert verification_v2["verdict"] == "pass"
    assert verification_v2["approved_plan_ids"]

    delivery = build_delivery_package(verification_v2, scenario, db.latest_snapshot(), {"speed": 0.38, "cost": 0.27, "risk": 0.35})
    assert delivery["status"] == "ready"
    assert delivery["recommended_plan"]["verification_score"] >= 80
    assert delivery["timeline"]

def test_agentarts_decision_is_bounded_to_valid_plans():
    plans = [
        {"plan_id": "PLAN-2-SPEED", "strategy": "speed"},
        {"plan_id": "PLAN-2-COST", "strategy": "cost"},
        {"plan_id": "PLAN-2-BALANCED", "strategy": "balanced"},
    ]
    decision = normalize_agentarts_decision({
        "recommended_plan_id": "PLAN-2-COST",
        "summary": "成本方案满足覆盖要求且总成本最低。",
    }, plans)
    assert decision["applied"] is True
    assert decision["recommended_plan_id"] == "PLAN-2-COST"
    assert decision["recommended_strategy"] == "cost"

    invalid = normalize_agentarts_decision({"recommended_plan_id": "PLAN-NOT-ALLOWED"}, plans)
    assert invalid["applied"] is False


def test_agentarts_recommendation_affects_delivery_selection():
    def report(plan_id, strategy, cost, eta):
        return {
            "plan_id": plan_id,
            "strategy": strategy,
            "name": strategy,
            "tag": strategy,
            "metrics": {"total_cost": cost, "max_eta_hours": eta, "coverage": 1.0, "vehicle_trips": 1},
            "verification": {"score": 100, "warnings": []},
            "approved": True,
            "groups": [],
            "assignments": [],
        }

    reports = [
        report("PLAN-2-SPEED", "speed", 1000, 1.0),
        report("PLAN-2-COST", "cost", 1100, 1.1),
        report("PLAN-2-BALANCED", "balanced", 1050, 1.05),
    ]
    decision = {
        "applied": True,
        "recommended_plan_id": "PLAN-2-COST",
        "recommended_strategy": "cost",
        "rationale": "AgentArts 选择成本方案。",
        "source": "agentarts",
    }
    delivery = build_delivery_package(
        {"reports": reports},
        {"name": "test scenario"},
        {"label": "snapshot", "uploaded_at": "2026-11-07"},
        {"speed": 0.38, "cost": 0.27, "risk": 0.35},
        agent_decision=decision,
    )
    assert delivery["recommended_plan"]["plan_id"] == "PLAN-2-COST"
    assert delivery["agent_decision"]["applied"] is True

