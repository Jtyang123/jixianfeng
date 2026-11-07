from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

STRATEGIES = [
    {
        "id": "speed", "name": "极速直送", "tag": "时效优先",
        "weights": {"eta": 0.78, "cost": 0.1, "risk": 0.12},
        "cost_factor": 1.08, "consolidation_delay_hours": 0.0,
        "policy_note": "并行分仓直送，不等待集货；含 8% 加急服务费。",
    },
    {
        "id": "cost", "name": "成本集约", "tag": "成本优先",
        "weights": {"eta": 0.12, "cost": 0.78, "risk": 0.1},
        "cost_factor": 0.94, "consolidation_delay_hours": 0.35,
        "policy_note": "合并批次、减少空载；增加约 0.35h 集货等待，运输费按 94% 计。",
    },
    {
        "id": "balanced", "name": "韧性均衡", "tag": "均衡推荐",
        "weights": {"eta": 0.38, "cost": 0.25, "risk": 0.37},
        "cost_factor": 0.98, "consolidation_delay_hours": 0.15,
        "policy_note": "在时效、成本和库存韧性之间折中；增加约 0.15h 集货等待。",
    },
]


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_demand_plan(request: dict, materials: list[dict], destination_rows: list[dict]) -> dict:
    material_map = {item["id"]: item for item in materials}
    total_population = sum(max(1, int(dest["population"])) for dest in destination_rows)
    demand_items: list[dict] = []
    assumptions: list[str] = []

    for raw in request["demand_items"]:
        material = material_map.get(int(raw["material_id"]))
        if not material:
            continue
        total_quantity = _num(raw["quantity"])
        remaining = total_quantity
        for index, dest in enumerate(destination_rows):
            if index == len(destination_rows) - 1:
                quantity = round(max(0, remaining), 2)
            else:
                quantity = round(total_quantity * int(dest["population"]) / total_population, 2)
                remaining -= quantity
            demand_items.append(
                {
                    "material_id": material["id"],
                    "material_code": material["code"],
                    "material_name": material["name"],
                    "base_unit": material["base_unit"],
                    "destination_id": dest["id"],
                    "destination_name": dest["name"],
                    "quantity": quantity,
                    "priority": raw.get("priority", material["default_priority"]),
                    "deadline_hours": _num(raw.get("deadline_hours"), 12),
                    "unit_weight_kg": _num(material["unit_weight_kg"], 0.5),
                }
            )

    for dest in destination_rows:
        assumptions.append(f"{dest['name']}按安置人口 {dest['population']} 人分配需求。")
    assumptions.append("需求数量已标准化为物资基本单位，库存与方案使用同一口径。")
    return {
        "agent": "demand_agent",
        "summary": f"已将 {len(request['demand_items'])} 类物资拆分为 {len(demand_items)} 条安置点需求。",
        "items": demand_items,
        "assumptions": assumptions,
        "evidence_refs": ["需求分配规则：按安置人口比例", "物资主数据：单位与单件重量"],
        "confidence": 0.92,
    }


def _available_inventory(inventory: list[dict]) -> dict[tuple[str, int], float]:
    stock: dict[tuple[str, int], float] = defaultdict(float)
    today = datetime.now().date().isoformat()
    for row in inventory:
        if row.get("status") != "available":
            continue
        if row.get("expiry_date") and row["expiry_date"] < today:
            continue
        qty = max(0.0, _num(row.get("quantity")) - _num(row.get("locked_quantity")))
        stock[(row["warehouse_id"], int(row["material_id"]))] += qty
    return stock


def _select_route(
    warehouse_id: str,
    destination_id: str,
    routes: list[dict],
    strategy: dict,
    ignore_closures: bool,
    closed_routes: set[str],
) -> dict | None:
    candidates = [
        route for route in routes
        if route["warehouse_id"] == warehouse_id
        and route["destination_id"] == destination_id
        and route.get("status") == "open"
        and (ignore_closures or route["id"] not in closed_routes)
    ]
    if not candidates:
        return None
    if ignore_closures and closed_routes:
        primary_candidates = [route for route in candidates if route.get("is_primary")]
        if primary_candidates:
            candidates = primary_candidates
    weights = strategy["weights"]
    etas = [_num(route["eta_hours"]) for route in candidates]
    costs = [_num(route["cost"]) for route in candidates]
    min_eta, max_eta = min(etas), max(etas)
    min_cost, max_cost = min(costs), max(costs)

    def normalize(value: float, low: float, high: float) -> float:
        if high - low <= 0.000001:
            return 0.0
        return (value - low) / (high - low)

    def score(route: dict) -> float:
        eta_score = normalize(_num(route["eta_hours"]), min_eta, max_eta)
        cost_score = normalize(_num(route["cost"]), min_cost, max_cost)
        risk_score = 0.1 if route.get("is_primary") else 0.65
        return (
            eta_score * weights["eta"]
            + cost_score * weights["cost"]
            + risk_score * weights["risk"]
        )

    return min(candidates, key=score)

