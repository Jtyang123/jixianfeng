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


PRIORITY_LEVELS = ["P0", "P1", "P2", "P3"]


def apply_llm_priority_adjustments(demand_plan: dict, reasoning: dict) -> dict:
    """Apply bounded priority changes proposed by the LLM reasoning layer."""
    result = reasoning.get("result") if isinstance(reasoning, dict) else None
    if not isinstance(result, dict):
        return {"applied": False, "source": "deterministic-fallback", "changes": []}
    raw = result.get("priority_adjustments") or result.get("priority_overrides") or {}
    if isinstance(raw, list):
        adjustments = {
            str(item.get("material_id") or item.get("material_code") or item.get("material_name") or ""): item.get("priority")
            for item in raw if isinstance(item, dict)
        }
    elif isinstance(raw, dict):
        adjustments = raw
    else:
        adjustments = {}
    changes = []
    for item in demand_plan.get("items", []):
        candidates = [str(item.get("material_id")), str(item.get("material_code")), str(item.get("material_name"))]
        target = next((str(adjustments.get(key) or "") for key in candidates if adjustments.get(key)), "")
        current = str(item.get("priority") or "P2")
        if target not in PRIORITY_LEVELS or current not in PRIORITY_LEVELS or target == current:
            continue
        if abs(PRIORITY_LEVELS.index(target) - PRIORITY_LEVELS.index(current)) > 1:
            continue
        item["priority"] = target
        item["llm_priority_adjustment"] = {"from": current, "to": target}
        changes.append({
            "material_id": item.get("material_id"), "material_name": item.get("material_name"),
            "from": current, "to": target,
        })
    demand_plan["llm_priority_decision"] = {
        "applied": bool(changes), "source": reasoning.get("mode", "llm"),
        "changes": changes, "boundary": "仅允许相邻优先级调整一级，不改变数量、截止时间和硬规则",
    }
    return demand_plan["llm_priority_decision"]


def normalize_llm_weight_profile(default_weights: dict, reasoning: dict) -> dict:
    """Blend user weights with a bounded LLM proposal used for approved-plan ranking."""
    defaults = {
        "speed": max(0.0, _num(default_weights.get("speed"), 0.38)),
        "cost": max(0.0, _num(default_weights.get("cost"), 0.27)),
        "risk": max(0.0, _num(default_weights.get("risk"), 0.35)),
    }
    total = sum(defaults.values()) or 1.0
    defaults = {key: value / total for key, value in defaults.items()}
    result = reasoning.get("result") if isinstance(reasoning, dict) else None
    raw = None
    if isinstance(result, dict):
        raw = result.get("weight_profile") or result.get("weights")
    if not isinstance(raw, dict):
        return {"applied": False, "weights": defaults, "source": "user-weights", "raw": {}}
    proposed = {}
    valid_values = 0
    for key in ("speed", "cost", "risk"):
        value = _num(raw.get(key), float("nan"))
        if not math.isfinite(value):
            proposed[key] = defaults[key]
            continue
        valid_values += 1
        proposed[key] = max(0.05, min(0.80, value))
    if not valid_values:
        return {"applied": False, "weights": defaults, "source": "llm-no-valid-weights", "raw": {}}
    proposed_total = sum(proposed.values()) or 1.0
    proposed = {key: value / proposed_total for key, value in proposed.items()}
    blended = {key: round(defaults[key] * 0.5 + proposed[key] * 0.5, 4) for key in defaults}
    blended_total = sum(blended.values()) or 1.0
    blended = {key: round(value / blended_total, 4) for key, value in blended.items()}
    applied = any(abs(blended[key] - defaults[key]) > 0.0001 for key in defaults)
    return {
        "applied": applied,
        "weights": blended,
        "source": reasoning.get("mode", "llm") if applied else "llm-no-weight-change",
        "raw": proposed,
        "boundary": "用户权重与 LLM 建议各占 50%，仅用于已核验方案排序，不改变核验结论",
    }


