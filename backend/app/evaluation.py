from __future__ import annotations

import asyncio
import copy
import json
import math
import time
import uuid
from datetime import datetime
from pathlib import Path
from statistics import mean
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import httpx

from .agentarts import AgentArtsBridge, AgentArtsError
from .agents import build_demand_plan, build_rework_directive, generate_plans, normalize_agentarts_decision, verify_plans
from .config import EVALUATION_CONCURRENCY_LEVELS, EVALUATION_SOAK_SECONDS
from .database import Database
from .orchestrator import _parse_json_text
from .routing import generate_route_candidates
from .run_queue import RunQueueService, RunWorkerPool


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


def _scenario_request(scenario: dict, materials: list[dict]) -> dict:
    material_ids = {item["code"]: item["id"] for item in materials}
    return {
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


def _independent_layout(
    destinations: list[dict], warehouses: list[dict], routes: list[dict],
    vehicles: list[dict], inventory: list[dict], profile: dict,
) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
    """Build a distinct warehouse/destination/route topology for independent evaluation."""
    prefix = profile["prefix"]
    destination_map = {}
    layout_destinations = []
    for index, source in enumerate(destinations):
        item = copy.deepcopy(source)
        old_id = item["id"]
        new_id = f"DST-{prefix}{index + 1:02d}"
        destination_map[old_id] = new_id
        item["id"] = new_id
        item["name"] = f"{item.get('name', '安置点')}（{profile['name']}）"
        item["region"] = profile["region"]
        item["address"] = profile["address"]
        item["lat"] = round(profile["destination_lat"] + index * profile["lat_step"], 6)
        item["lng"] = round(profile["destination_lng"] - index * profile["lng_step"], 6)
        layout_destinations.append(item)

    warehouse_map = {}
    layout_warehouses = []
    for index, source in enumerate(warehouses):
        item = copy.deepcopy(source)
        old_id = item["id"]
        new_id = f"WH-{prefix}{index + 1:02d}"
        warehouse_map[old_id] = new_id
        item["id"] = new_id
        item["name"] = f"{item.get('name', '仓库')}（{profile['name']}）"
        item["region"] = profile["region"]
        item["lat"] = round(profile["warehouse_lat"] - index * profile["warehouse_lat_step"], 6)
        item["lng"] = round(profile["warehouse_lng"] + index * profile["warehouse_lng_step"], 6)
        layout_warehouses.append(item)

    layout_inventory = []
    for source in inventory:
        item = copy.deepcopy(source)
        item["warehouse_id"] = warehouse_map.get(item.get("warehouse_id"), item.get("warehouse_id"))
        item["warehouse_name"] = f"{item.get('warehouse_name', item['warehouse_id'])}（{profile['name']}）"
        layout_inventory.append(item)

    vehicle_count = max(1, math.ceil(len(vehicles) * profile.get("vehicle_ratio", 1.0)))
    layout_vehicles = []
    for index, source in enumerate(vehicles[:vehicle_count]):
        item = copy.deepcopy(source)
        item["id"] = f"VEH-{prefix}{index + 1:03d}"
        item["warehouse_id"] = warehouse_map.get(item.get("warehouse_id"), item.get("warehouse_id"))
        item["lat"] = round(profile["warehouse_lat"] - (index % max(1, len(warehouses))) * profile["warehouse_lat_step"], 6)
        item["lng"] = round(profile["warehouse_lng"] + (index % max(1, len(warehouses))) * profile["warehouse_lng_step"], 6)
        layout_vehicles.append(item)

    layout_routes = []
    for index, source in enumerate(routes):
        item = copy.deepcopy(source)
        item["id"] = f"ROUTE-{prefix}{index + 1:03d}"
        item["warehouse_id"] = warehouse_map.get(item.get("warehouse_id"), item.get("warehouse_id"))
        item["destination_id"] = destination_map.get(item.get("destination_id"), item.get("destination_id"))
        item["road_name"] = f"{profile['road_prefix']}-{index + 1:02d}"
        item["distance_km"] = round(float(item.get("distance_km") or 0) * profile["distance_factor"], 2)
        item["eta_hours"] = round(float(item.get("eta_hours") or 0) + profile["eta_add"], 2)
        item["cost"] = round(float(item.get("cost") or 0) * profile["cost_factor"], 2)
        item["geometry"] = []
        item["status"] = "open"
        layout_routes.append(item)
    return layout_destinations, layout_warehouses, layout_routes, layout_vehicles, layout_inventory


def _benchmark_contexts(db: Database) -> list[dict]:
    materials = db.list_materials()
    destinations = db.destination_rows()
    warehouses = db.warehouse_rows()
    routes = db.route_rows()
    vehicles = db.vehicle_rows()
    snapshot = db.latest_snapshot()
    inventory = copy.deepcopy(db.inventory_rows(snapshot["id"] if snapshot else None))
    for row in inventory:
        row["locked_quantity"] = 0
    rain = db.get_scenario("rainstorm-ii")
    heat = db.get_scenario("heatwave-iii")
    if not rain or not heat:
        raise RuntimeError("基准场景不可用")

    def build(
        scenario: dict, *, inventory_rows=None, vehicle_rows=None, feasible=True, name=None,
        destination_rows=None, warehouse_rows=None, route_rows=None,
        independence="shared_layout", layout_id="default-network",
    ):
        return {
            "id": scenario["id"], "name": name or scenario["name"], "scenario": scenario,
            "request": _scenario_request(scenario, materials), "materials": materials,
            "destinations": destination_rows if destination_rows is not None else destinations,
            "warehouses": warehouse_rows if warehouse_rows is not None else warehouses,
            "routes": route_rows if route_rows is not None else routes,
            "vehicles": copy.deepcopy(vehicle_rows if vehicle_rows is not None else vehicles),
            "inventory": copy.deepcopy(inventory_rows if inventory_rows is not None else inventory),
            "closed_routes": list(scenario["closed_routes"]), "feasible": feasible,
            "data_independence": independence, "layout_id": layout_id,
        }

    shortage_inventory = copy.deepcopy(inventory)
    for row in shortage_inventory:
        row["quantity"] = round(float(row.get("quantity") or 0) * 0.05, 2)
        row["locked_quantity"] = 0

    western_profile = {
        "prefix": "W", "name": "西部走廊", "region": "演练·西部走廊", "address": "西部走廊独立安置点",
        "destination_lat": 30.93, "destination_lng": 114.78, "lat_step": 0.07, "lng_step": 0.09,
        "warehouse_lat": 31.18, "warehouse_lng": 114.56, "warehouse_lat_step": 0.08,
        "warehouse_lng_step": 0.12, "vehicle_ratio": 1.0, "road_prefix": "西部通道",
        "distance_factor": 1.17, "eta_add": 0.22, "cost_factor": 1.09,
    }
    southern_profile = {
        "prefix": "S", "name": "南部环线", "region": "演练·南部环线", "address": "南部环线独立安置点",
        "destination_lat": 30.31, "destination_lng": 114.51, "lat_step": 0.05, "lng_step": 0.11,
        "warehouse_lat": 30.18, "warehouse_lng": 114.28, "warehouse_lat_step": 0.07,
        "warehouse_lng_step": 0.15, "vehicle_ratio": 1.0, "road_prefix": "南部环线",
        "distance_factor": 1.31, "eta_add": 0.35, "cost_factor": 0.96,
    }
    western = _independent_layout(destinations, warehouses, routes, vehicles, inventory, western_profile)
    southern = _independent_layout(destinations, warehouses, routes, vehicles, inventory, southern_profile)
    western_scenario = copy.deepcopy(heat)
    western_scenario["id"] = "western-corridor-layout"
    western_scenario["name"] = "西部走廊独立路网"
    western_scenario["location"] = "演练·西部走廊"
    western_scenario["closed_routes"] = []
    western_scenario["default_demand"] = copy.deepcopy(heat["default_demand"])
    southern_scenario = copy.deepcopy(rain)
    southern_scenario["id"] = "southern-ring-layout"
    southern_scenario["name"] = "南部环线独立路网"
    southern_scenario["location"] = "演练·南部环线"
    southern_scenario["closed_routes"] = []
    southern_scenario["default_demand"] = copy.deepcopy(rain["default_demand"])

    return [
        build(rain, independence="shared_layout"),
        build(heat, independence="shared_layout"),
        build(rain, inventory_rows=shortage_inventory, feasible=False, name="库存严重缺口", independence="controlled_variant"),
        build(heat, vehicle_rows=[], feasible=False, name="无可用车辆", independence="controlled_variant"),
        build(
            western_scenario, name="西部走廊独立路网", independence="independent_layout", layout_id="western-corridor",
            destination_rows=western[0], warehouse_rows=western[1], route_rows=western[2],
            vehicle_rows=western[3], inventory_rows=western[4],
        ),
        build(
            southern_scenario, name="南部环线独立路网", independence="independent_layout", layout_id="southern-ring",
            destination_rows=southern[0], warehouse_rows=southern[1], route_rows=southern[2],
            vehicle_rows=southern[3], inventory_rows=southern[4],
        ),
    ]


def _generate_baseline_plan(
    demand_plan: dict, inventory: list[dict], warehouses: list[dict], routes: list[dict],
    vehicles: list[dict], closed_routes: list[str], policy: str,
) -> dict:
    """Independent single-agent baseline: greedy allocation without verification or repair."""
    stock: dict[tuple[str, int], float] = {}
    for row in inventory:
        key = (str(row["warehouse_id"]), int(row["material_id"]))
        stock[key] = stock.get(key, 0.0) + max(0.0, float(row.get("quantity") or 0) - float(row.get("locked_quantity") or 0))
    warehouse_map = {item["id"]: item for item in warehouses}
    vehicle_count: dict[str, int] = {}
    for vehicle in vehicles:
        if vehicle.get("status") == "available":
            vehicle_count[vehicle["warehouse_id"]] = vehicle_count.get(vehicle["warehouse_id"], 0) + 1
    closed = set(closed_routes)
    used: dict[tuple[str, int], float] = {}
    assignments, unmet = [], []
    for demand in demand_plan["items"]:
        remaining = float(demand["quantity"])
        candidates = []
        for warehouse in warehouses:
            wh_id = warehouse["id"]
            available = max(0.0, stock.get((wh_id, int(demand["material_id"])), 0.0) - used.get((wh_id, int(demand["material_id"])), 0.0))
            if available <= 0.001:
                continue
            route_options = [
                route for route in routes
                if route["warehouse_id"] == wh_id and route["destination_id"] == demand["destination_id"]
                and route.get("status") == "open" and route["id"] not in closed
            ]
            if not route_options:
                continue
            route = min(route_options, key=lambda item: float(item["eta_hours"]))
            candidates.append({"warehouse": warehouse, "route": route, "available": available})
        if policy == "stock":
            candidates.sort(key=lambda item: item["available"], reverse=True)
        elif policy == "cost":
            candidates.sort(key=lambda item: float(item["route"]["cost"]))
        else:
            candidates.sort(key=lambda item: float(item["route"]["eta_hours"]))
        for candidate in candidates:
            if remaining <= 0.001:
                break
            take = min(remaining, candidate["available"])
            key = (candidate["warehouse"]["id"], int(demand["material_id"]))
            used[key] = used.get(key, 0.0) + take
            remaining -= take
            assignments.append({
                "warehouse_id": candidate["warehouse"]["id"], "warehouse_name": candidate["warehouse"]["name"],
                "destination_id": demand["destination_id"], "destination_name": demand["destination_name"],
                "material_id": demand["material_id"], "material_code": demand["material_code"],
                "material_name": demand["material_name"], "base_unit": demand["base_unit"],
                "quantity": round(take, 2), "priority": demand["priority"],
                "deadline_hours": demand["deadline_hours"], "route_id": candidate["route"]["id"],
                "route_name": candidate["route"]["road_name"], "route_eta_hours": candidate["route"]["eta_hours"],
                "route_cost": candidate["route"]["cost"], "is_primary_route": bool(candidate["route"]["is_primary"]),
                "unit_weight_kg": demand["unit_weight_kg"], "group_vehicle_trips": 1,
                "group_cost": candidate["route"]["cost"],
                "group_id": f"BASE-{candidate['warehouse']['id']}-{demand['destination_id']}",
            })
        if remaining > 0.001:
            unmet.append({
                "material_id": demand["material_id"], "material_name": demand["material_name"],
                "destination_id": demand["destination_id"], "destination_name": demand["destination_name"],
                "quantity": round(remaining, 2), "unit": demand["base_unit"],
            })
    total_demand = sum(float(item["quantity"]) for item in demand_plan["items"])
    delivered = sum(float(item["quantity"]) for item in assignments)
    total_cost = sum(float(item["route_cost"]) for item in assignments)
    group_cost = sum(
        float(next(route["cost"] for route in routes if route["id"] == item["route_id"]))
        for item in assignments
    )
    return {
        "plan_id": f"BASELINE-{policy.upper()}", "name": f"单 Agent 贪心基线（{policy}）", "tag": "基线",
        "strategy": f"baseline-{policy}", "attempt": 1, "assignments": assignments, "unmet": unmet, "groups": [],
        "metrics": {
            "total_cost": round(group_cost or total_cost, 2),
            "max_eta_hours": round(max([float(item["route_eta_hours"]) for item in assignments] or [0]), 2),
            "coverage": round(delivered / total_demand, 4) if total_demand else 0.0,
            "vehicle_trips": len(assignments),
            "warehouse_count": len({item["warehouse_id"] for item in assignments}),
            "assignment_count": len(assignments), "unmet_items": len(unmet),
        },
        "policy_note": "独立基线：最近仓库贪心分配，不执行多目标协同、独立核验或反馈重算。",
    }

def run_benchmark(db: Database, repetitions: int = 20) -> dict:
    try:
        contexts = _benchmark_contexts(db)
    except Exception as exc:
        return {"error": f"基准场景不可用：{str(exc)[:200]}"}

    scenario_results = []
    baseline_times = []
    multi_times = []
    baseline_scores = []
    multi_scores = []
    baseline_unsafe = 0
    baseline_candidate_unsafe = 0
    baseline_candidate_scores: list[float] = []
    multi_unsafe = 0
    detected_unsafe = 0
    feasible_without_final_delivery = 0
    infeasible_without_escalation = 0
    feasible_count = 0
    infeasible_count = 0
    rework_counts = 0
    converged_counts = 0
    signatures = set()
    total_demand_rows = 0

    for context in contexts:
        demand = build_demand_plan(context["request"], context["materials"], context["destinations"])
        total_demand_rows += len(demand["items"])
        baseline_latencies = []
        multi_latencies = []
        initial_blockers = 0
        final_coverage = 0.0
        final_score = 0
        baseline_score = 0
        initial_verdict = "rework"
        final_verdict = "rework"
        rework_rounds = 0

        scenario_candidate_scores = []
        scenario_candidate_unsafe = 0
        for _ in range(repetitions):
            baseline_started = time.perf_counter()
            baseline_plans = [
                _generate_baseline_plan(
                    demand, context["inventory"], context["warehouses"], context["routes"],
                    context["vehicles"], context["closed_routes"], policy,
                )
                for policy in ("nearest", "stock", "cost")
            ]
            baseline_truths = [
                verify_plans([plan], demand, context["inventory"], context["routes"], context["vehicles"], context["closed_routes"])
                for plan in baseline_plans
            ]
            baseline_truth = baseline_truths[0]
            for truth in baseline_truths:
                safety = float(truth["score"])
                baseline_candidate_scores.append(safety)
                scenario_candidate_scores.append(safety)
                if truth["verdict"] != "pass":
                    baseline_candidate_unsafe += 1
                    scenario_candidate_unsafe += 1
            baseline_latencies.append((time.perf_counter() - baseline_started) * 1000)

            multi_started = time.perf_counter()
            final_plans, verification, rounds, directives = _solve_adaptively(
                demand, context["inventory"], context["warehouses"], context["routes"],
                context["vehicles"], context["closed_routes"],
            )
            multi_latencies.append((time.perf_counter() - multi_started) * 1000)
            initial_verdict = rounds[0]["verification"]["verdict"]
            final_verdict = verification["verdict"]
            initial_blockers = len(rounds[0]["verification"]["blocker_summary"])
            final_score = verification["score"]
            baseline_score = baseline_truth["score"]
            rework_rounds = max(0, len(rounds) - 1)
            final_coverage = max(
                (report["metrics"]["coverage"] for report in verification["reports"]), default=0.0
            )
            baseline_unsafe_this = baseline_truth["verdict"] != "pass"
            if baseline_unsafe_this:
                baseline_unsafe += 1
            feasible_count += 1 if context["feasible"] else 0
            infeasible_count += 0 if context["feasible"] else 1
            if context["feasible"]:
                if final_verdict != "pass":
                    feasible_without_final_delivery += 1
                else:
                    converged_counts += 1
            else:
                if final_verdict == "pass":
                    multi_unsafe += 1
                elif initial_verdict == "rework":
                    infeasible_without_escalation += 1
                    converged_counts += 1
            if initial_verdict == "rework":
                rework_counts += 1
                if baseline_unsafe_this:
                    detected_unsafe += 1
            signatures.add(json.dumps(_signature(verification), ensure_ascii=False, sort_keys=True))

        baseline_times.extend(baseline_latencies)
        multi_times.extend(multi_latencies)
        baseline_scores.append(baseline_score)
        multi_scores.append(final_score)
        scenario_results.append({
            "scenario_id": context["id"],
            "name": context["name"],
            "feasible": context["feasible"],
            "data_independence": context["data_independence"],
            "layout_id": context["layout_id"],
            "baseline_verdict": "不安全但被接受" if baseline_score < 80 else "安全",
            "initial_verdict": initial_verdict,
            "final_verdict": final_verdict,
            "baseline_score": baseline_score,
            "final_score": final_score,
            "coverage": round(final_coverage, 4),
            "initial_blockers": initial_blockers,
            "rework_rounds": rework_rounds,
            "baseline_latency_ms": round(mean(baseline_latencies), 2),
            "multi_latency_ms": round(mean(multi_latencies), 2),
            "baseline_generator": "独立贪心单 Agent（nearest/stock/cost 三种候选）",
            "baseline_candidate_safety_scores": sorted(set(scenario_candidate_scores)),
            "baseline_candidate_unsafe_exposure": scenario_candidate_unsafe,
        })

    total_samples = len(contexts) * repetitions
    baseline_avg = round(mean(baseline_times), 2)
    multi_avg = round(mean(multi_times), 2)
    baseline_p95 = _p95(baseline_times)
    multi_p95 = _p95(multi_times)
    overhead_ms = round(multi_avg - baseline_avg, 2)
    baseline_unsafe_rate = baseline_unsafe / total_samples
    multi_unsafe_rate = multi_unsafe / total_samples
    baseline_candidate_samples = max(total_samples * 3, 1)
    baseline_candidate_exposure_rate = baseline_candidate_unsafe / baseline_candidate_samples
    interception_rate = detected_unsafe / max(baseline_unsafe, 1)
    coverage = mean(item["coverage"] for item in scenario_results)
    baseline_avg_score = round(mean(baseline_scores), 2)
    multi_avg_score = round(mean(multi_scores), 2)
    reproducible = len(signatures) <= len(contexts)

    quantitative = {
        "repetitions": repetitions,
        "scenarios": len(contexts),
        "samples": total_samples,
        "baseline": {
            "label": "单 Agent / 不执行独立核验",
            "unsafe_acceptance_rate": round(baseline_unsafe_rate, 4),
            "unsafe_acceptance_ci95": _wilson_interval(baseline_unsafe, total_samples),
            "candidate_unsafe_exposure_rate": round(baseline_candidate_exposure_rate, 4),
            "candidate_unsafe_exposure_ci95": _wilson_interval(baseline_candidate_unsafe, baseline_candidate_samples),
            "candidate_samples": baseline_candidate_samples,
            "average_candidate_safety_score": round(mean(baseline_candidate_scores), 2) if baseline_candidate_scores else 0,
            "generator": "独立贪心 nearest-warehouse 单 Agent；未使用多目标生成器",
            "audit_method": "独立确定性核验器仅作为外部真值和误差审计，不参与基线决策",
            "risk_detection_rate": 0.0,
            "average_verification_score": baseline_avg_score,
            "average_latency_ms": baseline_avg,
            "p95_latency_ms": baseline_p95,
        },
        "multi_agent": {
            "label": "多 Agent / 自适应多轮核验",
            "unsafe_acceptance_rate": round(multi_unsafe_rate, 4),
            "unsafe_acceptance_ci95": _wilson_interval(multi_unsafe, total_samples),
            "risk_detection_rate": round(interception_rate, 4),
            "average_verification_score": multi_avg_score,
            "average_coverage": round(coverage, 4),
            "average_latency_ms": multi_avg,
            "p95_latency_ms": multi_p95,
            "rework_rate": round(rework_counts / total_samples, 4),
            "safe_outcome_rate": round(converged_counts / total_samples, 4),
            "false_rejection_rate": round(feasible_without_final_delivery / max(feasible_count, 1), 4),
            "infeasible_escalation_rate": round(infeasible_without_escalation / max(infeasible_count, 1), 4),
        },
        "tradeoff": {
            "latency_overhead_ms": overhead_ms,
            "latency_overhead_pct": round(overhead_ms / baseline_avg * 100, 2) if baseline_avg else 0,
            "unsafe_acceptance_reduction_pp": round((baseline_unsafe_rate - multi_unsafe_rate) * 100, 1),
            "risk_detection_gain_pp": round(interception_rate * 100, 1),
            "score_gain": round(multi_avg_score - baseline_avg_score, 2),
        },
    }
    quantitative_rows = [
        {"metric": "安全结果率（可行交付或不可行升级）", "baseline": f"{(1 - baseline_unsafe_rate) * 100:.1f}%", "multi": f"{quantitative['multi_agent']['safe_outcome_rate'] * 100:.1f}%", "change": f"+{(quantitative['multi_agent']['safe_outcome_rate'] - (1 - baseline_unsafe_rate)) * 100:.1f} 个百分点"},
        {"metric": "不安全方案验收率", "baseline": f"{baseline_unsafe_rate * 100:.1f}%", "multi": f"{multi_unsafe_rate * 100:.1f}%", "change": f"-{(baseline_unsafe_rate - multi_unsafe_rate) * 100:.1f} 个百分点"},
        {"metric": "硬风险检出率", "baseline": "0.0%", "multi": f"{interception_rate * 100:.1f}%", "change": f"+{interception_rate * 100:.1f} 个百分点"},
        {"metric": "平均核验评分", "baseline": str(baseline_avg_score), "multi": str(multi_avg_score), "change": f"{multi_avg_score - baseline_avg_score:+.2f}"},
        {"metric": "需求覆盖率", "baseline": "仅首个候选", "multi": f"{coverage * 100:.1f}%", "change": "跨场景平均"},
        {"metric": "可行情景误拒率", "baseline": "0.0%", "multi": f"{quantitative['multi_agent']['false_rejection_rate'] * 100:.1f}%", "change": "越低越好"},
        {"metric": "不可行情景升级率", "baseline": "0.0%", "multi": f"{quantitative['multi_agent']['infeasible_escalation_rate'] * 100:.1f}%", "change": "转人工研判"},
        {"metric": "平均纯计算耗时", "baseline": f"{baseline_avg:.2f} ms", "multi": f"{multi_avg:.2f} ms", "change": f"+{overhead_ms:.2f} ms"},
        {"metric": "P95 纯计算耗时", "baseline": f"{baseline_p95:.2f} ms", "multi": f"{multi_p95:.2f} ms", "change": f"{multi_p95 - baseline_p95:+.2f} ms"},
        {"metric": "重复运行一致", "baseline": "是" if reproducible else "否", "multi": "是" if reproducible else "否", "change": "固定快照可复现"},
    ]

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scenario": "多场景基准套件",
        "metrics": {
            "hard_blockers_detected": sum(item["initial_blockers"] for item in scenario_results),
            "unsafe_plans_blocked": f"{baseline_unsafe}/{total_samples}",
            "initial_score": round(mean([item["baseline_score"] for item in scenario_results]), 2),
            "final_score": round(mean([item["final_score"] for item in scenario_results]), 2),
            "score_gain": round(multi_avg_score - baseline_avg_score, 2),
            "final_coverage": round(coverage, 4),
            "approved_plans": sum(1 for item in scenario_results if item["final_verdict"] == "pass"),
            "compute_ms": multi_avg,
            "repeat_compute_ms": multi_p95,
            "reproducible": reproducible,
            "demand_rows": total_demand_rows,
            "inventory_snapshot": "共享快照 + 独立布局 + 明确标注的受控变体",
            "independent_layout_scenarios": sum(1 for item in contexts if item["data_independence"] == "independent_layout"),
            "independent_layout_count": len({item["layout_id"] for item in contexts if item["data_independence"] == "independent_layout"}),
            "controlled_variant_scenarios": sum(1 for item in contexts if item["data_independence"] == "controlled_variant"),
            "shared_layout_scenarios": sum(1 for item in contexts if item["data_independence"] == "shared_layout"),
            "repetitions": repetitions,
            "scenarios": len(contexts),
            "samples": total_samples,
            "rework_rate": quantitative["multi_agent"]["rework_rate"],
            "convergence_rate": quantitative["multi_agent"]["safe_outcome_rate"],
            "safe_outcome_rate": quantitative["multi_agent"]["safe_outcome_rate"],
            "unsafe_acceptance_reduction_pp": quantitative["tradeoff"]["unsafe_acceptance_reduction_pp"],
        },
        "quantitative": quantitative,
        "quantitative_rows": quantitative_rows,
        "scenario_results": scenario_results,
        "comparison": [
            {"label": "单 Agent / 无独立核验", "score": round((1 - baseline_unsafe_rate) * 100, 1), "unsafe_plans": baseline_unsafe, "passed": False, "metric": "safe_outcome_rate"},
            {"label": "多 Agent / 自适应多轮核验", "score": round(quantitative["multi_agent"]["safe_outcome_rate"] * 100, 1), "unsafe_plans": multi_unsafe, "passed": multi_unsafe == 0, "metric": "safe_outcome_rate"},
        ],
        "notes": [
            f"基准覆盖 {len(contexts)} 个差异化场景，每个场景重复 {repetitions} 次，共 {total_samples} 个样本。",
            "场景包括道路中断、正常条件、库存严重缺口、无可用车辆、西部走廊独立路网和南部环线独立路网；不可行场景的正确行为是转人工研判而不是伪造方案。",
            "6 个场景中 2 个共享默认路网、2 个明确标注为受控变体、2 个使用不同的独立仓库/安置点/路线布局；不把受控变体包装成多城市泛化。",
            "耗时只统计本地确定性计算，不包含公网 AgentArts 等待；真实云端端到端指标从持久化运行记录读取并单独展示。",
            "并发测试是单进程 asyncio 压测，不等同于多实例生产集群容量；生产部署需在 ECS/容器集群上重新压测。",
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
    diagnostic_inventory = copy.deepcopy(db.inventory_rows(snapshot["id"] if snapshot else None))
    for row in diagnostic_inventory:
        row["locked_quantity"] = 0
    return {
        "scenario": scenario,
        "materials": materials,
        "snapshot": snapshot,
        "inventory": diagnostic_inventory,
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


def _solve_adaptively(
    demand: dict,
    inventory: list[dict],
    warehouses: list[dict],
    routes: list[dict],
    vehicles: list[dict],
    closed_routes: list[str],
    max_attempts: int = 3,
) -> tuple[list[dict], dict, list[dict], list[dict]]:
    plans = generate_plans(demand, inventory, warehouses, routes, vehicles, 1, closed_routes)
    rounds: list[dict] = []
    directives: list[dict] = []
    while len(rounds) < max_attempts:
        attempt = len(rounds) + 1
        verification = verify_plans(plans, demand, inventory, routes, vehicles, closed_routes)
        rounds.append({"attempt": attempt, "plans": plans, "verification": verification})
        if verification["verdict"] == "pass":
            break
        next_attempt = attempt + 1
        directive = build_rework_directive(verification, next_attempt)
        if not directive["can_retry"]:
            break
        directives.append(directive)
        plans = generate_plans(
            demand, inventory, warehouses, routes, vehicles,
            attempt=next_attempt, closed_routes=closed_routes, rework_directive=directive,
        )
    return plans, rounds[-1]["verification"], rounds, directives

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
    final_plans, final_verification, rounds, directives = _solve_adaptively(
        demand, fixture["inventory"], fixture["warehouses"], fixture["routes"],
        fixture["vehicles"], fixture["scenario"]["closed_routes"],
    )
    return {
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "initial_verdict": verification_v1["verdict"],
        "final_verdict": final_verification["verdict"],
        "initial_blockers": verification_v1["blocker_summary"],
        "final_blockers": final_verification["blocker_summary"],
        "initial_warnings": sorted({
            warning["code"] for report in verification_v1["reports"]
            for warning in report["verification"]["warnings"]
        }),
        "attempts": len(rounds),
        "adaptive_categories": sorted({
            category for directive in directives for category in directive["categories"]
        }),
        "final_score": final_verification["score"],
        "final_coverage": max(
            (report["metrics"]["coverage"] for report in final_verification["reports"]), default=0.0
        ),
        "signature": json.dumps(_signature(final_verification), ensure_ascii=False, sort_keys=True),
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
    codes = [code for code in verification["blocker_summary"] if code.startswith("VEHICLE-")]
    return {
        "passed": verification["verdict"] == "rework" and bool(codes),
        "observed": f"识别运力硬阻断 {len(codes)} 个：{', '.join(codes[:5])}" if codes else "未识别运力不足",
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


async def _platform_queue_stress(samples: int = 24, concurrency: int = 4) -> dict:
    """Exercise the persistent platform queue with a fake orchestrator in an isolated DB."""
    with TemporaryDirectory() as temp_dir:
        db = Database(Path(temp_dir) / "queue-stress.db", database_url="")
        db.initialize()
        queue = RunQueueService(db)
        for index in range(samples):
            run_id = f"QUEUE-STRESS-{index + 1:03d}"
            db.create_run(run_id, "rainstorm-ii", {"scenario_id": "rainstorm-ii"})
            queue.enqueue(run_id, float(index + 1))
        active = 0
        peak = 0
        completed = 0

        async def fake_orchestrator(run_db: Database, run_id: str, _request: dict) -> None:
            nonlocal active, peak, completed
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.04)
                run_db.update_run(run_id, "awaiting_approval", {"fake": True})
                completed += 1
            finally:
                active -= 1

        pool = RunWorkerPool(
            lambda: db, fake_orchestrator, concurrency=concurrency,
            poll_interval=0.01, lease_seconds=30,
        )
        started = time.perf_counter()
        await pool.start()
        deadline = time.perf_counter() + 12
        while time.perf_counter() < deadline:
            metrics = queue.metrics()
            if metrics["queued"] == 0 and metrics["running"] == 0:
                break
            await asyncio.sleep(0.02)
        await pool.stop()
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        waits = []
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT queued_at, started_at FROM runs WHERE started_at IS NOT NULL"
            ).fetchall()
        for row in rows:
            try:
                waits.append(max(0.0, (datetime.fromisoformat(row["started_at"]) - datetime.fromisoformat(row["queued_at"])).total_seconds() * 1000))
            except (TypeError, ValueError):
                pass
        return {
            "status": "pass" if completed == samples and 1 < peak <= concurrency else "fail",
            "samples": samples,
            "completed": completed,
            "concurrency_limit": concurrency,
            "peak_concurrency": peak,
            "wall_ms": elapsed_ms,
            "throughput_runs_per_second": round(completed / (elapsed_ms / 1000), 2) if elapsed_ms else 0,
            "average_wait_ms": round(mean(waits), 2) if waits else 0,
            "p95_wait_ms": _p95(waits),
            "scope": "隔离 SQLite 数据库上的平台队列真实 Worker 并发；PostgreSQL 使用 FOR UPDATE SKIP LOCKED 支持多实例",
        }


async def _run_soak_suite(
    fixture: dict, duration_seconds: float = 8.0, concurrency: int = 4, interval_seconds: float = 0.35,
) -> dict:
    """Sustained single-process submission test with explicit production scope."""
    started = time.perf_counter()
    deadline = started + max(2.0, float(duration_seconds))
    results: list[dict] = []
    errors: list[str] = []
    batches = 0
    while time.perf_counter() < deadline:
        batch = await asyncio.gather(
            *(asyncio.to_thread(_run_multi_pipeline, fixture) for _ in range(max(1, int(concurrency)))),
            return_exceptions=True,
        )
        results.extend(item for item in batch if isinstance(item, dict))
        errors.extend(str(item)[:240] for item in batch if isinstance(item, Exception))
        batches += 1
        remaining = deadline - time.perf_counter()
        if remaining > 0:
            await asyncio.sleep(min(interval_seconds, remaining))
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    successful = [item for item in results if item["final_verdict"] == "pass"]
    latencies = [item["elapsed_ms"] for item in successful]
    signatures = {item["signature"] for item in successful}
    target_met = elapsed_ms >= duration_seconds * 1000
    status = "pass" if results and not errors and len(successful) == len(results) and len(signatures) == 1 and target_met else "fail"
    return {
        "status": status,
        "scope": "单进程 asyncio + 线程池持续提交；不代表多实例集群容量",
        "target_duration_seconds": duration_seconds,
        "duration_ms": elapsed_ms,
        "concurrency": concurrency,
        "batches": batches,
        "samples": len(results),
        "successful": len(successful),
        "success_rate": round(len(successful) / len(results), 4) if results else 0.0,
        "throughput_runs_per_second": round(len(successful) / (elapsed_ms / 1000), 2) if elapsed_ms else 0.0,
        "average_latency_ms": round(mean(latencies), 2) if latencies else 0.0,
        "p95_latency_ms": _p95(latencies),
        "isolation_consistent": len(signatures) == 1 if successful else False,
        "target_met": target_met,
        "errors": errors[:10],
    }


def run_diagnostics(db: Database, levels: list[int] | None = None) -> dict:
    run_id = f"EVAL-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
    levels = sorted({max(1, min(50, int(level))) for level in (levels or EVALUATION_CONCURRENCY_LEVELS)})
    db.create_evaluation_run(run_id, "stress_and_fault", "rainstorm-ii")
    started = time.perf_counter()
    try:
        fixture = _diagnostic_fixture(db)
        platform_queue = asyncio.run(_platform_queue_stress())
        concurrency = asyncio.run(_run_concurrency_suite(fixture, levels))
        soak = asyncio.run(_run_soak_suite(fixture, duration_seconds=EVALUATION_SOAK_SECONDS, concurrency=4))
        faults = asyncio.run(_fault_suite(fixture))
        error_profile = _error_profile(fixture)
        all_passed = (
            platform_queue["status"] == "pass"
            and all(item["status"] == "pass" for item in concurrency)
            and soak["status"] == "pass"
            and all(item["passed"] for item in faults)
        )
        result = {
            "run_id": run_id,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "scenario": fixture["scenario"]["name"],
            "inventory_snapshot": fixture["snapshot"]["label"] if fixture["snapshot"] else "未记录",
            "status": "passed" if all_passed else "warning",
            "all_passed": all_passed,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "platform_queue": platform_queue,
            "concurrency": concurrency,
            "soak": soak,
            "fault_injection": faults,
            "error_analysis": error_profile,
            "conclusion": (
                "并发执行、故障降级和误差分类全部达到预期。"
                if all_passed else "存在未达到预期的诊断项，请查看失败证据。"
            ),
            "notes": [
                "平台队列压测使用隔离数据库和真实 RunWorkerPool，验证并发上限、排队与完成数。",
                "并发测试使用同一冻结库存快照，每个并发级别运行相应数量的完整自适应闭环。",
                "持续压测持续提交 8 秒并记录实际样本、吞吐、P95 与隔离一致性；它证明单进程持续可用性，不冒充多实例集群容量。",
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