def generate_plans(
    demand_plan: dict,
    inventory: list[dict],
    warehouses: list[dict],
    routes: list[dict],
    vehicles: list[dict],
    attempt: int,
    closed_routes: list[str],
) -> list[dict]:
    stock = _available_inventory(inventory)
    warehouse_map = {warehouse["id"]: warehouse for warehouse in warehouses}
    closed_set = set(closed_routes)
    ignore_closures = attempt == 1
    vehicle_by_warehouse: dict[str, list[dict]] = defaultdict(list)
    for vehicle in vehicles:
        if vehicle.get("status") == "available":
            vehicle_by_warehouse[vehicle["warehouse_id"]].append(vehicle)

    plans: list[dict] = []
    for strategy in STRATEGIES:
        used: dict[tuple[str, int], float] = defaultdict(float)
        warehouse_dispatches: dict[str, int] = defaultdict(int)
        route_cache: dict[tuple[str, str], dict | None] = {}
        assignments: list[dict] = []
        unmet: list[dict] = []

        for demand in demand_plan["items"]:
            remaining = _num(demand["quantity"])
            raw_candidates: list[dict] = []
            for warehouse in warehouses:
                wh_id = warehouse["id"]
                key = (wh_id, demand["material_id"])
                available = max(0.0, stock.get(key, 0) - used[key])
                if available <= 0.001:
                    continue
                pair = (wh_id, demand["destination_id"])
                if pair not in route_cache:
                    route_cache[pair] = _select_route(
                        wh_id, demand["destination_id"], routes, strategy,
                        ignore_closures, closed_set,
                    )
                route = route_cache[pair]
                if not route:
                    continue
                take_limit = remaining
                if strategy["id"] == "speed" and remaining > 10:
                    take_limit = max(1.0, remaining * 0.7)
                take = min(remaining, available, take_limit)
                weight_kg = take * _num(demand["unit_weight_kg"])
                warehouse_vehicles = vehicle_by_warehouse.get(wh_id, [])
                capacity = max([_num(vehicle["capacity_kg"]) for vehicle in warehouse_vehicles] or [4200])
                trips = max(1, math.ceil(weight_kg / max(capacity, 1)))
                trip_cost = _num(route["cost"]) * trips
                raw_candidates.append({
                    "warehouse": warehouse,
                    "route": route,
                    "available": available,
                    "take": take,
                    "eta": _num(route["eta_hours"]),
                    "trip_cost": trip_cost,
                    "unit_cost": trip_cost / max(take, 1.0),
                    "split": 0.0 if take + 0.001 >= remaining else 1.0,
                    "new_warehouse": 0.0 if warehouse_dispatches[wh_id] else 1.0,
                    "load_pressure": used[key] / max(stock.get(key, 0), 1.0),
                    "risk": 0.1 if route.get("is_primary") else 0.65,
                })

            candidates: list[tuple[float, dict, dict]] = []
            if raw_candidates:
                eta_values = [item["eta"] for item in raw_candidates]
                cost_values = [item["unit_cost"] for item in raw_candidates]
                dispatch_cost_values = [item["trip_cost"] for item in raw_candidates]
                min_eta, max_eta = min(eta_values), max(eta_values)
                min_cost, max_cost = min(cost_values), max(cost_values)
                min_dispatch, max_dispatch = min(dispatch_cost_values), max(dispatch_cost_values)

                def norm(value: float, low: float, high: float) -> float:
                    if high - low <= 0.000001:
                        return 0.0
                    return (value - low) / (high - low)

                for item in raw_candidates:
                    eta_score = norm(item["eta"], min_eta, max_eta)
                    cost_score = norm(item["unit_cost"], min_cost, max_cost)
                    dispatch_score = norm(item["trip_cost"], min_dispatch, max_dispatch)
                    if strategy["id"] == "speed":
                        candidate_score = eta_score * 0.72 + cost_score * 0.12 + item["risk"] * 0.06 + item["split"] * 0.04
                    elif strategy["id"] == "cost":
                        candidate_score = eta_score * 0.03 + dispatch_score * 0.92 + item["risk"] * 0.03 + item["split"] * 0.0 + item["new_warehouse"] * 0.0
                    else:
                        candidate_score = eta_score * 0.34 + cost_score * 0.26 + item["risk"] * 0.24 + item["split"] * 0.08 + item["load_pressure"] * 0.08
                    candidates.append((candidate_score, item["warehouse"], item["route"]))

            candidates.sort(key=lambda item: item[0])
            for _, warehouse, route in candidates:
                if remaining <= 0.001:
                    break
                key = (warehouse["id"], demand["material_id"])
                available = max(0.0, stock.get(key, 0) - used[key])
                take = min(remaining, available)
                if take <= 0:
                    continue
                used[key] += take
                remaining -= take
                warehouse_dispatches[warehouse["id"]] += 1
                assignments.append(
                    {
                        "warehouse_id": warehouse["id"],
                        "warehouse_name": warehouse["name"],
                        "destination_id": demand["destination_id"],
                        "destination_name": demand["destination_name"],
                        "material_id": demand["material_id"],
                        "material_code": demand["material_code"],
                        "material_name": demand["material_name"],
                        "base_unit": demand["base_unit"],
                        "quantity": round(take, 2),
                        "priority": demand["priority"],
                        "deadline_hours": demand["deadline_hours"],
                        "route_id": route["id"],
                        "route_name": route["road_name"],
                        "route_eta_hours": route["eta_hours"],
                        "route_cost": route["cost"],
                        "is_primary_route": bool(route["is_primary"]),
                        "unit_weight_kg": demand["unit_weight_kg"],
                    }
                )
            if remaining > 0.001:
                unmet.append(
                    {
                        "material_id": demand["material_id"],
                        "material_name": demand["material_name"],
                        "destination_id": demand["destination_id"],
                        "destination_name": demand["destination_name"],
                        "quantity": round(remaining, 2),
                        "unit": demand["base_unit"],
                    }
                )

        grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for assignment in assignments:
            grouped[(assignment["warehouse_id"], assignment["destination_id"])].append(assignment)

        group_outputs: list[dict] = []
        total_cost = 0.0
        total_trips = 0
        for (wh_id, dst_id), items in grouped.items():
            weight = sum(_num(item["quantity"]) * _num(item["unit_weight_kg"]) for item in items)
            available_vehicles = vehicle_by_warehouse.get(wh_id, [])
            capacity = max([_num(vehicle["capacity_kg"]) for vehicle in available_vehicles] or [4200])
            trips = max(1, math.ceil(weight / capacity))
            route = next(route for route in routes if route["id"] == items[0]["route_id"])
            effective_eta = round(_num(route["eta_hours"]) + _num(strategy["consolidation_delay_hours"]), 2)
            group_cost = round(_num(route["cost"]) * trips * _num(strategy["cost_factor"], 1.0), 2)
            total_cost += group_cost
            total_trips += trips
            group_id = f"G-{strategy['id']}-{wh_id}-{dst_id}"
            group_outputs.append(
                {
                    "group_id": group_id,
                    "warehouse_id": wh_id,
                    "warehouse_name": warehouse_map[wh_id]["name"],
                    "destination_id": dst_id,
                    "destination_name": items[0]["destination_name"],
                    "route_id": route["id"],
                    "route_name": route["road_name"],
                    "eta_hours": effective_eta,
                    "base_eta_hours": route["eta_hours"],
                    "policy_delay_hours": _num(strategy["consolidation_delay_hours"]),
                    "weight_kg": round(weight, 2),
                    "vehicle_capacity_kg": capacity,
                    "available_vehicles": len(available_vehicles),
                    "vehicle_trips": trips,
                    "cost": group_cost,
                }
            )
            for item in items:
                item["group_id"] = group_id
                item["group_cost"] = group_cost
                item["group_vehicle_trips"] = trips
                item["route_eta_hours"] = effective_eta
                item["base_route_eta_hours"] = route["eta_hours"]
                item["route_cost"] = round(_num(route["cost"]) * _num(strategy["cost_factor"], 1.0), 2)

        total_demand = sum(_num(item["quantity"]) for item in demand_plan["items"])
        delivered = sum(_num(item["quantity"]) for item in assignments)
        coverage = delivered / total_demand if total_demand else 0
        max_eta = max([_num(item["route_eta_hours"]) for item in assignments] or [0])
        plans.append(
            {
                "plan_id": f"PLAN-{attempt}-{strategy['id'].upper()}",
                "name": strategy["name"],
                "tag": strategy["tag"],
                "strategy": strategy["id"],
                "policy_note": strategy["policy_note"],
                "cost_factor": strategy["cost_factor"],
                "consolidation_delay_hours": strategy["consolidation_delay_hours"],
                "attempt": attempt,
                "assignments": assignments,
                "groups": group_outputs,
                "unmet": unmet,
                "metrics": {
                    "total_cost": round(total_cost, 2),
                    "max_eta_hours": round(max_eta, 2),
                    "coverage": round(coverage, 4),
                    "vehicle_trips": total_trips,
                    "warehouse_count": len({item["warehouse_id"] for item in assignments}),
                    "assignment_count": len(assignments),
                    "unmet_items": len(unmet),
                },
            }
        )
    return plans