def build_demand_plan(request: dict, materials: list[dict], destination_rows: list[dict]) -> dict:
    material_map = {item["id"]: item for item in materials}
    total_population = sum(max(1, int(dest["population"])) for dest in destination_rows)
    demand_items: list[dict] = []
    assumptions: list[str] = []
    requested_rows = list(request.get("demand_items") or [])
    mapped_rows = 0
    valid_quantity_rows = 0
    valid_deadline_rows = 0

    for raw in requested_rows:
        material = material_map.get(int(raw["material_id"]))
        if not material:
            continue
        mapped_rows += 1
        total_quantity = _num(raw["quantity"])
        if total_quantity > 0:
            valid_quantity_rows += 1
        if _num(raw.get("deadline_hours"), 12) > 0:
            valid_deadline_rows += 1
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
    requested_count = max(len(requested_rows), 1)
    destination_count = max(len(destination_rows), 1)
    factors = {
        "material_mapping_rate": round(mapped_rows / requested_count, 4),
        "valid_quantity_rate": round(valid_quantity_rows / requested_count, 4),
        "valid_deadline_rate": round(valid_deadline_rows / requested_count, 4),
        "population_completeness": round(sum(1 for dest in destination_rows if int(dest.get("population") or 0) > 0) / destination_count, 4),
        "coordinate_completeness": round(sum(1 for dest in destination_rows if dest.get("lat") is not None and dest.get("lng") is not None) / destination_count, 4),
    }
    confidence = (
        0.50
        + factors["material_mapping_rate"] * 0.18
        + factors["valid_quantity_rate"] * 0.10
        + factors["valid_deadline_rate"] * 0.08
        + factors["population_completeness"] * 0.07
        + factors["coordinate_completeness"] * 0.07
    )
    if not requested_rows:
        confidence = 0.35
    confidence = round(max(0.35, min(0.98, confidence)), 2)
    return {
        "agent": "demand_agent",
        "summary": f"已将 {len(requested_rows)} 类物资拆分为 {len(demand_items)} 条安置点需求。",
        "items": demand_items,
        "assumptions": assumptions,
        "evidence_refs": ["需求分配规则：按安置人口比例", "物资主数据：单位与单件重量"],
        "confidence": confidence,
        "confidence_factors": factors,
        "confidence_method": "0.50 + 物资映射18% + 数量有效10% + 时限有效8% + 人口完整7% + 坐标完整7%，上限0.98",
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


def allowed_rework_policies(verification: dict) -> list[dict]:
    warning_codes = {
        warning["code"]
        for report in verification.get("reports", [])
        for warning in report.get("verification", {}).get("warnings", [])
    }
    codes = set(verification.get("blocker_summary", [])) | warning_codes
    policies = []
    if any(code.startswith("ROAD-") for code in codes):
        policies.append({"id": "route_failover", "name": "路线故障转移", "description": "排除封闭路线并优先后备通道"})
    if any(code.startswith(("STOCK-", "COVERAGE-")) for code in codes):
        policies.append({"id": "multi_warehouse", "name": "跨仓拆分", "description": "跨多个仓库重新平衡库存"})
    if any(code.startswith("VEHICLE-") for code in codes):
        policies.append({"id": "fleet_rebalance", "name": "运力再平衡", "description": "优先有可用车队的仓库并校验趟次"})
    if any(code.startswith("ETA-") for code in codes):
        policies.append({"id": "deadline_first", "name": "截止时间优先", "description": "提高超时惩罚并优化时效"})
    if any(code.startswith(("DATA-", "SCHEMA-", "MISSING-", "INVALID-")) for code in codes):
        policies.append({"id": "data_quality_repair", "name": "数据质量修复", "description": "暂停自动调度并请求补全或纠正源数据"})
    if not policies:
        policies.append({"id": "review_data_quality", "name": "人工数据复核", "description": "当前问题无法安全自动修复"})
    policies.append({"id": "escalate_human", "name": "转人工研判", "description": "停止自动循环并交由值班人员处理"})
    return policies

def build_prompt_adaptation(verification: dict, directive: dict) -> dict:
    categories = set(directive.get("categories") or [])
    instructions = []
    if "道路通行" in categories:
        instructions.append("下一轮必须先排除事件封闭路线，比较主备通道 ETA 与风险，不得再次采用封闭路线。")
    if "库存与覆盖" in categories:
        instructions.append("下一轮必须检查总库存和分仓可用量，优先跨仓拆分并明确不可满足缺口。")
    if "运力" in categories:
        instructions.append("下一轮必须按仓库可用车辆与载重计算趟次，禁止虚构默认运力。")
    if "时效" in categories:
        instructions.append("下一轮必须提高截止时间惩罚，优先满足 P0 时限并报告预计超时。")
    if "数据质量" in categories:
        instructions.append("下一轮必须停止自动规划，输出需要修复的数据字段和人工复核要求。")
    attempt = int(directive.get("attempt") or 1)
    return {
        "agent_id": "matching_agent",
        "base_version": "v1.4",
        "active_version": f"v1.4+adaptive.r{attempt}",
        "selection_policy": directive.get("selected_policy", ""),
        "instructions": instructions,
        "source_issue_codes": directive.get("issue_codes", []),
        "scope": "当前运行的下一次匹配调用",
    }

def build_rework_directive(verification: dict, next_attempt: int, selected_policy: str = "") -> dict:
    warning_codes = sorted({
        warning["code"]
        for report in verification.get("reports", [])
        for warning in report.get("verification", {}).get("warnings", [])
    })
    codes = sorted(set(verification.get("blocker_summary", [])) | set(warning_codes))
    categories: list[str] = []
    policies: dict[str, str] = {}
    tool_overrides: list[str] = []

    if any(code.startswith("ROAD-") for code in codes):
        categories.append("道路通行")
        policies["route_policy"] = "exclude_closed_routes"
        tool_overrides.append("route_query:排除事件封闭路线并优先后备通道")
    if any(code.startswith(("STOCK-", "COVERAGE-")) for code in codes):
        categories.append("库存与覆盖")
        policies["allocation_policy"] = "multi_warehouse"
        tool_overrides.append("inventory_query:跨仓拆分并校验总可用量")
    if any(code.startswith("VEHICLE-") for code in codes):
        categories.append("运力")
        policies["fleet_policy"] = "capacity_first"
        policies["allocation_policy"] = "multi_warehouse"
        tool_overrides.append("vehicle_query:优先有可用车队的仓库并校验趟次")
    if any(code.startswith("ETA-") for code in codes):
        categories.append("时效")
        policies["objective_policy"] = "deadline_first"
        tool_overrides.append("multi_objective_planner:提高截止时间惩罚")
    data_quality_issue = any(code.startswith(("DATA-", "SCHEMA-", "MISSING-", "INVALID-")) for code in codes)
    if data_quality_issue:
        categories.append("数据质量")
        policies["data_policy"] = "request_data_repair"
        tool_overrides.append("data_quality_tool:暂停自动调度并请求修复源数据")

    allowed = allowed_rework_policies(verification)
    allowed_ids = {item["id"] for item in allowed}
    selected = selected_policy if selected_policy in allowed_ids else ""
    if not selected:
        selected = next((item["id"] for item in allowed if item["id"] != "escalate_human"), "escalate_human")
    if selected == "route_failover":
        policies["route_policy"] = "exclude_closed_routes"
    elif selected == "multi_warehouse":
        policies["allocation_policy"] = "multi_warehouse"
    elif selected == "fleet_rebalance":
        policies["fleet_policy"] = "capacity_first"
        policies["allocation_policy"] = "multi_warehouse"
    elif selected == "deadline_first":
        policies["objective_policy"] = "deadline_first"
    elif selected == "data_quality_repair":
        policies["data_policy"] = "request_data_repair"

    hard_resource_issue = any(code.startswith(("STOCK-", "COVERAGE-", "VEHICLE-", "ROAD-")) for code in codes)
    can_retry = bool(codes) and selected != "escalate_human" and not data_quality_issue and not (next_attempt >= 3 and hard_resource_issue)
    return {
        "attempt": next_attempt,
        "issue_codes": codes,
        "categories": categories,
        "policies": policies,
        "tool_overrides": tool_overrides,
        "allowed_policies": allowed,
        "selected_policy": selected,
        "can_retry": can_retry,
        "reason": f"根据核验问题代码选择修复策略：{selected}。" if selected else "没有可安全执行的自动修复策略。",
    }

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

def _cost_breakdown(route: dict, trips: int, strategy_factor: float) -> dict:
    """Return a transparent comparison estimate, not a carrier quotation."""
    trips = max(1, int(trips))
    base_transport_cost = round(_num(route.get("cost")) * trips, 2)
    fixed_dispatch_cost = round(min(base_transport_cost, 180.0 * trips), 2)
    distance_time_cost = round(max(0.0, base_transport_cost - fixed_dispatch_cost), 2)
    strategy_adjustment = round(base_transport_cost * (_num(strategy_factor, 1.0) - 1.0), 2)
    return {
        "currency": "CNY",
        "basis": "\u6f14\u7ec3\u4f30\u7b97\uff0c\u4e0d\u662f\u627f\u8fd0\u5546\u62a5\u4ef7",
        "model_version": "estimate-v2",
        "base_transport_cost": base_transport_cost,
        "fixed_dispatch_cost": fixed_dispatch_cost,
        "distance_time_cost": distance_time_cost,
        "strategy_adjustment": strategy_adjustment,
        "total_cost": round(base_transport_cost + strategy_adjustment, 2),
        "formula": "\u03a3(\u8def\u7ebf\u57fa\u7840\u8fd0\u8f93\u8d39 \u00d7 \u8d9f\u6b21 \u00d7 \u7b56\u7565\u7cfb\u6570)",
        "includes": ["\u56fa\u5b9a\u51fa\u8f66\u8d39", "\u91cc\u7a0b\u4e0e\u8fd0\u8f93\u65f6\u957f\u4f30\u7b97", "\u7b56\u7565\u670d\u52a1\u8c03\u6574"],
        "excludes": ["\u771f\u5b9e\u5e02\u573a\u62a5\u4ef7", "\u8def\u6865\u8d39\u5b9e\u62a5", "\u53f8\u673a\u98df\u5bbf\u8865\u8d34", "\u88c5\u5378\u4eba\u5de5", "\u51b7\u94fe\u9644\u52a0\u8d39", "\u4e34\u65f6\u52a0\u4ef7"],
    }


def generate_plans(
    demand_plan: dict,
    inventory: list[dict],
    warehouses: list[dict],
    routes: list[dict],
    vehicles: list[dict],
    attempt: int,
    closed_routes: list[str],
    rework_directive: dict | None = None,
) -> list[dict]:
    stock = _available_inventory(inventory)
    warehouse_map = {warehouse["id"]: warehouse for warehouse in warehouses}
    closed_set = set(closed_routes)
    rework_directive = rework_directive or {}
    policies = rework_directive.get("policies", {})
    ignore_closures = attempt == 1 and policies.get("route_policy") != "exclude_closed_routes"
    fleet_first = policies.get("fleet_policy") == "capacity_first"
    deadline_first = policies.get("objective_policy") == "deadline_first"
    multi_warehouse = policies.get("allocation_policy") == "multi_warehouse"
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
                take = min(remaining, available)
                weight_kg = take * _num(demand["unit_weight_kg"])
                warehouse_vehicles = vehicle_by_warehouse.get(wh_id, [])
                capacity = max([_num(vehicle["capacity_kg"]) for vehicle in warehouse_vehicles] or [0])
                trips = max(1, math.ceil(weight_kg / max(capacity, 1)))
                vehicle_gap = max(0, trips - len(warehouse_vehicles))
                trip_cost = _num(route["cost"]) * trips
                eta = _num(route["eta_hours"])
                overdue = max(0.0, eta - _num(demand["deadline_hours"], 12))
                raw_candidates.append({
                    "warehouse": warehouse,
                    "route": route,
                    "available": available,
                    "take": take,
                    "eta": eta,
                    "overdue": overdue,
                    "vehicle_gap": vehicle_gap,
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
                vehicle_gap_values = [item["vehicle_gap"] for item in raw_candidates]
                overdue_values = [item["overdue"] for item in raw_candidates]
                min_eta, max_eta = min(eta_values), max(eta_values)
                min_cost, max_cost = min(cost_values), max(cost_values)
                min_dispatch, max_dispatch = min(dispatch_cost_values), max(dispatch_cost_values)
                min_vehicle_gap, max_vehicle_gap = min(vehicle_gap_values), max(vehicle_gap_values)
                min_overdue, max_overdue = min(overdue_values), max(overdue_values)

                def norm(value: float, low: float, high: float) -> float:
                    if high - low <= 0.000001:
                        return 0.0
                    return (value - low) / (high - low)

                for item in raw_candidates:
                    eta_score = norm(item["eta"], min_eta, max_eta)
                    cost_score = norm(item["unit_cost"], min_cost, max_cost)
                    dispatch_score = norm(item["trip_cost"], min_dispatch, max_dispatch)
                    vehicle_gap_score = norm(item["vehicle_gap"], min_vehicle_gap, max_vehicle_gap)
                    overdue_score = norm(item["overdue"], min_overdue, max_overdue)
                    fleet_penalty = vehicle_gap_score * (0.32 if fleet_first else 0.08)
                    deadline_penalty = overdue_score * (0.30 if deadline_first else 0.06)
                    allocation_bonus = item["new_warehouse"] * (0.06 if multi_warehouse else 0.0)
                    if strategy["id"] == "speed":
                        candidate_score = eta_score * 0.76 + cost_score * 0.06 + item["risk"] * 0.04 + item["split"] * 0.12 + item["new_warehouse"] * 0.06 + fleet_penalty + deadline_penalty + allocation_bonus
                    elif strategy["id"] == "cost":
                        candidate_score = eta_score * 0.01 + dispatch_score * 0.66 + item["risk"] * 0.01 + item["split"] * 0.22 + item["new_warehouse"] * 0.10 + fleet_penalty + deadline_penalty + allocation_bonus
                    else:
                        candidate_score = eta_score * 0.34 + cost_score * 0.22 + item["risk"] * 0.18 + item["split"] * 0.14 + item["load_pressure"] * 0.08 + item["new_warehouse"] * 0.04 + fleet_penalty + deadline_penalty + allocation_bonus
                    candidates.append((candidate_score, item["warehouse"], item["route"]))

            candidates.sort(key=lambda item: item[0])
            for _, warehouse, route in candidates:
                if remaining <= 0.001:
                    break
                key = (warehouse["id"], demand["material_id"])
                available = max(0.0, stock.get(key, 0) - used[key])
                take_cap = remaining
                if len(candidates) > 1 and multi_warehouse and remaining > 10:
                    take_cap = max(1.0, remaining * (0.5 if strategy["id"] == "speed" else 0.6))
                take = min(remaining, available, take_cap)
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
                for _, warehouse, route in candidates:
                    key = (warehouse["id"], demand["material_id"])
                    available = max(0.0, stock.get(key, 0) - used[key])
                    if available <= 0.001:
                        continue
                    take = min(remaining, available)
                    used[key] += take
                    remaining -= take
                    warehouse_dispatches[warehouse["id"]] += 1
                    existing = next((
                        item for item in assignments
                        if item["warehouse_id"] == warehouse["id"]
                        and item["destination_id"] == demand["destination_id"]
                        and item["material_id"] == demand["material_id"]
                        and item["route_id"] == route["id"]
                    ), None)
                    if existing:
                        existing["quantity"] = round(existing["quantity"] + take, 2)
                    else:
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
                    if remaining <= 0.001:
                        break

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
        total_weight_kg = 0.0
        total_ton_km = 0.0
        weighted_eta_hours = 0.0
        for (wh_id, dst_id), items in grouped.items():
            weight = sum(_num(item["quantity"]) * _num(item["unit_weight_kg"]) for item in items)
            available_vehicles = vehicle_by_warehouse.get(wh_id, [])
            capacity = max([_num(vehicle["capacity_kg"]) for vehicle in available_vehicles] or [0])
            trips = max(1, math.ceil(weight / max(capacity, 1)))
            route = next(route for route in routes if route["id"] == items[0]["route_id"])
            effective_eta = round(_num(route["eta_hours"]) + _num(strategy["consolidation_delay_hours"]), 2)
            breakdown = _cost_breakdown(route, trips, strategy["cost_factor"])
            group_cost = breakdown["total_cost"]
            distance_km = _num(route.get("distance_km"))
            ton_km = (weight / 1000.0) * distance_km
            total_cost += group_cost
            total_trips += trips
            total_weight_kg += weight
            total_ton_km += ton_km
            weighted_eta_hours += effective_eta * trips
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
                    "distance_km": round(distance_km, 2),
                    "eta_hours": effective_eta,
                    "base_eta_hours": route["eta_hours"],
                    "policy_delay_hours": _num(strategy["consolidation_delay_hours"]),
                    "weight_kg": round(weight, 2),
                    "vehicle_capacity_kg": capacity,
                    "available_vehicles": len(available_vehicles),
                    "vehicle_trips": trips,
                    "cost": group_cost,
                    "cost_per_ton_km": round(group_cost / ton_km, 2) if ton_km > 0 else 0.0,
                    "cost_breakdown": breakdown,
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
        plan_cost_breakdown = {
            "currency": "CNY",
            "basis": "\u6f14\u7ec3\u4f30\u7b97\uff0c\u4e0d\u662f\u627f\u8fd0\u5546\u62a5\u4ef7",
            "model_version": "estimate-v2",
            "base_transport_cost": round(sum(group["cost_breakdown"]["base_transport_cost"] for group in group_outputs), 2),
            "fixed_dispatch_cost": round(sum(group["cost_breakdown"]["fixed_dispatch_cost"] for group in group_outputs), 2),
            "distance_time_cost": round(sum(group["cost_breakdown"]["distance_time_cost"] for group in group_outputs), 2),
            "strategy_adjustment": round(sum(group["cost_breakdown"]["strategy_adjustment"] for group in group_outputs), 2),
            "total_cost": round(total_cost, 2),
            "formula": "\u03a3(\u8def\u7ebf\u57fa\u7840\u8fd0\u8f93\u8d39 \u00d7 \u8d9f\u6b21 \u00d7 \u7b56\u7565\u7cfb\u6570)",
            "includes": ["\u56fa\u5b9a\u51fa\u8f66\u8d39", "\u91cc\u7a0b\u4e0e\u8fd0\u8f93\u65f6\u957f\u4f30\u7b97", "\u7b56\u7565\u670d\u52a1\u8c03\u6574"],
            "excludes": ["\u771f\u5b9e\u5e02\u573a\u62a5\u4ef7", "\u8def\u6865\u8d39\u5b9e\u62a5", "\u53f8\u673a\u98df\u5bbf\u8865\u8d34", "\u88c5\u5378\u4eba\u5de5", "\u51b7\u94fe\u9644\u52a0\u8d39", "\u4e34\u65f6\u52a0\u4ef7"],
        }
        plans.append(
            {
                "plan_id": f"PLAN-{attempt}-{strategy['id'].upper()}",
                "name": strategy["name"],
                "tag": strategy["tag"],
                "strategy": strategy["id"],
                "policy_note": strategy["policy_note"],
                "cost_factor": strategy["cost_factor"],
                "consolidation_delay_hours": strategy["consolidation_delay_hours"],
                "cost_breakdown": plan_cost_breakdown,
                "attempt": attempt,
                "assignments": assignments,
                "groups": group_outputs,
                "unmet": unmet,
                "metrics": {
                    "total_cost": round(total_cost, 2),
                    "max_eta_hours": round(max_eta, 2),
                    "avg_eta_hours": round(weighted_eta_hours / total_trips, 2) if total_trips else 0.0,
                    "coverage": round(coverage, 4),
                    "vehicle_trips": total_trips,
                    "warehouse_count": len({item["warehouse_id"] for item in assignments}),
                    "assignment_count": len(assignments),
                    "unmet_items": len(unmet),
                    "total_weight_kg": round(total_weight_kg, 2),
                    "ton_km": round(total_ton_km, 2),
                    "cost_per_trip": round(total_cost / total_trips, 2) if total_trips else 0.0,
                    "cost_per_ton_km": round(total_cost / total_ton_km, 2) if total_ton_km > 0 else 0.0,
                    "cost_basis": "\u6f14\u7ec3\u4f30\u7b97\uff0c\u4e0d\u662f\u627f\u8fd0\u5546\u62a5\u4ef7",
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
                code = f"VEHICLE-{assignment['warehouse_id']}"
                if code not in seen_blockers:
                    blockers.append({
                        "code": code,
                        "type": "运力",
                        "message": f"{assignment['warehouse_name']} 所需趟次超过可用车辆数。",
                        "evidence": f"需要 {assignment['group_vehicle_trips']} 趟 / 可用 {vehicle_count.get(assignment['warehouse_id'], 0)} 辆",
                    })
                    seen_blockers.add(code)

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
            {"name": "运力约束", "status": "fail" if any(item["type"] == "运力" for item in blockers) else "pass"},
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
            "summary": "多轮核验均未形成可交付方案，已转入人工研判。",
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