def verify_plans(
    plans: list[dict],
    demand_plan: dict,
    inventory: list[dict],
    routes: list[dict],
    vehicles: list[dict],
    closed_routes: list[str],
) -> dict:
    stock = _available_inventory(inventory)
    route_map = {route["id"]: route for route in routes}
    closed_set = set(closed_routes)
    vehicle_count: dict[str, int] = defaultdict(int)
    for vehicle in vehicles:
        if vehicle.get("status") == "available":
            vehicle_count[vehicle["warehouse_id"]] += 1
    demand_map = {
        (item["material_id"], item["destination_id"]): item for item in demand_plan["items"]
    }
    reports: list[dict] = []

    for plan in plans:
        blockers: list[dict] = []
        warnings: list[dict] = []
        checks: list[dict] = []
        used: dict[tuple[str, int], float] = defaultdict(float)
        seen_blockers: set[str] = set()

        for assignment in plan["assignments"]:
            route = route_map.get(assignment["route_id"])
            key = (assignment["warehouse_id"], assignment["material_id"])
            used[key] += _num(assignment["quantity"])
            if used[key] > stock.get(key, 0) + 0.001:
                code = f"STOCK-{assignment['warehouse_id']}-{assignment['material_id']}"
                if code not in seen_blockers:
                    blockers.append({
                        "code": code,
                        "type": "库存",
                        "message": f"{assignment['warehouse_name']} 的 {assignment['material_name']} 分配量超过可用库存。",
                        "evidence": f"库存快照可用量 {stock.get(key, 0):.0f} {assignment['base_unit']}",
                    })
                    seen_blockers.add(code)

            if assignment["route_id"] in closed_set or (route and route.get("status") == "blocked"):
                code = f"ROAD-{assignment['route_id']}"
                if code not in seen_blockers:
                    blockers.append({
                        "code": code,
                        "type": "道路",
                        "message": f"{assignment['route_name']} 已被事件标记为不可通行。",
                        "evidence": f"事件封闭路线 {assignment['route_id']}",
                    })
                    seen_blockers.add(code)

            demand = demand_map.get((assignment["material_id"], assignment["destination_id"]))
            if demand and _num(assignment["route_eta_hours"]) > _num(demand["deadline_hours"]):
                warnings.append({
                    "code": f"ETA-{assignment['route_id']}",
                    "type": "时效",
                    "message": f"{assignment['destination_name']} 的 {assignment['material_name']} 预计超时。",
                    "evidence": f"ETA {assignment['route_eta_hours']:.2f}h / 要求 {demand['deadline_hours']:.0f}h",
                })

            if assignment["group_vehicle_trips"] > vehicle_count.get(assignment["warehouse_id"], 0):
                warnings.append({
                    "code": f"VEHICLE-{assignment['warehouse_id']}",
                    "type": "运力",
                    "message": f"{assignment['warehouse_name']} 所需趟次超过可用车辆数。",
                    "evidence": f"需要 {assignment['group_vehicle_trips']} 趟 / 可用 {vehicle_count.get(assignment['warehouse_id'], 0)} 辆",
                })

        coverage = _num(plan["metrics"]["coverage"])
        if coverage < 0.99:
            blockers.append({
                "code": "COVERAGE-SHORTAGE",
                "type": "缺口",
                "message": f"方案仅覆盖 {coverage * 100:.1f}% 的需求。",
                "evidence": f"未满足条目 {len(plan['unmet'])} 条",
            })

        checks.extend([
            {"name": "需求覆盖", "status": "pass" if coverage >= 0.99 else "fail", "value": f"{coverage * 100:.1f}%"},
            {"name": "道路通行", "status": "fail" if any(item["type"] == "道路" for item in blockers) else "pass"},
            {"name": "库存约束", "status": "fail" if any(item["type"] == "库存" for item in blockers) else "pass"},
            {"name": "时效约束", "status": "warn" if warnings else "pass"},
        ])
        score = max(0, round(100 - len(blockers) * 28 - len(warnings) * 6 - (1 - coverage) * 45))
        passed = not blockers and score >= 80 and coverage >= 0.99
        reports.append({
            **plan,
            "verification": {
                "verdict": "pass" if passed else "rework",
                "score": score,
                "blockers": blockers,
                "warnings": warnings,
                "checks": checks,
            },
            "approved": passed,
        })

    approved = [report for report in reports if report["approved"]]
    return {
        "agent": "verifier_agent",
        "verdict": "pass" if approved else "rework",
        "score": max([report["verification"]["score"] for report in reports] or [0]),
        "approved_plan_ids": [report["plan_id"] for report in approved],
        "blocker_summary": sorted({item["code"] for report in reports for item in report["verification"]["blockers"]}),
        "required_fixes": sorted({item["message"] for report in reports for item in report["verification"]["blockers"]}),
        "reports": reports,
        "evidence_refs": ["事件道路封闭清单", "库存快照", "运力与路线规则", "需求截止时间"],
    }

def normalize_agentarts_decision(parsed: dict | None, plans: list[dict]) -> dict:
    allowed = {str(plan.get("plan_id")): str(plan.get("strategy", "")) for plan in plans}
    if not isinstance(parsed, dict):
        return {
            "applied": False,
            "recommended_plan_id": None,
            "recommended_strategy": None,
            "rationale": "AgentArts 未返回可解析的推荐结果，使用确定性目标排序。",
            "source": "deterministic-fallback",
        }

    candidates = [parsed.get("recommended_plan_id"), parsed.get("selected_plan_id"), parsed.get("plan_id")]
    recommendation = parsed.get("recommendation")
    if isinstance(recommendation, dict):
        candidates.extend([recommendation.get("plan_id"), recommendation.get("recommended_plan_id")])
    for value in candidates:
        if str(value) in allowed:
            return {
                "applied": True,
                "recommended_plan_id": str(value),
                "recommended_strategy": allowed[str(value)],
                "rationale": str(parsed.get("summary") or parsed.get("rationale") or "AgentArts 返回有效候选方案推荐。")[:500],
                "source": "agentarts",
            }

    remote_plans = parsed.get("plans")
    if isinstance(remote_plans, list):
        for item in remote_plans:
            if isinstance(item, dict):
                value = str(item.get("plan_id") or item.get("recommended_plan_id") or "")
                if value in allowed:
                    return {
                        "applied": True,
                        "recommended_plan_id": value,
                        "recommended_strategy": allowed[value],
                        "rationale": str(item.get("rationale") or parsed.get("summary") or "AgentArts 排序推荐。")[:500],
                        "source": "agentarts",
                    }

    strategy_hint = str(parsed.get("recommended_strategy") or parsed.get("strategy") or "").lower()
    for plan_id, strategy in allowed.items():
        if strategy_hint and strategy.lower() == strategy_hint:
            return {
                "applied": True,
                "recommended_plan_id": plan_id,
                "recommended_strategy": strategy,
                "rationale": str(parsed.get("summary") or parsed.get("rationale") or "AgentArts 策略推荐。")[:500],
                "source": "agentarts-strategy",
            }

    return {
        "applied": False,
        "recommended_plan_id": None,
        "recommended_strategy": None,
        "rationale": "AgentArts 推荐不在允许方案集合中，已回退到确定性目标排序。",
        "source": "agentarts-invalid-fallback",
    }


def build_delivery_package(verification: dict, scenario: dict, snapshot: dict | None, weights: dict, agent_decision: dict | None = None) -> dict:
    approved = [report for report in verification["reports"] if report["approved"]]
    if not approved:
        return {
            "agent": "delivery_agent",
            "status": "human_escalation",
            "summary": "两轮核验均未形成可交付方案，已转入人工研判。",
            "recommended_plan": None,
            "alternatives": [],
            "risk_alerts": verification["required_fixes"],
            "actions": ["人工确认道路与库存数据", "调整需求或补充资源后重新推演"],
            "agent_decision": {**(agent_decision or {}), "applied": False},
            "disclaimer": "本结果仅为应急演练数据，不构成真实调度命令。",
        }

    def objective_score(plan: dict) -> float:
        metrics = plan["metrics"]
        risk_score = plan["verification"]["score"]
        speed = max(0, 100 - _num(metrics["max_eta_hours"]) * 18)
        cost = max(0, 100 - _num(metrics["total_cost"]) / 180)
        return speed * weights.get("speed", 0.38) + cost * weights.get("cost", 0.27) + risk_score * weights.get("risk", 0.35)

    decision = dict(agent_decision or {})
    selected_plan_id = decision.get("recommended_plan_id") if decision.get("applied") else None
    approved_ids = {item["plan_id"] for item in approved}
    decision["applied"] = bool(selected_plan_id and selected_plan_id in approved_ids)

    def ranking_score(plan: dict) -> float:
        score = objective_score(plan)
        if decision["applied"] and plan.get("plan_id") == selected_plan_id:
            score += 6.0
        return score

    approved.sort(key=ranking_score, reverse=True)
    recommended = approved[0]
    start = datetime.now()
    timeline = []
    for group in recommended["groups"]:
        departure = start + timedelta(minutes=20 + group["vehicle_trips"] * 5)
        arrival = departure + timedelta(hours=_num(group["eta_hours"]))
        timeline.append({
            "warehouse": group["warehouse_name"],
            "destination": group["destination_name"],
            "route": group["route_name"],
            "departure": departure.isoformat(timespec="minutes"),
            "arrival": arrival.isoformat(timespec="minutes"),
            "vehicle_trips": group["vehicle_trips"],
        })

    risk_alerts = [warning["message"] for warning in recommended["verification"]["warnings"]]
    if not risk_alerts:
        risk_alerts.append("未发现阻断项，仍需关注现场道路与库存实时变化。")
    actions = [
        "核对本方案对应的库存快照和应急物资清单。",
        "由值班负责人确认道路可用性及现场收货条件。",
        "按批次核对装车清单、车辆和交接人员。",
        "执行过程中如出现道路或库存变化，重新触发核验与重规划。",
    ]
    alternatives = [
        {
            "plan_id": plan["plan_id"], "name": plan["name"], "tag": plan["tag"],
            "policy_note": plan.get("policy_note", ""),
            "cost": plan["metrics"]["total_cost"],
            "max_eta_hours": plan["metrics"]["max_eta_hours"],
            "risk_score": plan["verification"]["score"],
        }
        for plan in approved[1:]
    ]
    return {
        "agent": "delivery_agent",
        "status": "ready",
        "summary": f"已形成 {len(approved)} 套通过核验的方案，推荐「{recommended['name']}」进入人工审批。",
        "recommended_plan": {
            "plan_id": recommended["plan_id"], "name": recommended["name"],
            "tag": recommended["tag"], "policy_note": recommended.get("policy_note", ""),
            "metrics": recommended["metrics"],
            "verification_score": recommended["verification"]["score"],
        },
        "alternatives": alternatives,
        "timeline": timeline,
        "risk_alerts": risk_alerts,
        "actions": actions,
        "data_basis": {
            "scenario": scenario["name"],
            "snapshot": snapshot["label"] if snapshot else "未记录",
            "snapshot_time": snapshot["uploaded_at"] if snapshot else "",
            "data_mode": "SIMULATED / 演练数据",
        },
        "agent_decision": decision,
        "disclaimer": "本方案基于演练数据生成，仅用于预案编制、教学和辅助决策，不替代现场应急指挥。",
    }
