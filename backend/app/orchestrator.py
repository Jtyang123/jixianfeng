from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from uuid import uuid4

import httpx

from .a2a import A2ABus, A2AError
from .agentarts import AgentArtsBridge
from .agents import allowed_rework_policies, apply_llm_priority_adjustments, build_delivery_package, build_demand_plan, build_prompt_adaptation, build_rework_directive, generate_plans, normalize_agentarts_decision, normalize_llm_weight_profile, verify_plans
from .config import A2A_ENABLED, AGENTARTS_TIMEOUT, AGENT_RUNTIME, DEMO_PACING_FACTOR, RETRY_MAX_ATTEMPTS
from .database import Database
from .knowledge_base import KnowledgeRepository, sanitize_agent_payload
from .local_agents import invoke_local_registered_agent
from .llm import LLMService
from .llm_planner import LLMPlanner
from .memory import MemoryRepository
from .registry import AgentRegistryService, apply_agent_load
from .reservations import InventoryReservationService
from .retry_queue import RetryQueueRepository
from .scheduler import AsyncPriorityExecutor, CORE_WORKFLOW, build_dynamic_schedule
from .tools import ToolError, ToolRepository, ToolService
from .workflow_engine import WorkflowEngine, WorkflowRepository

logger = logging.getLogger(__name__)

CORE_AGENT_IDS = {"demand_agent", "matching_agent", "verifier_agent", "delivery_agent"}
STAGE_NAMES = ("demand", "retrieval", "matching", "verification", "a2a", "delivery")
ROOT_CAUSE_MAP = {
    "STOCK": "inventory_shortage",
    "COVERAGE": "coverage_gap",
    "ROAD": "road_closed",
    "VEHICLE": "fleet_insufficient",
    "DATA": "rule_blocked",
    "SCHEMA": "rule_blocked",
    "MISSING": "rule_blocked",
    "INVALID": "rule_blocked",
    "VOTE": "rule_blocked",
    "PLANNER": "llm_failure",
    "LLM": "llm_failure",
    "ENDPOINT": "endpoint_failure",
    "TIMEOUT": "timeout",
}


def append_stage_metric(metrics: list[dict], stage: str, started: float, agent_id: str, attempt: int = 1) -> None:
    ended = time.perf_counter()
    metrics.append({
        "stage": stage,
        "started_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "ended_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "duration_ms": round(max(0.0, (ended - started) * 1000), 2),
        "agent_id": agent_id,
        "attempt": int(attempt),
    })


def complete_stage_metrics(metrics: list[dict]) -> list[dict]:
    existing = {item.get("stage") for item in metrics}
    for stage in STAGE_NAMES:
        if stage not in existing:
            now = datetime.now().astimezone().isoformat(timespec="milliseconds")
            metrics.append({
                "stage": stage, "started_at": now, "ended_at": now, "duration_ms": 0.0,
                "agent_id": "orchestrator", "attempt": 0,
            })
    return metrics


def allocate_stage_overhead(metrics: list[dict], elapsed_ms: float) -> None:
    accounted = sum(float(item.get("duration_ms") or 0) for item in metrics)
    overhead = max(0.0, float(elapsed_ms) - accounted)
    if overhead <= 0:
        return
    delivery = next((item for item in reversed(metrics) if item.get("stage") == "delivery"), None)
    if delivery is None:
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        delivery = {
            "stage": "delivery", "started_at": now, "ended_at": now,
            "duration_ms": 0.0, "agent_id": "orchestrator", "attempt": 0,
        }
        metrics.append(delivery)
    delivery["duration_ms"] = round(float(delivery.get("duration_ms") or 0) + overhead, 2)


def classify_failure_root_causes(codes: list[str]) -> list[str]:
    roots = set()
    for code in codes:
        normalized = str(code or "").upper()
        for prefix, root in ROOT_CAUSE_MAP.items():
            if normalized.startswith(prefix) or prefix in normalized:
                roots.add(root)
                break
    return sorted(roots)

AGENT_NAMES = {
    "orchestrator": "编排控制器",
    "demand_agent": "需求研判智能体",
    "matching_agent": "资源匹配智能体",
    "verifier_agent": "独立核验智能体",
    "verifier_secondary": "第二核验智能体",
    "verification_vote": "多核验投票汇总智能体",
    "rework_policy": "自适应修复策略智能体",
    "delivery_agent": "方案交付智能体",
}


def _parse_json_text(text: str) -> dict | None:
    if not text or not isinstance(text, str):
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def new_run_id() -> str:
    return f"RUN-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:4].upper()}"


def build_executor_priorities(schedule: dict) -> dict[str, int]:
    priorities = {}
    for item in schedule.get("dispatch", []):
        effective = item.get("effective_priority", item.get("priority", 0))
        priorities[str(item["agent_id"])] = int(effective)
    return priorities


def build_ai_decision_matrix(
    agent_decision: dict, traces: dict, verification: dict, extension_reviews: list[dict],
    llm_status: dict, llm_decisions: dict | None = None,
    planner_decision: dict | None = None,
) -> list[dict]:
    llm_decisions = llm_decisions or {}
    demand_trace = traces.get("demand_agent") or {}
    rework_trace = traces.get("rework_policy") or {}
    matching_trace = traces.get("matching_agent") or {}
    verifier_trace = traces.get("verifier_agent") or {}
    delivery_trace = traces.get("delivery_agent") or {}
    demand_parsed = demand_trace.get("parsed") if isinstance(demand_trace, dict) else None
    rework_parsed = rework_trace.get("parsed") if isinstance(rework_trace, dict) else None
    verifier_parsed = verifier_trace.get("parsed") if isinstance(verifier_trace, dict) else None
    delivery_parsed = delivery_trace.get("parsed") if isinstance(delivery_trace, dict) else None
    delivery_audit_only = isinstance(delivery_trace, dict) and str(delivery_trace.get("review_status") or "") == "audit-only"
    d6_applied = bool(delivery_parsed) and not delivery_audit_only
    d6_source = "agentarts-audit-only" if (delivery_parsed and delivery_audit_only) else (delivery_trace.get("mode", "local-template") if isinstance(delivery_trace, dict) else "local-template")
    d6_discarded_fields = delivery_trace.get("discarded_fields", []) if isinstance(delivery_trace, dict) else []
    demand_priority_decision = llm_decisions.get("demand_priority") or {}
    weight_decision = llm_decisions.get("matching_weights") or {}
    planner_decision = planner_decision or {}
    chosen_by = str(agent_decision.get("chosen_by") or "")
    d3_applied = bool(agent_decision.get("applied"))
    d3_source = agent_decision.get("source", "deterministic-fallback")
    d3_boundary = "只能从已通过独立核验的方案集合中选择"
    if chosen_by == "deterministic-scorer":
        d3_applied = False
        d3_source = "deterministic-scorer"
        d3_boundary = "推荐由确定性权重排序给出，LLM 未参与方案选择"
    planner_repair_policies = [str(item) for item in planner_decision.get("repair_policies", []) if item]
    d2_applied = bool(rework_parsed) and rework_trace.get("mode") == "agentarts"
    d2_source = rework_trace.get("mode", "deterministic-fallback") if isinstance(rework_trace, dict) else "deterministic-fallback"
    if planner_decision.get("applied") and planner_repair_policies:
        d2_applied = True
        d2_source = "llm-planner"
    demand_priority_applied = bool(demand_priority_decision.get("applied"))
    demand_priority_advisory = {}
    demand_narrative_applied = False
    if isinstance(demand_parsed, dict):
        demand_priority_advisory = demand_parsed.get("priority_adjustments") or demand_parsed.get("priority_overrides") or {}
        demand_narrative_applied = bool(
            demand_parsed.get("summary") or demand_parsed.get("assumptions") or demand_parsed.get("evidence_refs")
        )
    demand_source = (
        str(demand_priority_decision.get("source") or "llm")
        if demand_priority_applied
        else ("agentarts-advisory-only" if demand_parsed else (demand_trace.get("mode", "local-deterministic") if isinstance(demand_trace, dict) else "local-deterministic"))
    )
    matrix = [
        {
            "id": "D1", "decision": "\u9700\u6c42\u4f18\u5148\u7ea7\u5efa\u8bae\u4e0e\u89e3\u91ca", "level": "AI \u8f85\u52a9",
            "ai_role": "\u8865\u5145\u9700\u6c42\u6458\u8981\u3001\u5047\u8bbe\u4e0e\u8bc1\u636e\u5f15\u7528\uff1b\u8fdc\u7aef\u4f18\u5148\u7ea7\u5efa\u8bae\u4ec5\u4f5c\u5ba1\u8ba1\uff0c\u4e0d\u8ffd\u6539\u5df2\u6838\u9a8c\u65b9\u6848",
            "applied": demand_priority_applied,
            "source": demand_source,
            "narrative_applied": demand_narrative_applied,
            "priority_advisory_received": bool(demand_priority_advisory),
            "priority_advisory_applied": demand_priority_applied,
            "priority_advisory": demand_priority_advisory,
            "boundary": "\u8fdc\u7aef\u4f18\u5148\u7ea7\u5efa\u8bae\u4e0d\u5728\u65b9\u6848\u751f\u6210\u540e\u56de\u5199\uff1b\u7269\u8d44\u6570\u91cf\u3001\u5355\u4f4d\u548c\u5b89\u7f6e\u70b9\u5206\u914d\u4ecd\u7531\u786e\u5b9a\u6027\u5de5\u5177\u8ba1\u7b97",
            "fallback": "\u672c\u5730\u9700\u6c42\u62c6\u5206\u4e0e\u4e3b\u6570\u636e\u6821\u9a8c",
            "local_equivalent": "\u786e\u5b9a\u6027\u9700\u6c42\u62c6\u5206\u3001\u4e3b\u6570\u636e\u6620\u5c04\u3001\u4eba\u53e3\u5206\u914d\u4e0e\u7f6e\u4fe1\u5ea6\u56e0\u5b50\u8ba1\u7b97",
        },
        {
            "id": "D2", "decision": "修复策略优先级选择", "level": "AI 决策",
            "ai_role": "从当前问题允许的策略集合中选择下一条修复路径",
            "applied": d2_applied,
            "source": d2_source,
            "boundary": "只能选择 allowed_policies，硬修复项仍由规则层强制执行",
            "fallback": "按道路、库存、运力、时效问题代码选择默认策略",
            "local_equivalent": "问题代码到允许策略的确定性映射；历史成功策略仅在允许集合内做 tie-break",
        },
        {
            "id": "D3", "decision": "已核验方案推荐", "level": "AI 决策",
            "ai_role": "在通过硬规则的方案中选择最终推荐方案",
            "applied": d3_applied,
            "source": d3_source,
            "boundary": d3_boundary,
            "fallback": "按速度、成本和风险权重排序",
            "local_equivalent": "对已核验方案按速度、成本、风险权重确定性排序",
        },
        {
            "id": "D4", "decision": "核验解释与风险归纳", "level": "AI 辅助",
            "ai_role": "解释核验问题并补充风险说明",
            "applied": bool(verifier_parsed),
            "source": verifier_trace.get("mode", "deterministic-rules") if isinstance(verifier_trace, dict) else "deterministic-rules",
            "boundary": "硬规则结论不可被模型覆盖",
            "fallback": "确定性规则引擎给出最终 verdict",
            "local_equivalent": "库存、道路、载重、时效和覆盖硬规则引擎",
        },
        {
            "id": "D5", "decision": "扩展 Agent 风险合成", "level": "AI 辅助",
            "ai_role": "补充领域风险和现场提示",
            "applied": bool(extension_reviews),
            "source": "registered-extension-agents" if extension_reviews else "none",
            "boundary": "扩展意见只追加风险，不能删除或修改主方案数字",
            "fallback": "跳过扩展审查，保留确定性风险清单",
            "local_equivalent": "无扩展 Agent 时跳过该层，不伪造扩展结论",
        },
        {
            "id": "D7", "decision": "已核验方案目标权重调整", "level": "AI 决策",
            "ai_role": "在用户权重基础上提出 speed/cost/risk 权重建议，影响已核验方案排序",
            "applied": bool(weight_decision.get("applied")),
            "source": weight_decision.get("source", llm_status.get("model", "deterministic-fallback")),
            "boundary": weight_decision.get("boundary", "用户权重与 LLM 建议各占 50%，只能重排已核验方案"),
            "fallback": "完全使用用户设置的权重",
            "local_equivalent": "用户权重加确定性多目标排序",
        },
        {
            "id": "D6", "decision": "交付摘要组织", "level": "AI 生成",
            "ai_role": "将已核验事实组织成可读摘要",
            "applied": d6_applied,
            "source": d6_source,
            "discarded_fields": d6_discarded_fields,
            "boundary": "仅使用已核验数字和事实",
            "fallback": "本地交付模板与证据链",
            "local_equivalent": "确定性交付模板、方案对比、执行清单、风险提示与证据引用",
        },
        {
            "id": "D8", "decision": "任务计划与工具编排", "level": "AI 决策",
            "ai_role": "决定调用哪些工具、按什么顺序调用、是否重规划",
            "applied": bool(planner_decision.get("applied")),
            "source": planner_decision.get("source", "deterministic-fallback"),
            "steps_executed": planner_decision.get("steps_executed", 0),
            "replans": planner_decision.get("replans", 0),
            "repair_policies": planner_repair_policies,
            "boundary": "只能决定调用哪些工具、按什么顺序、是否重规划；不得计算或修改任何数字",
            "fallback": "固定四步安全流程（需求→生成→核验→交付）",
            "local_equivalent": "确定性 A01→A02→A03→门控→A04 主干工作流",
        },

    ]
    return sorted(matrix, key=lambda item: int(item["id"][1:]))

def emit(
    db: Database,
    run_id: str,
    event_type: str,
    agent_id: str,
    title: str,
    detail: str = "",
    payload: dict | None = None,
) -> None:
    db.add_event(
        run_id=run_id,
        event_type=event_type,
        agent_id=agent_id,
        agent_name=AGENT_NAMES.get(agent_id, agent_id),
        title=title,
        detail=detail,
        payload=payload or {},
    )


async def pace(seconds: float) -> None:
    delay = max(0.0, seconds * DEMO_PACING_FACTOR)
    if delay:
        await asyncio.sleep(delay)


def _cross_warehouse_candidates(demand_plan: dict, inventory: list[dict]) -> list[dict]:
    required: dict[int, float] = {}
    required_weight: dict[int, float] = {}
    for item in demand_plan.get("items", []):
        material_id = int(item["material_id"])
        quantity = float(item.get("quantity") or 0)
        required[material_id] = required.get(material_id, 0.0) + quantity
        required_weight[material_id] = required_weight.get(material_id, 0.0) + quantity * float(item.get("unit_weight_kg") or 0)
    available: dict[int, dict[str, float]] = {}
    for row in inventory:
        if row.get("status") != "available":
            continue
        material_id = int(row["material_id"])
        qty = max(0.0, float(row.get("quantity") or 0) - float(row.get("locked_quantity") or 0))
        available.setdefault(material_id, {})
        available[material_id][str(row["warehouse_id"])] = available[material_id].get(str(row["warehouse_id"]), 0.0) + qty
    candidates = []
    for material_id, needed in required.items():
        warehouses = available.get(material_id, {})
        total = sum(warehouses.values())
        largest = max(warehouses.values() or [0.0])
        if total + 0.001 >= needed > largest + 0.001:
            candidates.append({
                "material_id": material_id,
                "required": round(needed, 2),
                "required_weight_kg": round(required_weight.get(material_id, 0.0), 2),
                "total_available": round(total, 2),
                "largest_single_warehouse": round(largest, 2),
                "warehouse_count": len([qty for qty in warehouses.values() if qty > 0]),
            })
    return candidates


def _build_cross_warehouse_reply(candidates: list[dict], inventory: list[dict], vehicles: list[dict] | None) -> dict:
    available: dict[int, dict[str, float]] = {}
    for row in inventory:
        if row.get("status") != "available":
            continue
        material_id = int(row["material_id"])
        qty = max(0.0, float(row.get("quantity") or 0) - float(row.get("locked_quantity") or 0))
        if qty <= 0:
            continue
        available.setdefault(material_id, {})
        warehouse_id = str(row["warehouse_id"])
        available[material_id][warehouse_id] = available[material_id].get(warehouse_id, 0.0) + qty

    allocated: list[dict] = []
    allocation_details: list[dict] = []
    total_shortfall = 0.0
    load_by_warehouse: dict[str, float] = {}
    for candidate in candidates:
        material_id = int(candidate["material_id"])
        remaining = float(candidate.get("required") or 0)
        unit_weight = float(candidate.get("required_weight_kg") or 0) / max(remaining, 0.001)
        rows = sorted(available.get(material_id, {}).items(), key=lambda item: item[1], reverse=True)
        material_allocated = []
        for warehouse_id, warehouse_available in rows:
            if remaining <= 0.001:
                break
            qty = round(min(remaining, warehouse_available), 2)
            if qty <= 0:
                continue
            allocated.append({"material_id": material_id, "warehouse_id": warehouse_id, "qty": qty})
            material_allocated.append({"warehouse_id": warehouse_id, "qty": qty})
            load_by_warehouse[warehouse_id] = load_by_warehouse.get(warehouse_id, 0.0) + qty * unit_weight
            remaining -= qty
        shortfall = round(max(0.0, remaining), 2)
        total_shortfall += shortfall
        allocation_details.append({
            "material_id": material_id,
            "required": round(float(candidate.get("required") or 0), 2),
            "allocated": material_allocated,
            "shortfall": shortfall,
        })

    capacity_by_warehouse: dict[str, float] = {}
    for vehicle in vehicles or []:
        if vehicle.get("status") != "available":
            continue
        warehouse_id = str(vehicle.get("warehouse_id") or "")
        capacity_by_warehouse[warehouse_id] = capacity_by_warehouse.get(warehouse_id, 0.0) + float(vehicle.get("capacity_kg") or 0)
    warehouse_loads = [
        {
            "warehouse_id": warehouse_id,
            "load_kg": round(load, 2),
            "capacity_kg": round(capacity_by_warehouse.get(warehouse_id, 0.0), 2),
            "within_capacity": load <= capacity_by_warehouse.get(warehouse_id, 0.0) + 0.001,
        }
        for warehouse_id, load in sorted(load_by_warehouse.items())
    ]
    vehicle_feasible = bool(warehouse_loads) and all(item["within_capacity"] for item in warehouse_loads)
    feasible = total_shortfall <= 0.001 and vehicle_feasible
    return {
        "feasible": feasible,
        "method": "multi-warehouse-greedy-allocation",
        "allocated": allocated,
        "allocation_details": allocation_details,
        "shortfall": round(total_shortfall, 2),
        "vehicle_feasible": vehicle_feasible,
        "warehouse_loads": warehouse_loads,
        "constraint": "必须按仓库可用量、车辆载重和路线时效复核；A2A 只提供协调意见，不覆盖硬规则。",
    }


async def _matching_agent_consumer(
    bus: A2ABus,
    inventory: list[dict],
    vehicles: list[dict],
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        request_message = await bus.receive("matching_agent", timeout=0.1)
        if not request_message or request_message.get("message_type") != "request":
            continue
        candidates = [
            item for item in (request_message.get("content") or {}).get("materials", [])
            if isinstance(item, dict)
        ]
        reply_content = _build_cross_warehouse_reply(candidates, inventory, vehicles)
        await bus.reply(request_message, "matching_agent", reply_content)


async def maybe_a2a_cross_warehouse(
    bus: A2ABus,
    demand_plan: dict,
    inventory: list[dict],
    emit_fallback,
    vehicles: list[dict] | None = None,
) -> dict:
    candidates = _cross_warehouse_candidates(demand_plan, inventory)
    if not candidates:
        return {"used": False, "reason": "no-cross-warehouse-request"}
    stop_event = asyncio.Event()
    consumer = asyncio.create_task(
        _matching_agent_consumer(bus, inventory, vehicles or [], stop_event),
        name="matching-agent-a2a-consumer",
    )
    try:
        reply_message = await bus.request(
            "demand_agent", "matching_agent",
            {
                "question": "单仓库存不足时，能否从多个仓库协同调拨并完成跨仓分配？",
                "materials": candidates,
            },
            timeout=5.0,
        )
        if reply_message is None:
            raise A2AError("A2A 协商超时")
        content = reply_message.get("content") or {}
        return {
            "used": True,
            "mode": "a2a",
            "feasible": bool(content.get("feasible")),
            "request_id": reply_message.get("reply_to", ""),
            "reply_id": reply_message.get("message_id", ""),
            "correlation_id": reply_message.get("correlation_id", ""),
            "materials": candidates,
            "allocated": content.get("allocated", []),
            "allocation_details": content.get("allocation_details", []),
            "shortfall": content.get("shortfall", 0),
            "warehouse_loads": content.get("warehouse_loads", []),
        }
    except Exception as exc:
        emit_fallback(exc)
        return {
            "used": True,
            "mode": "blackboard-fallback",
            "feasible": None,
            "reason": str(exc)[:240],
            "materials": candidates,
            "allocated": [],
        }
    finally:
        stop_event.set()
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


def _load_repair_experiences(memory_repo: MemoryRepository, verification: dict, scenario_tag: str) -> list[dict]:
    codes = [str(code) for code in verification.get("blocker_summary", []) if str(code)]
    return memory_repo.find_repair_experiences(codes, scenario_tag, min_success_rate=0.6, limit=3)


def _record_repair_experiences(
    memory_repo: MemoryRepository,
    directives: list[dict],
    rounds: list[dict],
    scenario_tag: str,
    success: bool,
    run_id: str,
) -> list[dict]:
    strategy_by_policy = {
        "route_failover": "排除事件封闭路线，比较主备通道的 ETA 与风险后再分配。",
        "multi_warehouse": "保留总库存核验，同时按分仓可用量拆分并校验跨仓调拨。",
        "fleet_rebalance": "按仓库可用车辆、载重与趟次重新平衡运力。",
        "deadline_first": "提高截止时间惩罚，优先保障 P0 时限并显式报告超时。",
        "data_quality_repair": "暂停自动调度，列出缺失或冲突字段并请求人工补全。",
    }
    records: list[dict] = []
    for index, directive in enumerate(directives):
        policy_id = str(directive.get("selected_policy") or "")
        issue_codes = [str(code) for code in directive.get("issue_codes", []) if str(code)]
        if not policy_id or not issue_codes:
            continue
        before_score = float((rounds[index] if index < len(rounds) else {}).get("verification", {}).get("score", 0) or 0)
        after_score = float((rounds[index + 1] if index + 1 < len(rounds) else rounds[-1] if rounds else {}).get("verification", {}).get("score", before_score) or before_score)
        is_last = index == len(directives) - 1
        strategy = strategy_by_policy.get(policy_id, str(directive.get("reason") or policy_id))
        records.extend(memory_repo.record_repair_experience(
            issue_codes=issue_codes,
            scenario_tag=scenario_tag,
            policy_id=policy_id,
            strategy=strategy,
            prompt_fragment=strategy,
            success=bool(success and is_last),
            source_run_id=run_id,
            effect=after_score - before_score,
            superseded=not is_last,
        ))
    return records


async def run_planner_orchestration(
    db: Database,
    run_id: str,
    request: dict,
    scenario: dict,
    materials: list[dict],
    destinations: list[dict],
    warehouses: list[dict],
    routes: list[dict],
    vehicles: list[dict],
    inventory: list[dict],
    evidence: list[dict],
    schedule: dict,
    planner,
    llm_service,
    a2a_bus: A2ABus | None = None,
    post_verification_runner=None,
    stage_metrics: list[dict] | None = None,
    execution_started: float | None = None,
) -> dict:
    planner_started = time.perf_counter()
    stage_metrics = stage_metrics if stage_metrics is not None else []
    tool_agent_map = {
        "demand_plan": "demand_agent",
        "knowledge_search": "demand_agent",
        "inventory_query": "matching_agent",
        "route_query": "matching_agent",
        "vehicle_query": "matching_agent",
        "generate_plans": "matching_agent",
        "verify_plans": "verifier_agent",
        "delivery_package": "delivery_agent",
        "escalate_human": "orchestrator",
    }
    tool_stage_map = {
        "demand_plan": "demand", "knowledge_search": "retrieval",
        "inventory_query": "matching", "route_query": "matching",
        "vehicle_query": "matching", "generate_plans": "matching",
        "verify_plans": "verification", "delivery_package": "delivery",
    }
    tool_repository = ToolRepository(db)
    tool_repository.ensure_builtins()
    registered_tools = {
        item["id"] for item in tool_repository.list_tools(enabled_only=True)
    }
    available_tools = [tool_id for tool_id in tool_agent_map if tool_id in registered_tools]
    available_tool_set = set(available_tools)
    experience_repo = MemoryRepository(db)

    async def timed_planner_call(operation, func, *args):
        started = time.perf_counter()
        try:
            result = await func(*args)
            db.record_agent_performance(
                "planner", run_id, operation, True,
                (time.perf_counter() - started) * 1000, "",
            )
            return result
        except Exception as exc:
            db.record_agent_performance(
                "planner", run_id, operation, False,
                (time.perf_counter() - started) * 1000, type(exc).__name__,
            )
            raise

    def tool_result_summary(tool: str, result: dict) -> dict:
        if tool == "demand_plan":
            return {"items": len(result.get("items", [])), "confidence": result.get("confidence")}
        if tool == "knowledge_search":
            return {"items": len(result.get("items", []))}
        if tool == "inventory_query":
            return {"items": result.get("items", 0), "available_rows": result.get("available_rows", 0), "total_quantity": result.get("total_quantity", 0)}
        if tool == "route_query":
            return {"items": result.get("items", 0), "closed_routes": len(result.get("closed_routes", []))}
        if tool == "vehicle_query":
            return {"items": result.get("items", 0), "available": result.get("available", 0)}
        if tool == "generate_plans":
            plans = result.get("plans", [])
            return {"plan_count": len(plans), "plan_ids": [item.get("plan_id") for item in plans], "metrics": [item.get("metrics", {}) for item in plans]}
        if tool == "verify_plans":
            return {"verdict": result.get("verdict"), "score": result.get("score"), "blockers": result.get("blocker_summary", [])}
        if tool == "delivery_package":
            recommended = result.get("recommended_plan") or {}
            return {"status": result.get("status"), "recommended_plan_id": recommended.get("plan_id"), "alternatives": len(result.get("alternatives", [])), "risk_count": len(result.get("risk_alerts", []))}
        return {"status": result.get("status"), "error": result.get("error")}
    planner_request = {
        "event": request.get("event", {}),
        "scenario": {key: scenario.get(key) for key in ("id", "name", "description", "severity", "affected_population")},
        "demand_items": request.get("demand_items", []),
        "weights": request.get("weights", {}),
        "resource_summary": {
            "warehouses": len(warehouses), "routes": len(routes),
            "vehicles": len(vehicles), "inventory_rows": len(inventory),
        },
        "evidence": [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "score": item.get("score"),
                "excerpt": str(item.get("excerpt") or "")[:240],
            }
            for item in evidence[:7]
        ],
    }
    plan = await timed_planner_call("create_plan", planner.create_plan, planner_request, available_tools)
    emit(db, run_id, "llm.thinking", "planner", "LLM生成任务计划", plan.get("summary", ""), {
        "mode": plan.get("mode"), "steps": plan.get("steps", []), "available_tools": available_tools,
    })
    demand_plan = None
    plans: list[dict] = []
    verification = None
    delivery = None
    rounds: list[dict] = []
    rework_directives: list[dict] = []
    prompt_adaptations: list[dict] = []
    decisions: list[dict] = []
    attempt = 1
    executed_steps = 0
    step_index = 0
    current_plan = plan
    last_decision = {"decision": "continue", "reason": "开始执行LLM计划", "next_tool": None}

    def ensure_demand() -> dict:
        nonlocal demand_plan
        if demand_plan is None:
            demand_plan = build_demand_plan(request, materials, destinations)
            demand_plan["evidence_refs"] = list(dict.fromkeys(demand_plan.get("evidence_refs", []) + [item["id"] for item in evidence]))
            emit(db, run_id, "agent.completed", "demand_agent", "需求工具执行完成", demand_plan["summary"], {
                "items": len(demand_plan["items"]), "confidence": demand_plan["confidence"],
            })
        return demand_plan

    while executed_steps < 16 and step_index < len(current_plan.get("steps", [])):
        step = current_plan["steps"][step_index]
        tool = str(step.get("tool") or "")
        agent_id = tool_agent_map.get(tool, "orchestrator")
        emit(db, run_id, "agent.started", agent_id, f"LLM Planner 调用 {AGENT_NAMES.get(agent_id, agent_id)}", step.get("reason", ""), {
            "runtime": "llm-planner", "step_id": step.get("id"), "tool": tool,
        })
        emit(db, run_id, "llm.thinking", "planner", f"LLM选择工具 · {tool}", step.get("reason", ""), {
            "step_id": step.get("id"), "tool": tool, "input": step.get("input", {}),
        })
        step_started = time.perf_counter()
        result: dict
        if tool not in available_tool_set:
            result = {"error": f"工具不存在或未启用：{tool}"}
        elif tool == "demand_plan":
            result = ensure_demand()
            if a2a_bus is not None:
                a2a_started = time.perf_counter()
                a2a_result = await maybe_a2a_cross_warehouse(
                    a2a_bus, result, inventory,
                    lambda exc: emit(db, run_id, "a2a.fallback", "demand_agent", "A2A 通道不可用，已回退到黑板交接", str(exc)[:240]),
                    vehicles,
                )
                append_stage_metric(stage_metrics, "a2a", a2a_started, "matching_agent", attempt)
                result = {**result, "a2a_exchange": a2a_result}
        elif tool == "inventory_query":
            result = {
                "items": len(inventory),
                "available_rows": sum(1 for item in inventory if item.get("status") == "available"),
                "total_quantity": round(sum(float(item.get("quantity") or 0) for item in inventory), 2),
            }
        elif tool == "route_query":
            result = {"items": len(routes), "closed_routes": request.get("event", {}).get("closed_route_ids", [])}
        elif tool == "vehicle_query":
            result = {"items": len(vehicles), "available": sum(1 for item in vehicles if item.get("status") == "available")}
        elif tool == "knowledge_search":
            result = {"items": evidence}
        elif tool == "generate_plans":
            demand_plan = ensure_demand()
            directive = rework_directives[-1] if rework_directives else None
            plans = generate_plans(
                demand_plan, inventory, warehouses, routes, vehicles,
                attempt=attempt, closed_routes=request.get("event", {}).get("closed_route_ids", []),
                rework_directive=directive,
            )
            emit(db, run_id, "plan.generated", "matching_agent", f"第 {attempt} 轮候选方案生成", "LLM规划调用确定性方案生成工具。", {
                "plans": [{"plan_id": item["plan_id"], "name": item["name"], "metrics": item["metrics"]} for item in plans],
            })
            result = {"plans": plans}
        elif tool == "verify_plans":
            demand_plan = ensure_demand()
            if not plans:
                remaining_tools = current_plan.get("steps", [])[step_index + 1:]
                generate_index = next(
                    (step_index + 1 + offset for offset, item in enumerate(remaining_tools) if item.get("tool") == "generate_plans"),
                    None,
                )
                if generate_index is not None:
                    step_index = generate_index
                    last_decision = {
                        "decision": "continue",
                        "reason": "\u6838\u9a8c\u524d\u7f3a\u5c11\u5019\u9009\u65b9\u6848\uff0c\u8df3\u5230\u5f53\u524d\u8ba1\u5212\u540e\u7eed\u7684\u65b9\u6848\u751f\u6210\u6b65\u9aa4\u3002",
                        "next_tool": "generate_plans",
                    }
                    emit(db, run_id, "llm.thinking", "planner", "\u524d\u7f6e\u6761\u4ef6\u4e0d\u8db3\uff0c\u8df3\u8f6c\u65b9\u6848\u751f\u6210", last_decision["reason"], {
                        "action": "jump_to_generate", "attempt": attempt,
                    })
                    continue
                if attempt >= 3:
                    last_decision = {"decision": "human", "reason": "\u7f3a\u5c11\u5019\u9009\u65b9\u6848\u4e14\u5df2\u8fbe\u5230\u91cd\u89c4\u5212\u4e0a\u9650\u3002", "next_tool": "escalate_human"}
                    emit(db, run_id, "llm.thinking", "planner", "\u524d\u7f6e\u6761\u4ef6\u4e0d\u8db3\uff0c\u8f6c\u4eba\u5de5", last_decision["reason"], {
                        "action": "replan", "attempt": attempt,
                    })
                    break
                attempt += 1
                current_plan = await timed_planner_call(
                    "create_plan", planner.create_plan,
                    {**planner_request, "feedback": {"missing": "plans", "required_action": "generate_plans"}},
                    available_tools,
                )
                step_index = 0
                last_decision = {
                    "decision": "replan",
                    "reason": "\u6838\u9a8c\u524d\u7f3a\u5c11\u5019\u9009\u65b9\u6848\uff0c\u91cd\u65b0\u751f\u6210\u5305\u542b\u65b9\u6848\u751f\u6210\u6b65\u9aa4\u7684\u8ba1\u5212\u3002",
                    "next_tool": "generate_plans",
                }
                emit(db, run_id, "llm.thinking", "planner", "\u524d\u7f6e\u6761\u4ef6\u4e0d\u8db3\uff0c\u91cd\u65b0\u89c4\u5212", last_decision["reason"], {
                    "action": "replan", "attempt": attempt,
                })
                continue
            verification = verify_plans(
                plans, demand_plan, inventory, routes, vehicles,
                request.get("event", {}).get("closed_route_ids", []),
            )
            rounds.append({
                "attempt": attempt, "plans": plans, "verification": verification,
                "mode": "llm-react", "rework_directive": rework_directives[-1] if rework_directives else None,
            })
            emit(
                db, run_id, "verification.passed" if verification["verdict"] == "pass" else "verification.failed",
                "verifier_agent", f"第 {attempt} 轮核验完成",
                f"核验结论：{verification['verdict']}，评分 {verification['score']}。", verification,
            )
            result = verification
        elif tool == "delivery_package":
            if not verification or verification.get("verdict") != "pass":
                delivery = None
                result = {"status": "blocked", "reason": "核验未通过，禁止生成可执行交付包。"}
            else:
                delivery = build_delivery_package(
                    verification, scenario, db.latest_snapshot(), request.get("weights", {}),
                    agent_decision={"applied": True, "recommended_plan_id": None, "source": "llm-planner", "rationale": "LLM Planner完成决策。"},
                )
                result = delivery
        elif tool == "escalate_human":
            result = {"status": "human_escalation", "reason": step.get("reason", "")}
        else:
            result = {"error": f"未知工具：{tool}"}
        executed_steps += 1
        step_latency_ms = (time.perf_counter() - step_started) * 1000
        if tool in tool_stage_map:
            append_stage_metric(stage_metrics, tool_stage_map[tool], step_started, agent_id, attempt)
        result_summary = tool_result_summary(tool, result if isinstance(result, dict) else {"result": result})
        tool_status = "failed" if isinstance(result, dict) and result.get("error") else "success"
        tool_repository.record_call(
            f"planner:{tool}", run_id, tool_status, step.get("input", {}), result_summary, step_latency_ms,
        )
        emit(db, run_id, "agent.completed", agent_id, f"LLM Planner 完成 {AGENT_NAMES.get(agent_id, agent_id)}", "工具结果已返回。", {
            "runtime": "llm-planner", "step_id": step.get("id"), "tool": tool,
            "status": tool_status, "latency_ms": round(step_latency_ms, 2), "summary": result_summary,
        })
        remaining = current_plan.get("steps", [])[step_index + 1:]
        remaining_decision = [
            {"id": item.get("id"), "tool": item.get("tool")}
            for item in remaining
        ]
        if tool == "delivery_package" and delivery is not None:
            db.record_agent_performance("planner", run_id, "decide", True, 0.0, "")
            decision = {
                "mode": "llm-planner", "decision": "stop", "next_tool": None,
                "reason": "交付包已生成，ReAct 循环结束。", "repair_policy": "",
            }
        elif tool == "verify_plans" and verification and verification.get("verdict") == "pass":
            db.record_agent_performance("planner", run_id, "decide", True, 0.0, "")
            next_delivery = next((item for item in remaining_decision if item.get("tool") == "delivery_package"), None)
            decision = {
                "mode": "llm-planner",
                "decision": "continue" if next_delivery else "stop",
                "next_tool": "delivery_package" if next_delivery else None,
                "reason": "核验已通过，进入交付步骤。",
                "repair_policy": "",
            }
        else:
            decision = await timed_planner_call(
                "decide", planner.decide,
                {"tool": tool, "result": result_summary, "status": tool_status},
                remaining_decision,
                {
                    "attempt": attempt,
                    "verification_verdict": (verification or {}).get("verdict"),
                    "has_delivery": delivery is not None,
                    "executed_steps": executed_steps,
                    "last_reason": last_decision.get("reason", ""),
                },
            )
        if attempt > 1 and tool != "verify_plans" and decision.get("decision") == "replan":
            next_step = remaining_decision[0] if remaining_decision else None
            decision = {
                "mode": "llm-planner",
                "decision": "continue" if next_step else "stop",
                "next_tool": next_step.get("tool") if next_step else None,
                "reason": "重规划已生效，按新计划继续执行，避免重复重规划。",
                "repair_policy": "",
            }
        decisions.append({"step": step, "decision": decision})
        last_decision = decision
        emit(db, run_id, "llm.thinking", "planner", "LLM判断工具结果", decision.get("reason", ""), decision)

        if tool == "verify_plans" and verification and verification.get("verdict") != "pass":
            allowed = allowed_rework_policies(verification)
            selected_policy = decision.get("repair_policy") or next(
                (item["id"] for item in allowed if item["id"] != "escalate_human"), "escalate_human"
            )
            if attempt >= 3 or selected_policy == "escalate_human":
                last_decision = {"decision": "human", "reason": "达到自适应重算上限或没有安全修复策略。", "next_tool": "escalate_human"}
                break
            directive = build_rework_directive(verification, attempt + 1, selected_policy=selected_policy)
            experience_context = _load_repair_experiences(
                experience_repo, verification, str(scenario.get("id") or "generic")
            )
            directive["experience_context"] = experience_context
            adaptation = build_prompt_adaptation(verification, directive)
            directive["prompt_adaptation"] = adaptation
            rework_directives.append(directive)
            prompt_adaptations.append(adaptation)
            attempt += 1
            current_plan = await timed_planner_call(
                "create_plan", planner.create_plan,
                {**planner_request, "feedback": {
                    "issue_codes": verification.get("blocker_summary", []),
                    "required_fixes": verification.get("required_fixes", []),
                    "experience_context": experience_context,
                }},
                available_tools,
            )
            emit(db, run_id, "llm.thinking", "planner", "LLM触发重规划", "核验未通过，基于问题代码重新生成工具计划。", {
                "blockers": verification.get("blocker_summary", []),
                "repair_policy": selected_policy,
                "adaptive_prompt": adaptation,
                "experience_hits": len(experience_context),
            })
            step_index = 0
            continue
        if decision.get("decision") in {"stop", "human"}:
            if delivery is not None or tool in {"escalate_human", "delivery_package"}:
                break
            if verification and verification.get("verdict") == "pass" and delivery is None:
                delivery = build_delivery_package(
                    verification, scenario, db.latest_snapshot(), request.get("weights", {}),
                    agent_decision={"applied": True, "recommended_plan_id": None, "source": "llm-planner", "rationale": "LLM Planner 已完成核验，系统自动生成交付包。"},
                )
                last_decision = {"decision": "stop", "reason": "方案已通过核验，交付包已生成。", "next_tool": None}
                break
            break
        if decision.get("decision") == "replan":
            current_plan = await timed_planner_call(
                "create_plan", planner.create_plan,
                {**planner_request, "feedback": {"last_decision": decision, "last_tool": tool}},
                available_tools,
            )
            step_index = 0
            continue
        next_tool = decision.get("next_tool")
        if next_tool and next_tool != "stop":
            next_index = next(
                (index for index, item in enumerate(current_plan.get("steps", [])[step_index + 1:], start=step_index + 1) if item.get("tool") == next_tool),
                step_index + 1,
            )
            step_index = next_index
        else:
            step_index += 1

    if delivery is None:
        final_verification = verification or {"verdict": "rework", "score": 0, "reports": [], "blocker_summary": ["PLANNER-INCOMPLETE"], "required_fixes": ["LLM计划未完成核验与交付"]}
        delivery = build_delivery_package(final_verification, scenario, db.latest_snapshot(), request.get("weights", {}))
    else:
        final_verification = verification or {"verdict": "rework", "score": 0, "reports": [], "blocker_summary": [], "required_fixes": []}
    extension_reviews: list[dict] = []
    extension_workflow_runs: list[dict] = []
    planner_agentarts_trace: dict = {}
    if final_verification.get("verdict") == "pass" and post_verification_runner and plans:
        try:
            extension_result = await post_verification_runner(demand_plan or {}, plans, final_verification)
            extension_reviews = list(extension_result.get("extension_reviews") or [])
            extension_workflow_runs = list(extension_result.get("extension_workflow_runs") or [])
            planner_agentarts_trace = dict(extension_result.get("agentarts_trace") or {})
        except Exception as exc:
            logger.exception("post-verification extension review failed for run %s", run_id)
            extension_workflow_runs.append({
                "workflow_id": "post_verification-extension",
                "workflow_name": "核验后扩展审查",
                "stage": "post_verification",
                "status": "failed",
                "error": str(exc)[:500],
            })
    delivery["extension_reviews"] = extension_reviews
    for review in extension_reviews:
        parsed = review.get("parsed") or {}
        risks = parsed.get("risks") or parsed.get("warnings") or parsed.get("risk_alerts") or []
        if isinstance(risks, str):
            risks = [risks]
        for risk in risks:
            risk_text = risk if isinstance(risk, str) else json.dumps(risk, ensure_ascii=False)
            delivery.setdefault("risk_alerts", []).append(f"{review.get('agent_name', review.get('agent_id', '扩展 Agent'))}：{risk_text}")
    recommended = delivery.get("recommended_plan") or {}
    agent_decision = {
        "applied": bool(recommended),
        "recommended_plan_id": recommended.get("plan_id"),
        "recommended_strategy": None,
        "rationale": last_decision.get("reason", ""),
        "source": "deterministic-scorer",
        "chosen_by": "deterministic-scorer",
    }
    planner_decision = {
        "applied": plan.get("mode") == "llm-planner",
        "source": "llm-planner" if plan.get("mode") == "llm-planner" else "deterministic-fallback",
        "steps_executed": executed_steps,
        "replans": len(rework_directives),
        "repair_policies": [item.get("selected_policy") for item in rework_directives if item.get("selected_policy")],
    }
    ai_matrix = build_ai_decision_matrix(
        agent_decision, planner_agentarts_trace, final_verification, extension_reviews, llm_service.status, {},
        planner_decision=planner_decision,
    )
    experience_writes = _record_repair_experiences(
        experience_repo, rework_directives, rounds, str(scenario.get("id") or "generic"),
        final_verification.get("verdict") == "pass", run_id,
    )
    allocate_stage_overhead(stage_metrics, (time.perf_counter() - (execution_started or planner_started)) * 1000)
    complete_stage_metrics(stage_metrics)
    failure_root_causes = classify_failure_root_causes(final_verification.get("blocker_summary", []))
    emit(db, run_id, "run.stage_metrics", "orchestrator", "阶段耗时统计完成", f"共 {len(stage_metrics)} 条阶段记录。", {
        "stage_metrics": stage_metrics,
        "failure_root_causes": failure_root_causes,
    })
    return {
        "run_id": run_id,
        "runtime": "llm-planner",
        "runtime_label": "LLM ReAct Planner + 确定性工具安全执行",
        "scenario": scenario,
        "event": request["event"],
        "closed_routes": request.get("event", {}).get("closed_route_ids", []),
        "demand": demand_plan or {},
        "rounds": rounds,
        "final_verification": final_verification,
        "delivery": delivery,
        "rework_directives": rework_directives,
        "prompt_adaptations": prompt_adaptations,
        "active_prompt_versions": {"planner": "llm-react-v1", "matching_agent": "v1.4", "verifier_agent": "v1.5", "delivery_agent": "v1.2"},
        "loop_metrics": {
            "attempts": len(rounds), "max_attempts": 3,
            "initial_score": rounds[0]["verification"]["score"] if rounds else 0,
            "final_score": final_verification.get("score", 0),
            "score_gain": (final_verification.get("score", 0) - rounds[0]["verification"]["score"]) if rounds else 0,
            "blockers_found": len(rounds[0]["verification"].get("blocker_summary", [])) if rounds else 0,
            "final_blockers": len(final_verification.get("blocker_summary", [])),
            "adaptive_categories": sorted({category for item in rework_directives for category in item.get("categories", [])}),
            "adaptive_prompt_versions": [item["active_version"] for item in prompt_adaptations],
            "adaptive_tool_overrides": list(dict.fromkeys(override for item in rework_directives for override in item.get("tool_overrides", []))),
            "workflow_paths": ["llm-react"],
            "selected_repair_policies": [item.get("selected_policy", "") for item in rework_directives],
            "rework_rounds": len(rework_directives),
            "converged": final_verification.get("verdict") == "pass",
            "exhausted": final_verification.get("verdict") != "pass" and executed_steps >= 16,
        },
        "retrieval": evidence,
        "agent_decision": agent_decision,
        "ai_decision_matrix": ai_matrix,
        "planner_decision": planner_decision,
        "planner_trace": {"initial_plan": plan, "decisions": decisions, "executed_steps": executed_steps},
        "stage_metrics": stage_metrics,
        "failure_root_causes": failure_root_causes,
        "repair_experience_writes": experience_writes,
        "orchestration": {"engine": "LLM ReAct planner with deterministic tools", "schedule": schedule, "max_steps": 16},
        "agentarts_trace": planner_agentarts_trace,
        "extension_reviews": extension_reviews,
        "extension_workflow_runs": extension_workflow_runs,
        "ai_reasoning": {},
        "data_mode": "SIMULATED",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

async def orchestrate(db: Database, run_id: str, request: dict) -> None:
    try:
        llm_service = LLMService(db, run_id)
    except TypeError:
        llm_service = LLMService()
    agentarts = AgentArtsBridge()
    http_client = httpx.AsyncClient(timeout=AGENTARTS_TIMEOUT) if agentarts.enabled else None
    registry = AgentRegistryService(db)
    memory_repo = MemoryRepository(db)
    memory_context: dict[str, dict[str, dict]] = {}
    memory_writes: list[dict] = []
    for agent_id, endpoint in registry.active_endpoints().items():
        agentarts.override_endpoint(agent_id, endpoint)
    runtime_metrics = registry.runtime_metrics()
    ai_reasoning: dict = {}
    llm_decisions: dict = {}
    agent_decision: dict = {"applied": False, "rationale": "AgentArts 未配置，使用确定性排序。"}
    agentarts_trace: dict = {}
    extension_agents = [
        item for item in registry.list_agents()
        if item["id"] not in CORE_AGENT_IDS and item.get("enabled") and item.get("endpoint_configured")
    ][:5]
    workflow_repo = WorkflowRepository(db)
    all_extension_workflows = workflow_repo.list_workflows(enabled_only=True)
    extension_workflows = [item for item in all_extension_workflows if item["stage"] == "post_verification"]
    core_workflow = next((item for item in all_extension_workflows if item["stage"] == "core"), CORE_WORKFLOW)
    extension_reviews = []
    extension_workflow_runs = []
    agent_executor: AsyncPriorityExecutor | None = None
    background_tasks: set[asyncio.Task] = set()

    def track_background_task(coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        background_tasks.add(task)

        return task

    async def call_agentarts(agent_id: str, payload: dict, trace_id: str | None = None) -> dict:
        if not agentarts.enabled:
            return {"mode": "local-fallback", "reason": "AgentArts 未配置"}
        endpoint_map = getattr(agentarts, "endpoints", None)
        shared_base_url = getattr(agentarts, "base_url", "")
        if endpoint_map is not None and agent_id not in endpoint_map and not shared_base_url:
            local = {
                "mode": "local-configured",
                "runtime": "huaweicloud-agentarts",
                "agent_id": agent_id,
                "reason": "未配置端点，使用本地确定性执行",
            }
            agentarts_trace[trace_id or agent_id] = local
            emit(
                db, run_id, "agent.local_fallback", agent_id,
                "本地执行（未配置端点）",
                "该 Agent 未配置独立云端端点，已直接使用本地确定性执行。",
                local,
            )
            return local
        safe_payload = sanitize_agent_payload(payload)
        started_at = time.perf_counter()
        endpoint = ""
        try:
            endpoint = agentarts.endpoint_for(agent_id)
            emit(db, run_id, "agentarts.called", agent_id, f"调用 AgentArts · {AGENT_NAMES.get(agent_id, agent_id)}", "已向华为云 AgentArts Runtime 提交结构化任务。", {
                "endpoint": endpoint,
            })
            async def invoke_and_wait() -> dict:
                task = asyncio.create_task(
                    agentarts.invoke(agent_id, safe_payload, session_id=run_id, client=http_client)
                )
                waited = 0
                while True:
                    done, _ = await asyncio.wait({task}, timeout=3)
                    if done:
                        break
                    waited += 3
                    emit(
                        db, run_id, "agentarts.waiting", agent_id,
                        f"等待华为云推理 · {waited}s",
                        "AgentArts 正在处理当前角色任务，系统仍在运行。",
                        {"waited_seconds": waited},
                    )
                return await task

            response = await agent_executor.submit(agent_id, invoke_and_wait) if agent_executor else await invoke_and_wait()
            parsed = _parse_json_text(response.get("text", ""))
            if parsed:
                response["parsed"] = parsed
            agentarts_trace[trace_id or agent_id] = response
            db.record_agent_performance(
                agent_id, run_id, trace_id or "agentarts_invoke", True,
                (time.perf_counter() - started_at) * 1000, "",
            )
            emit(db, run_id, "agentarts.completed", agent_id, f"AgentArts 返回 · {AGENT_NAMES.get(agent_id, agent_id)}", "已完成云端智能体调用，结果等待确定性规则复核。", {
                "session_id": run_id,
            })
            return response
        except Exception as exc:
            response = getattr(exc, "response", None)
            http_status = int(getattr(response, "status_code", 0) or 0)
            fallback = {
                "mode": "local-fallback-after-agentarts-error",
                "error": str(exc)[:240],
                "endpoint": endpoint,
                "error_type": AgentArtsBridge._error_type(exc),
                "http_status": http_status,
            }
            db.record_agent_performance(
                agent_id, run_id, trace_id or "agentarts_invoke", False,
                (time.perf_counter() - started_at) * 1000, type(exc).__name__,
            )
            agentarts_trace[trace_id or agent_id] = fallback
            RetryQueueRepository(db).enqueue(
                "agentarts", {"agent_id": agent_id, "payload": safe_payload, "session_id": run_id},
                str(exc), run_id=run_id, max_attempts=RETRY_MAX_ATTEMPTS,
            )
            emit(db, run_id, "agentarts.fallback", agent_id, "AgentArts 调用回退", "云端调用失败，已使用本地确定性结果继续运行；失败请求已写入持久化重试队列。", fallback)
            return fallback

    async def invoke_extension_agent(agent_id: str, payload: dict) -> dict:
        agents = {item["id"]: item for item in registry.list_agents()}
        endpoint_map = getattr(agentarts, "endpoints", None)
        if endpoint_map is not None and agent_id not in endpoint_map:
            local = invoke_local_registered_agent(agents.get(agent_id, {"id": agent_id}), payload)
            agentarts_trace[agent_id] = local
            emit(
                db, run_id, "agent.local_fallback", agent_id,
                "已执行离线注册 Agent", "该 Agent 未配置独立端点，已按角色与能力执行确定性审查。",
                {"mode": "local-rule-agent"},
            )
            return local
        response = await call_agentarts(agent_id, payload)
        if response.get("mode") == "agentarts":
            return response
        local = invoke_local_registered_agent(agents.get(agent_id, {"id": agent_id}), payload)
        agentarts_trace[agent_id] = local
        emit(
            db, run_id, "agent.local_fallback", agent_id,
            "已执行离线注册 Agent", "未使用云端端点，已按 Agent 角色与能力执行确定性审查。",
            {"mode": "local-rule-agent"},
        )
        return local
    async def invoke_workflow_tool(tool_id: str, arguments: dict, workflow_state: dict) -> dict:
        nonlocal evidence
        emit(db, run_id, "tool.called", "tool_runtime", f"调用工具 · {tool_id}", "工作流工具节点开始执行。", {"arguments": arguments})
        try:
            call = await ToolService(db).call_tool(tool_id, arguments, session_id=run_id)
        except ToolError as exc:
            emit(db, run_id, "tool.failed", "tool_runtime", f"工具失败 · {tool_id}", str(exc), {"arguments": arguments})
            raise
        tool_result = call.get("result") or {}
        if tool_id == "inventory_query" and isinstance(tool_result.get("items"), list):
            pipeline_state["execution_inventory"] = tool_result["items"]
            pipeline_state["inventory_source"] = "inventory_query tool"
            emit(
                db, run_id, "tool.consumed", "matching_agent", "库存工具结果已进入方案计算",
                f"资源匹配将使用工具返回的 {len(tool_result['items'])} 条库存记录，而不是再次直接读取数据库。",
                {"tool_id": tool_id, "inventory_rows": len(tool_result["items"])},
            )
        if tool_id == "knowledge_search" and isinstance(tool_result.get("items"), list):
            knowledge_items = [item for item in tool_result["items"] if isinstance(item, dict)]
            merged = {item.get("id"): item for item in evidence if isinstance(item, dict)}
            for item in knowledge_items:
                merged[item.get("id")] = item
            evidence = sorted(merged.values(), key=lambda item: float(item.get("score", 0)), reverse=True)[:10]
            pipeline_state["execution_evidence"] = knowledge_items
            emit(
                db, run_id, "tool.consumed", "matching_agent", "知识工具结果已进入推理上下文",
                f"{len(knowledge_items)} 条带引用定位的知识片段已注入匹配、核验和交付上下文。",
                {"tool_id": tool_id, "evidence_ids": [item.get("id") for item in knowledge_items]},
            )
        emit(db, run_id, "tool.completed", "tool_runtime", f"工具完成 · {tool_id}", "工具结果已写入工作流上下文。", call.get("audit") or {})
        return {"tool_id": tool_id, "result": tool_result, "inventory_tool_result": tool_result, "audit": call.get("audit"), "mode": "tool"}
    async def select_rework_policy(verification: dict, next_attempt: int) -> dict:
        allowed = allowed_rework_policies(verification)
        allowed_ids = {item["id"] for item in allowed}
        default_policy = next((item["id"] for item in allowed if item["id"] != "escalate_human"), "escalate_human")
        remembered = (
            memory_context.get("matching_agent", {})
            .get("successful_repair_policy", {})
            .get("value", {})
        )
        remembered_policy = str(remembered.get("policy") or "") if isinstance(remembered, dict) else ""
        memory_selected = remembered_policy if remembered_policy in allowed_ids else ""
        repairable = [item for item in allowed if item["id"] != "escalate_human"]
        if len(repairable) <= 1:
            selected = repairable[0]["id"] if repairable else "escalate_human"
            source = "deterministic-single-policy" if repairable else "deterministic-no-safe-repair"
            result = {
                "selected_policy": selected,
                "allowed_policies": allowed,
                "source": source,
                "memory_key": memory_selected if memory_selected == selected else "",
                "rationale": (
                    f"\u5f53\u524d\u53ea\u6709 {selected} \u4e00\u4e2a\u5b89\u5168\u81ea\u52a8\u4fee\u590d\u7b56\u7565\uff0c\u76f4\u63a5\u6267\u884c\u786e\u5b9a\u6027\u7b56\u7565\uff0c\u4e0d\u518d\u7b49\u5f85\u4e91\u7aef\u7b56\u7565\u9009\u62e9\u3002"
                    if repairable
                    else "\u5f53\u524d\u6ca1\u6709\u53ef\u5b89\u5168\u81ea\u52a8\u6267\u884c\u7684\u4fee\u590d\u7b56\u7565\uff0c\u76f4\u63a5\u8f6c\u4eba\u5de5\u7814\u5224\u3002"
                ),
                "attempt": next_attempt,
            }
            agentarts_trace["rework_policy"] = {
                "mode": source,
                "selected_policy": selected,
                "source": source,
            }
            emit(db, run_id, "ai.rework_policy", "rework_policy", "\u4fee\u590d\u7b56\u7565\u5df2\u786e\u5b9a", result["rationale"], result)
            return result
        if not agentarts.enabled:
            selected = memory_selected or default_policy
            return {
                "selected_policy": selected,
                "allowed_policies": allowed,
                "source": "memory-assisted-deterministic" if memory_selected else "deterministic-fallback",
                "memory_key": "successful_repair_policy" if memory_selected else "",
                "rationale": (
                    f"AgentArts 未启用；历史成功策略 {memory_selected} 在当前允许集合内，仅作为安全 tie-break 使用。"
                    if memory_selected
                    else "AgentArts 未启用，使用规则匹配选择修复策略。"
                ),
            }
        response = await call_agentarts("matching_agent", {
            "task_type": "rework_strategy",
            "next_attempt": next_attempt,
            "issue_codes": verification.get("blocker_summary", []),
            "required_fixes": verification.get("required_fixes", [])[:5],
            "allowed_policies": allowed,
            "runtime_prompt": pipeline_state.get("active_prompt_versions", {}).get("matching_agent", "v1.4"),
            "memory": memory_context.get("matching_agent", {}),
            "experience_context": _load_repair_experiences(
                memory_repo, verification, str(scenario.get("id") or "generic")
            ),
            "constraints": [
                "只能从 allowed_policies 中选择一个 id",
                "不得选择不存在或不允许的策略",
                "最终硬约束仍由规则层执行",
            ],
        }, trace_id="rework_policy")
        parsed = response.get("parsed") if isinstance(response, dict) else None
        candidate = ""
        if isinstance(parsed, dict):
            candidate = str(
                parsed.get("selected_policy")
                or parsed.get("policy_id")
                or parsed.get("strategy_id")
                or ((parsed.get("policy") or {}).get("id") if isinstance(parsed.get("policy"), dict) else "")
            )
        selected = candidate if candidate in allowed_ids else default_policy
        result = {
            "selected_policy": selected,
            "allowed_policies": allowed,
            "source": "agentarts" if candidate in allowed_ids else "deterministic-fallback",
            "rationale": str(parsed.get("rationale") or parsed.get("summary") or "根据问题代码选择安全修复策略。") if isinstance(parsed, dict) else "AgentArts 未返回有效策略，使用规则回退。",
            "attempt": next_attempt,
        }
        emit(db, run_id, "ai.rework_policy", "rework_policy", "AI 修复策略选择完成", result["rationale"], result)
        return result
    async def run_stage_workflows(stage_name: str, stage_context: dict) -> None:
        selected = [item for item in all_extension_workflows if item["stage"] == stage_name]
        if not selected:
            return
        agent_names = {item["id"]: item["name"] for item in registry.list_agents()}

        async def invoke_stage_agent(agent_id: str, workflow_state: dict, node: dict) -> dict:
            emit(db, run_id, "workflow.node.started", agent_id, f"工作流节点 {node['id']} 开始", f"调用 Agent：{agent_id}", {"stage": stage_name})
            payload = {**stage_context, "node_id": node["id"], "node_outputs": workflow_state.get("node_outputs", {})}
            return await invoke_extension_agent(agent_id, payload)

        for workflow_record in selected:
            try:
                engine = WorkflowEngine(
                    db,
                    invoke_agent=invoke_stage_agent,
                    load_workflow=lambda workflow_id: workflow_repo.get_workflow(workflow_id),
                    event_sink=lambda node_id, message: emit(
                        db, run_id, "workflow.node", node_id, message, "",
                        {"workflow_id": workflow_record["id"], "stage": stage_name},
                    ),
                    agent_metrics=runtime_metrics,
                    max_workers=4,
                    invoke_tool=invoke_workflow_tool,
                )
                result = await engine.execute(workflow_record["id"], workflow_record["definition"], stage_context)
                extension_workflow_runs.append({
                    "workflow_id": workflow_record["id"], "workflow_name": workflow_record["name"],
                    "stage": stage_name, "run_id": result["run_id"], "status": result["status"],
                    "metrics": result["metrics"], "outputs": result["outputs"],
                })
                for output in result["outputs"].values():
                    if isinstance(output, dict) and output.get("agent_id"):
                        agent_id = str(output["agent_id"])
                        extension_reviews.append({
                            "agent_id": agent_id, "agent_name": agent_names.get(agent_id, agent_id),
                            "role": f"{stage_name}-extension", "mode": output.get("mode", ""),
                            "parsed": output.get("parsed"), "text": output.get("text", ""),
                        })
            except Exception as exc:
                extension_workflow_runs.append({
                    "workflow_id": workflow_record["id"], "workflow_name": workflow_record["name"],
                    "stage": stage_name, "status": "failed", "error": str(exc)[:500],
                })
                emit(db, run_id, "workflow.failed", "orchestrator", "扩展工作流执行失败", str(exc)[:240], {
                    "workflow_id": workflow_record["id"], "stage": stage_name,
                })

    async def run_post_verification_extensions(stage_demand: dict, stage_plans: list[dict], stage_verification: dict) -> dict:
        review_start = len(extension_reviews)
        workflow_run_start = len(extension_workflow_runs)
        await run_stage_workflows("post_verification", {
            "scenario": scenario,
            "demand": stage_demand,
            "plans": [{"plan_id": plan["plan_id"], "name": plan["name"], "metrics": plan["metrics"]} for plan in stage_plans],
            "verification": stage_verification,
        })
        if not extension_workflows and extension_agents:
            async def run_extension(agent: dict) -> dict:
                agent_id = agent["id"]
                emit(db, run_id, "agent.started", agent_id, f"扩展审查开始 · {agent['name']}", "动态注册 Agent 已进入核验后的扩展审查节点。")
                response = await invoke_extension_agent(agent_id, {
                    "scenario": scenario,
                    "demand": stage_demand,
                    "plans": [{"plan_id": plan["plan_id"], "name": plan["name"], "metrics": plan["metrics"]} for plan in stage_plans],
                    "verification": {
                        "verdict": stage_verification["verdict"],
                        "score": stage_verification["score"],
                        "blockers": stage_verification["blocker_summary"],
                        "warnings": stage_verification.get("warnings", []),
                    },
                })
                emit(db, run_id, "agent.completed", agent_id, f"扩展审查完成 · {agent['name']}", "扩展意见已写入交付包，不能覆盖确定性硬规则。")
                return {
                    "agent_id": agent_id,
                    "agent_name": agent["name"],
                    "role": agent.get("role", ""),
                    "mode": response.get("mode", ""),
                    "parsed": response.get("parsed"),
                    "text": response.get("text", ""),
                }

            extension_reviews.extend(await asyncio.gather(*(run_extension(agent) for agent in extension_agents)))
        return {
            "extension_reviews": extension_reviews[review_start:],
            "extension_workflow_runs": extension_workflow_runs[workflow_run_start:],
            "agentarts_trace": dict(agentarts_trace),
        }

    try:
        execution_started = time.perf_counter()
        try:
            planner = LLMPlanner(db, run_id)
        except TypeError:
            planner = LLMPlanner()
        if planner.enabled:
            runtime_mode = "llm-planner"
        elif agentarts.enabled:
            runtime_mode = "agentarts"
        else:
            runtime_mode = "local"
        db.set_run_status(run_id, "running")
        emit(db, run_id, "run.started", "orchestrator", "推演任务启动", "已加载事件、需求与最新资源快照。", {
            "runtime": runtime_mode,
            "scenario_id": request["scenario_id"],
        })
        InventoryReservationService(db).cleanup_expired()
        await pace(0.28)

        scenario = db.get_scenario(request["scenario_id"])
        if not scenario:
            raise ValueError("推演场景不存在")
        materials = db.list_materials()
        destinations = db.destination_rows()
        warehouses = db.warehouse_rows()
        routes = db.route_rows()
        vehicles = db.vehicle_rows()
        snapshot = db.latest_snapshot()
        inventory = db.inventory_rows(snapshot["id"] if snapshot else None)
        closed_routes = request["event"].get("closed_route_ids") or scenario["closed_routes"]
        stage_metrics: list[dict] = []
        selected_ids = {int(item["material_id"]) for item in request["demand_items"]}
        selected_materials = [item for item in materials if item["id"] in selected_ids]
        query = " ".join([
            scenario["event_type"], scenario["severity"], scenario["description"],
            "道路封闭 库存 运力 时效 独立核验 反馈循环",
            " ".join(item["name"] for item in selected_materials),
        ])
        retrieval_started = time.perf_counter()
        knowledge_repo = KnowledgeRepository(db)
        knowledge_repo.ensure_seed_documents()
        evidence = knowledge_repo.search(query, top_k=7)
        memory_context = memory_repo.summary(["demand_agent", "matching_agent", "verifier_agent", "delivery_agent"], query=query)
        append_stage_metric(stage_metrics, "retrieval", retrieval_started, "rag_retrieve", 1)
        emit(db, run_id, "memory.loaded", "orchestrator", "跨运行记忆已加载", f"加载 {sum(len(items) for items in memory_context.values())} 条可审计记忆。", {
            "agents": {agent_id: list(items) for agent_id, items in memory_context.items()},
            "scope": "仅作上下文与安全 tie-break，不覆盖硬规则",
        })
        schedule = apply_agent_load(build_dynamic_schedule(request, closed_routes, len(request["demand_items"])), runtime_metrics, agents=registry.list_agents())
        agent_executor = AsyncPriorityExecutor(
            build_executor_priorities(schedule),
            max_workers=3,
        )

        emit(db, run_id, "data.snapshot.loaded", "orchestrator", "数据快照已冻结", f"{snapshot['label']}，共 {snapshot['row_count']} 条库存记录。", {
            "snapshot_id": snapshot["id"],
            "material_rows": len(inventory),
            "warehouses": len(warehouses),
        })
        emit(db, run_id, "scheduler.decided", "orchestrator", "动态调度策略已生成", schedule["rationale"], {
            "complexity_score": schedule["complexity_score"],
            "parallel_branch": schedule["parallel_branch"],
            "loop": schedule["loop"],
            "executor": "asyncio.PriorityQueue",
            "workers": agent_executor.max_workers,
            "dispatch": schedule["dispatch"],
        })
        emit(db, run_id, "rag.retrieved", "orchestrator", "RAG 规则检索完成", f"检索到 {len(evidence)} 条可引用规则。", {
            "documents": [{"id": item["id"], "title": item["title"], "score": item["score"]} for item in evidence],
        })
        await pace(0.25)
        a2a_bus = A2ABus(
            db, run_id, enabled=A2A_ENABLED,
            event_sink=lambda message: emit(
                db, run_id, "a2a.message", message["sender"],
                f"{message['sender']} → {message['receiver']} · {message['message_type']}",
                str(message.get("content") or {}),
                message,
            ),
        )
        if planner.enabled:
            try:
                planner_result = await run_planner_orchestration(
                    db, run_id, request, scenario, materials, destinations, warehouses,
                    routes, vehicles, inventory, evidence, schedule, planner, llm_service, a2a_bus,
                    run_post_verification_extensions, stage_metrics, execution_started,
                )
                planner_result["memory_context"] = memory_context
                planner_result["memory_writes"] = planner_result.get("repair_experience_writes", [])
                planner_result["llm_status"] = llm_service.status
                planner_result["planner_status"] = planner.status
                terminal_status = "awaiting_approval" if planner_result["delivery"]["status"] == "ready" else "needs_human"
                db.update_run(run_id, terminal_status, planner_result)
                emit(
                    db, run_id, "awaiting.approval" if terminal_status == "awaiting_approval" else "human.escalation",
                    "orchestrator", "LLM ReAct 编排完成" if terminal_status == "awaiting_approval" else "LLM ReAct 编排转人工",
                    planner_result["delivery"]["summary"],
                    {"status": terminal_status, "planner_steps": planner_result["planner_trace"]["executed_steps"]},
                )
                return
            except Exception:
                logger.exception("LLM planner orchestration failed for run %s; falling back to fixed workflow", run_id)
                emit(db, run_id, "llm.thinking", "planner", "LLM Planner执行失败，自动回退", "保留确定性工具流程，确保演示和调度不中断。", {"fallback": "fixed-workflow"})
        else:
            emit(db, run_id, "llm.thinking", "planner", "LLM Planner未启用", "使用固定安全流程；配置LLM后可启用ReAct规划。", {"fallback": "fixed-workflow"})

        pipeline_state: dict = {"demand": None, "plans": None, "verification": None, "demand_workflows_done": False, "execution_inventory": None, "execution_evidence": [], "inventory_source": "database snapshot", "prompt_adaptations": [], "decision_weights": request.get("weights", {}), "active_prompt_versions": {"demand_agent": "v1.3", "matching_agent": "v1.4", "verifier_agent": "v1.5", "delivery_agent": "v1.2"}}
        core_runs: list[dict] = []
        rounds: list[dict] = []
        rework_directives: list[dict] = []
        max_solution_attempts = 3

        async def invoke_core_agent(agent_id: str, workflow_state: dict, node: dict) -> dict:
            attempt = int(workflow_state.get("attempt", 1))
            directive = workflow_state.get("rework_directive")
            if agent_id == "demand_agent":
                if pipeline_state["demand"] is None:
                    emit(db, run_id, "agent.started", "demand_agent", "需求研判智能体开始工作", "按安置人口拆分物资需求并校验主数据。")
                    await pace(0.45)
                    demand_plan = build_demand_plan(request, materials, destinations)
                    demand_plan["evidence_refs"] = list(dict.fromkeys(demand_plan["evidence_refs"] + [item["id"] for item in evidence]))
                    demand_plan["a2a_exchange"] = await maybe_a2a_cross_warehouse(
                        a2a_bus, demand_plan, inventory,
                        lambda exc: emit(db, run_id, "a2a.fallback", "demand_agent", "A2A 通道不可用，已回退到黑板交接", str(exc)[:240]),
                        vehicles,
                    )
                    ai_reasoning["demand_agent"] = await llm_service.reflect("demand_agent", demand_plan, evidence)
                    llm_decisions["demand_priority"] = apply_llm_priority_adjustments(demand_plan, ai_reasoning["demand_agent"])
                    demand_remote_task = track_background_task(call_agentarts("demand_agent", {"summary": demand_plan["summary"], "items": demand_plan["items"], "evidence": evidence}))
                    pipeline_state["demand_agentarts_task"] = demand_remote_task
                    emit(db, run_id, "tool.called", "demand_agent", "\u8c03\u7528\u9700\u6c42\u6807\u51c6\u5316\u5de5\u5177", f"\u751f\u6210 {len(demand_plan['items'])} \u6761\u5b89\u7f6e\u70b9\u9700\u6c42\u3002")
                    emit(db, run_id, "agent.completed", "demand_agent", "\u9700\u6c42\u6807\u51c6\u5316\u5b8c\u6210", demand_plan["summary"], {
                        "items": len(demand_plan["items"]),
                        "confidence": demand_plan["confidence"],
                        "agentarts_mode": "parallel-advisory",
                    })
                    pipeline_state["demand"] = demand_plan
                if not pipeline_state["demand_workflows_done"]:
                    await run_stage_workflows("after_demand", {"scenario": scenario, "demand": pipeline_state["demand"]})
                    pipeline_state["demand_workflows_done"] = True
                return {"agent_id": "demand_agent", "demand": pipeline_state["demand"], "mode": "core-phase"}

            if agent_id == "matching_agent":
                emit(db, run_id, "agent.started", "matching_agent", "资源匹配智能体开始计算", f"第 {attempt} 轮基于物资库、库存、车辆与路网生成候选方案。")
                await pace(0.45)
                plans = generate_plans(
                    pipeline_state["demand"], pipeline_state.get("execution_inventory") or inventory, warehouses, routes, vehicles,
                    attempt=attempt, closed_routes=closed_routes, rework_directive=directive,
                )
                pipeline_state["plans"] = plans
                emit(db, run_id, "tool.called", "matching_agent", "调用多目标方案生成器", f"已生成 {len(plans)} 套候选方案。")
                ai_reasoning["matching_agent"] = await llm_service.reflect(
                    "matching_agent",
                    {
                        "summary": f"第 {attempt} 轮已生成多目标候选方案。",
                        "plans": [{"name": plan["name"], "metrics": plan["metrics"]} for plan in plans],
                        "rework_directive": directive,
                        "prompt_adaptations": pipeline_state.get("prompt_adaptations", []),
                        "active_prompt_versions": pipeline_state.get("active_prompt_versions", {}),
                        "scheduler": schedule,
                    },
                    evidence,
                )
                llm_decisions["matching_weights"] = normalize_llm_weight_profile(
                    request.get("weights", {}), ai_reasoning["matching_agent"],
                )
                pipeline_state["decision_weights"] = llm_decisions["matching_weights"]["weights"]
                emit(db, run_id, "plan.generated", "matching_agent", f"第 {attempt} 轮候选方案生成", "方案已提交独立核验。", {
                    "attempt": attempt,
                    "plans": [{"plan_id": plan["plan_id"], "name": plan["name"], "metrics": plan["metrics"]} for plan in plans],
                })
                emit(db, run_id, "agent.completed", "matching_agent", f"第 {attempt} 轮匹配完成", f"生成 {len(plans)} 套候选方案。")
                await pace(0.32)
                return {"agent_id": "matching_agent", "plans": plans, "attempt": attempt, "mode": "core-phase"}

            if agent_id == "verifier_agent":
                if not pipeline_state["plans"]:
                    raise ValueError("核验节点执行前没有候选方案")
                emit(db, run_id, "agent.started", "verifier_agent", "独立核验智能体开始审查", f"第 {attempt} 轮核验道路、库存、运力与时效约束。")
                await pace(0.5)
                verification = verify_plans(
                    pipeline_state["plans"], pipeline_state["demand"], pipeline_state.get("execution_inventory") or inventory, routes, vehicles, closed_routes
                )
                pipeline_state["verification"] = verification
                emit(db, run_id, "agent.completed", "verifier_agent", f"第 {attempt} 轮核验完成", f"核验结论：{verification['verdict']}。")
                if verification["verdict"] == "pass":
                    emit(db, run_id, "verification.passed", "verifier_agent", f"第 {attempt} 轮方案通过核验", f"最高评分 {verification['score']}。", {
                        "attempt": attempt,
                        "score": verification["score"],
                        "approved_plan_ids": verification["approved_plan_ids"],
                    })
                else:
                    emit(db, run_id, "verification.failed", "verifier_agent", f"第 {attempt} 轮方案未通过", f"最高评分 {verification['score']}，发现 {len(verification['blocker_summary'])} 类阻断项。", {
                        "attempt": attempt,
                        "score": verification["score"],
                        "blockers": verification["blocker_summary"],
                        "required_fixes": verification["required_fixes"][:5],
                    })
                await pace(0.35)
                return {"agent_id": "verifier_agent", "verification": verification, "attempt": attempt, "mode": "core-phase"}

            if agent_id == "rework_policy":
                verification = workflow_state.get("verification") or pipeline_state.get("verification")
                if not verification:
                    raise ValueError("修复策略节点执行前没有核验结果")
                next_attempt = attempt + 1
                policy_selection = await select_rework_policy(verification, next_attempt)
                directive = build_rework_directive(
                    verification, next_attempt, selected_policy=policy_selection["selected_policy"]
                )
                directive["experience_context"] = _load_repair_experiences(
                    memory_repo, verification, str(scenario.get("id") or "generic")
                )
                prompt_adaptation = build_prompt_adaptation(verification, directive)
                pipeline_state["prompt_adaptations"].append(prompt_adaptation)
                pipeline_state["active_prompt_versions"]["matching_agent"] = prompt_adaptation["active_version"]
                directive["policy_selection"] = policy_selection
                directive["prompt_adaptation"] = prompt_adaptation
                emit(db, run_id, "rework.requested", "rework_policy", f"生成第 {next_attempt} 轮修复指令", directive["reason"], {
                    "attempt": next_attempt,
                    "policy_selection": policy_selection,
                    "directive": directive,
                })
                return {
                    "agent_id": "rework_policy",
                    "rework_directive": directive,
                    "policy_selection": policy_selection,
                    "continue_loop": directive["can_retry"],
                    "mode": "ai-policy-selection",
                }
            if agent_id == "verification_vote":
                base = verify_plans(pipeline_state["plans"], pipeline_state["demand"], pipeline_state.get("execution_inventory") or inventory, routes, vehicles, closed_routes)
                votes = []
                for output in workflow_state.get("node_outputs", {}).values():
                    if not isinstance(output, dict):
                        continue
                    parsed = output.get("parsed") if isinstance(output.get("parsed"), dict) else {}
                    raw_verification = output.get("verification") if isinstance(output.get("verification"), dict) else {}
                    verdict = output.get("verdict") or parsed.get("verdict") or raw_verification.get("verdict")
                    if verdict:
                        votes.append({
                            "agent_id": output.get("agent_id", ""),
                            "verdict": str(verdict),
                            "score": parsed.get("score") if parsed.get("score") is not None else raw_verification.get("score"),
                        })
                pass_votes = sum(1 for item in votes if item["verdict"] == "pass")
                if votes and pass_votes <= len(votes) / 2:
                    base["verdict"] = "rework"
                    base["approved_plan_ids"] = []
                    base["blocker_summary"] = sorted(set(base.get("blocker_summary", [])) | {"VOTE-REJECTED"})
                    base["required_fixes"] = list(base.get("required_fixes", [])) + ["核验 Agent 投票未通过"]
                base["votes"] = votes
                base["vote_summary"] = {"pass": pass_votes, "total": len(votes), "rule": "majority"}
                pipeline_state["verification"] = base
                emit(db, run_id, "verification.vote", "verification_vote", "多核验 Agent 投票完成", f"通过 {pass_votes}/{len(votes)}，最终结论 {base['verdict']}。", base["vote_summary"])
                return {"agent_id": "verification_vote", "verification": base, "mode": "internal-vote"}

            agents = {item["id"]: item for item in registry.list_agents()}
            candidate_verification = None
            if pipeline_state.get("plans"):
                candidate_verification = verify_plans(
                    pipeline_state["plans"], pipeline_state["demand"], pipeline_state.get("execution_inventory") or inventory, routes, vehicles, closed_routes
                )
                pipeline_state["verification"] = candidate_verification
            response = await invoke_extension_agent(agent_id, {
                "scenario": scenario,
                "demand": pipeline_state.get("demand"),
                "plans": pipeline_state.get("plans") or [],
                "verification": candidate_verification or {},
                "node_outputs": workflow_state.get("node_outputs", {}),
            })
            return {**response, "agent_id": agent_id}

        core_definition = core_workflow["definition"]
        core_has_loop = any(node.get("type") == "loop" for node in core_definition.get("nodes", []))
        if core_has_loop:
            pipeline_state["plans"] = None
            pipeline_state["verification"] = None
            engine = WorkflowEngine(
                db,
                invoke_agent=invoke_core_agent,
                load_workflow=lambda workflow_id: workflow_repo.get_workflow(workflow_id),
                event_sink=lambda node_id, message: emit(
                    db, run_id, "workflow.core_node", node_id, message, "",
                    {"workflow_id": core_workflow["id"], "stage": "core"},
                ),
                agent_metrics=runtime_metrics,
                max_workers=4,
                invoke_tool=invoke_workflow_tool,
            )
            core_result = await engine.execute(
                core_workflow["id"], core_definition, {"scenario": scenario},
            )
            core_runs.append({
                "attempt": 1, "run_id": core_result["run_id"], "status": core_result["status"],
                "completed": core_result["completed"], "metrics": core_result["metrics"],
                "loop_mode": True,
            })
            workflow_run = workflow_repo.get_run(core_result["run_id"]) or {}
            stage_by_agent = {
                "demand_agent": "demand", "matching_agent": "matching",
                "verifier_agent": "verification", "verification_vote": "verification",
                "core-verifier-a": "verification", "core-verifier-b": "verification",
                "delivery_agent": "delivery",
            }
            for node_run in workflow_run.get("nodes", []):
                node_stage = stage_by_agent.get(str(node_run.get("agent_id") or ""))
                if not node_stage:
                    continue
                stage_metrics.append({
                    "stage": node_stage,
                    "started_at": node_run.get("started_at") or "",
                    "ended_at": node_run.get("completed_at") or "",
                    "duration_ms": round(float(node_run.get("execution_ms") or 0), 2),
                    "agent_id": str(node_run.get("agent_id") or ""),
                    "attempt": 1,
                })
            loop_node = next(node for node in core_definition["nodes"] if node.get("type") == "loop")
            loop_output = core_result["outputs"].get(loop_node["id"], {})
            for iteration in loop_output.get("iterations", []):
                merged = {}
                for item in iteration.get("outputs", {}).values():
                    if isinstance(item, dict):
                        merged.update({key: value for key, value in item.items() if key not in {"agent_id", "mode", "node_id"}})
                plans = merged.get("plans")
                verification = merged.get("verification")
                if plans is None or verification is None:
                    continue
                rounds.append({
                    "attempt": iteration["iteration"], "plans": plans, "verification": verification,
                    "mode": "core-workflow-loop",
                    "rework_directive": iteration.get("input_rework_directive"),
                    "workflow_run_id": core_result["run_id"],
                })
                if iteration.get("rework_directive"):
                    rework_directives.append(iteration["rework_directive"])
            if not rounds:
                raise ValueError(f"工作流循环没有产生可核验轮次：{core_result.get('outputs', {})}")
            if loop_output.get("status") != "completed":
                emit(db, run_id, "rework.escalated", "orchestrator", "工作流循环未能收敛", "达到循环上限或修复策略要求人工介入。", {
                    "loop_status": loop_output.get("status"),
                    "iterations": len(loop_output.get("iterations", [])),
                    "selected_policy": (rework_directives[-1] if rework_directives else {}).get("selected_policy"),
                })
        else:
            for attempt in range(1, max_solution_attempts + 1):
                pipeline_state["plans"] = None
                pipeline_state["verification"] = None
                directive = rework_directives[-1] if rework_directives else None
                engine = WorkflowEngine(
                    db,
                    invoke_agent=invoke_core_agent,
                    load_workflow=lambda workflow_id: workflow_repo.get_workflow(workflow_id),
                    event_sink=lambda node_id, message: emit(
                        db, run_id, "workflow.core_node", node_id, message, "",
                        {"workflow_id": core_workflow["id"], "stage": "core", "attempt": attempt},
                    ),
                    agent_metrics=runtime_metrics,
                    max_workers=4,
                    invoke_tool=invoke_workflow_tool,
                )
                core_result = await engine.execute(
                    core_workflow["id"], core_definition,
                    {"scenario": scenario, "attempt": attempt, "rework_directive": directive},
                )
                core_runs.append({
                    "attempt": attempt, "run_id": core_result["run_id"], "status": core_result["status"],
                    "completed": core_result["completed"], "metrics": core_result["metrics"],
                })
                workflow_run = workflow_repo.get_run(core_result["run_id"]) or {}
                stage_by_agent = {
                    "demand_agent": "demand", "matching_agent": "matching",
                    "verifier_agent": "verification", "verification_vote": "verification",
                    "core-verifier-a": "verification", "core-verifier-b": "verification",
                    "delivery_agent": "delivery",
                }
                for node_run in workflow_run.get("nodes", []):
                    node_stage = stage_by_agent.get(str(node_run.get("agent_id") or ""))
                    if not node_stage:
                        continue
                    stage_metrics.append({
                        "stage": node_stage,
                        "started_at": node_run.get("started_at") or "",
                        "ended_at": node_run.get("completed_at") or "",
                        "duration_ms": round(float(node_run.get("execution_ms") or 0), 2),
                        "agent_id": str(node_run.get("agent_id") or ""),
                        "attempt": attempt,
                    })
                plans = pipeline_state.get("plans")
                verification = pipeline_state.get("verification")
                if plans is None:
                    raise ValueError("主干工作流没有生成候选方案")
                if verification is None:
                    verification = verify_plans(plans, pipeline_state["demand"], pipeline_state.get("execution_inventory") or inventory, routes, vehicles, closed_routes)
                    emit(db, run_id, "verification.safety_fallback", "orchestrator", "主干缺少核验输出", "已自动执行内置安全核验，禁止绕过硬规则。")
                    pipeline_state["verification"] = verification
                rounds.append({
                    "attempt": attempt, "plans": plans, "verification": verification,
                    "mode": "core-dag" if attempt == 1 else "core-dag-adaptive",
                    "rework_directive": directive, "workflow_run_id": core_result["run_id"],
                })
                if verification["verdict"] == "pass":
                    break
                next_attempt = attempt + 1
                policy_selection = await select_rework_policy(verification, next_attempt)
                next_directive = build_rework_directive(
                    verification, next_attempt, selected_policy=policy_selection["selected_policy"]
                )
                next_directive["experience_context"] = _load_repair_experiences(
                    memory_repo, verification, str(scenario.get("id") or "generic")
                )
                prompt_adaptation = build_prompt_adaptation(verification, next_directive)
                pipeline_state["prompt_adaptations"].append(prompt_adaptation)
                pipeline_state["active_prompt_versions"]["matching_agent"] = prompt_adaptation["active_version"]
                next_directive["policy_selection"] = policy_selection
                next_directive["prompt_adaptation"] = prompt_adaptation
                if not next_directive["can_retry"] or next_attempt > max_solution_attempts:
                    emit(db, run_id, "rework.escalated", "orchestrator", "资源约束无法自动修复", next_directive["reason"], {
                        "attempt": next_attempt,
                        "issue_codes": next_directive["issue_codes"],
                        "categories": next_directive["categories"],
                        "policy_selection": policy_selection,
                    })
                    break
                rework_directives.append(next_directive)
                emit(db, run_id, "rework.requested", "orchestrator", f"触发第 {next_attempt} 轮自适应重算", next_directive["reason"], {
                    "attempt": next_attempt,
                    "constraints": verification["required_fixes"][:5],
                    "policy_selection": policy_selection,
                    "directive": next_directive,
                })
        demand_plan = pipeline_state["demand"]
        round1_plans = rounds[0]["plans"]
        round1_verification = rounds[0]["verification"]
        final_plans = rounds[-1]["plans"]
        final_verification = rounds[-1]["verification"]
        await run_stage_workflows("after_matching", {
            "scenario": scenario,
            "demand": demand_plan,
            "plans": [{"plan_id": plan["plan_id"], "name": plan["name"], "metrics": plan["metrics"]} for plan in final_plans],
            "verification": final_verification,
        })
        matching_payload = {
            "summary": "已完成多目标候选方案生成与约束修正。",
            "plans": [{"plan_id": plan["plan_id"], "name": plan["name"], "metrics": plan["metrics"]} for plan in final_plans],
            "closed_routes": closed_routes,
            "requested_weights": request.get("weights", {}),
            "weight_profile_schema": {"speed": "0-1", "cost": "0-1", "risk": "0-1"},
            "active_prompt_version": pipeline_state.get("active_prompt_versions", {}).get("matching_agent", "v1.4"),
            "prompt_adaptations": pipeline_state.get("prompt_adaptations", []),
        }
        verifier_payload = {
            "verdict": final_verification["verdict"],
            "score": final_verification["score"],
            "blocker_summary": final_verification["blocker_summary"],
            "required_fixes": final_verification["required_fixes"],
            "active_prompt_version": pipeline_state.get("active_prompt_versions", {}).get("verifier_agent", "v1.5"),
        }

        async def run_verifier_review() -> dict:
            ai_reasoning["verifier_agent"] = await llm_service.reflect(
                "verifier_agent",
                {
                    "verdict": final_verification["verdict"],
                    "score": final_verification["score"],
                    "blockers": final_verification["blocker_summary"],
                    "required_fixes": final_verification["required_fixes"],
                },
                evidence,
            )
            return await call_agentarts("verifier_agent", verifier_payload)

        provisional_delivery = build_delivery_package(
            final_verification, scenario, snapshot, pipeline_state.get("decision_weights") or request.get("weights", {}),
        )
        provisional_delivery["llm_weight_decision"] = llm_decisions.get("matching_weights", {})

        async def run_delivery_review() -> dict:
            ai_reasoning["delivery_agent"] = await llm_service.reflect("delivery_agent", provisional_delivery, evidence)
            return await call_agentarts("delivery_agent", {"delivery": provisional_delivery, "evidence": evidence})

        emit(db, run_id, "agent.started", "delivery_agent", "\u65b9\u6848\u4ea4\u4ed8\u667a\u80fd\u4f53\u5e76\u884c\u6c47\u603b", "\u4ea4\u4ed8 AgentArts \u4e0e\u6700\u7ec8\u5339\u914d\u3001\u72ec\u7acb\u6838\u9a8c\u5e76\u884c\u6267\u884c\uff0c\u6570\u503c\u4ecd\u7531\u786e\u5b9a\u6027\u89c4\u5219\u751f\u6210\u3002")
        demand_remote_task = pipeline_state.get("demand_agentarts_task")
        if demand_remote_task is None:
            matching_remote, verifier_remote, delivery_remote = await asyncio.gather(
                call_agentarts("matching_agent", matching_payload),
                run_verifier_review(),
                run_delivery_review(),
            )
            demand_remote = {"mode": "local-fallback", "reason": "demand advisory task was not started"}
        else:
            matching_remote, verifier_remote, delivery_remote, demand_remote = await asyncio.gather(
                call_agentarts("matching_agent", matching_payload),
                run_verifier_review(),
                run_delivery_review(),
                demand_remote_task,
            )
        remote_demand = demand_remote.get("parsed") if isinstance(demand_remote, dict) else None
        if remote_demand:
            demand_plan["agentarts_interpretation"] = {**remote_demand, "application_mode": "parallel-advisory"}
            demand_plan["agentarts_priority_advisory"] = remote_demand.get("priority_adjustments") or remote_demand.get("priority_overrides") or {}
            demand_plan["agentarts_priority_applied"] = False
            if remote_demand.get("summary"):
                demand_plan["summary"] = remote_demand["summary"]
            if remote_demand.get("assumptions"):
                demand_plan["assumptions"] = list(dict.fromkeys(demand_plan["assumptions"] + [str(item) for item in remote_demand["assumptions"]]))
            if remote_demand.get("evidence_refs"):
                demand_plan["evidence_refs"] = list(dict.fromkeys(demand_plan["evidence_refs"] + [str(item) for item in remote_demand["evidence_refs"]]))
            emit(db, run_id, "agentarts.advisory", "demand_agent", "\u9700\u6c42\u667a\u80fd\u4f53\u5e76\u884c\u5efa\u8bae\u5df2\u56de\u6536", "\u4e91\u7aef\u6458\u8981\u3001\u5047\u8bbe\u4e0e\u8bc1\u636e\u5f15\u7528\u5df2\u5408\u5165\u9700\u6c42\u8bf4\u660e\uff1b\u4f18\u5148\u7ea7\u5efa\u8bae\u4e0d\u5728\u65b9\u6848\u5df2\u6838\u9a8c\u540e\u8ffd\u6539\uff0c\u907f\u514d\u6570\u636e\u4e0e\u7ed3\u679c\u4e0d\u4e00\u81f4\u3002", {"advisory_only": True})
        remote_matching = matching_remote.get("parsed") if isinstance(matching_remote, dict) else None
        if remote_matching:
            rounds[-1]["agentarts_interpretation"] = remote_matching
            reasoning = ai_reasoning.setdefault("matching_agent", {})
            if isinstance(reasoning, dict):
                reasoning["agentarts_interpretation"] = remote_matching
            agentarts_weight_decision = normalize_llm_weight_profile(
                request.get("weights", {}), {"mode": "agentarts", "result": remote_matching}
            )
            if agentarts_weight_decision.get("applied"):
                llm_decisions["matching_weights"] = agentarts_weight_decision
                pipeline_state["decision_weights"] = agentarts_weight_decision["weights"]
        agent_decision = normalize_agentarts_decision(remote_matching, final_plans)
        emit(db, run_id, "agentarts.decision", "matching_agent",
             "AgentArts 方案推荐已解析" if agent_decision["applied"] else "AgentArts 推荐回退",
             agent_decision["rationale"], agent_decision)
        remote_verifier = verifier_remote.get("parsed") if isinstance(verifier_remote, dict) else None
        if remote_verifier:
            final_verification["agentarts_review"] = remote_verifier
            reasoning = ai_reasoning.setdefault("verifier_agent", {})
            if isinstance(reasoning, dict):
                reasoning["agentarts_interpretation"] = remote_verifier
            remote_verdict = remote_verifier.get("verdict")
            if remote_verdict and remote_verdict != final_verification["verdict"]:
                emit(
                    db, run_id, "agentarts.conflict", "verifier_agent",
                    "AgentArts 核验意见与本地硬规则不一致",
                    "保留本地确定性核验结论，云端意见仅作为辅助解释。",
                    {"local_verdict": final_verification["verdict"], "agentarts_verdict": remote_verdict},
                )
        await run_stage_workflows("after_verification", {
            "scenario": scenario,
            "demand": demand_plan,
            "plans": [{"plan_id": plan["plan_id"], "name": plan["name"], "metrics": plan["metrics"]} for plan in final_plans],
            "verification": final_verification,
        })
        await run_post_verification_extensions(demand_plan, final_plans, final_verification)
        await pace(0.4)
        delivery_started = time.perf_counter()
        delivery = build_delivery_package(
            final_verification, scenario, snapshot, pipeline_state.get("decision_weights") or request.get("weights", {}),
            agent_decision=agent_decision,
        )
        append_stage_metric(stage_metrics, "delivery", delivery_started, "delivery_agent", len(rounds))
        delivery["llm_weight_decision"] = llm_decisions.get("matching_weights", {})
        delivery["extension_reviews"] = extension_reviews
        for review in extension_reviews:
            parsed = review.get("parsed") or {}
            risks = parsed.get("risks") or parsed.get("warnings") or parsed.get("risk_alerts") or []
            if isinstance(risks, str):
                risks = [risks]
            for risk in risks:
                risk_text = risk if isinstance(risk, str) else json.dumps(risk, ensure_ascii=False)
                delivery.setdefault("risk_alerts", []).append(f"{review['agent_name']}：{risk_text}")
        remote_delivery = delivery_remote.get("parsed") if isinstance(delivery_remote, dict) else None
        if remote_delivery:
            cloud_recommendation = str(remote_delivery.get("recommended_plan_id") or "")
            final_recommendation = str((delivery.get("recommended_plan") or {}).get("plan_id") or "")
            valid_plan_ids = {str(report.get("plan_id") or "") for report in final_verification.get("reports", [])}
            consistent = not cloud_recommendation or cloud_recommendation == final_recommendation
            interpretation = {
                "review_status": "audit-only",
                "reviewed_delivery_mode": "provisional-parallel",
                "cloud_recommended_plan_id": cloud_recommendation,
                "final_recommended_plan_id": final_recommendation,
                "recommendation_consistency": (
                    "consistent" if consistent
                    else ("plan-mismatch" if cloud_recommendation in valid_plan_ids else "stale-plan")
                ),
                "discarded_fields": ["summary", "risk_alerts", "actions", "alternatives"],
                "note": "云端交付 Agent 仅作并行审计；最终摘要、风险与动作只从最终核验包生成。",
            }
            delivery["agentarts_interpretation"] = interpretation
            delivery_trace_entry = agentarts_trace.setdefault("delivery_agent", {})
            delivery_trace_entry["review_status"] = "audit-only"
            delivery_trace_entry["discarded_fields"] = interpretation.get("discarded_fields", [])
            reasoning = ai_reasoning.setdefault("delivery_agent", {})
            if isinstance(reasoning, dict):
                reasoning["agentarts_interpretation"] = interpretation
        ai_decision_matrix = build_ai_decision_matrix(
            agent_decision, agentarts_trace, final_verification, extension_reviews, llm_service.status,
            llm_decisions=llm_decisions,
        )
        emit(db, run_id, "agent.completed", "delivery_agent", "交付包生成完成", delivery["summary"], {
            "status": delivery["status"],
            "recommended_plan": (delivery.get("recommended_plan") or {}).get("name"),
            "alternatives": len(delivery.get("alternatives", [])),
        })
        scheduler_metrics = agent_executor.metrics() if agent_executor else {}
        schedule["runtime_metrics"] = scheduler_metrics

        memory_writes.append(memory_repo.remember(
            "demand_agent", "last_confidence",
            {"confidence": demand_plan.get("confidence"), "factors": demand_plan.get("confidence_factors", {})},
            float(demand_plan.get("confidence") or 0.5), run_id,
        ))
        previous_blockers = (
            memory_context.get("verifier_agent", {})
            .get("blocker_history", {})
            .get("value", {})
        )
        blocker_history = dict(previous_blockers) if isinstance(previous_blockers, dict) else {}
        for code in round1_verification.get("blocker_summary", []):
            blocker_history[code] = int(blocker_history.get(code, 0)) + 1
        memory_writes.append(memory_repo.remember(
            "verifier_agent", "blocker_history", blocker_history, 0.9, run_id,
        ))
        memory_writes.extend(_record_repair_experiences(
            memory_repo, rework_directives, rounds, str(scenario.get("id") or "generic"),
            final_verification.get("verdict") == "pass", run_id,
        ))
        if final_verification.get("verdict") == "pass" and rework_directives:
            selected_policy = str(rework_directives[-1].get("selected_policy") or "")
            if selected_policy:
                memory_writes.append(memory_repo.remember(
                    "matching_agent", "successful_repair_policy",
                    {
                        "policy": selected_policy,
                        "issue_codes": rework_directives[-1].get("issue_codes", []),
                        "scenario_id": scenario["id"],
                    },
                    0.8, run_id,
                ))
        recommended = delivery.get("recommended_plan") or {}
        if delivery.get("status") == "ready" and recommended:
            memory_writes.append(memory_repo.remember(
                "delivery_agent", "last_recommended_plan",
                {
                    "plan_id": recommended.get("plan_id", ""),
                    "plan_name": recommended.get("name", ""),
                    "score": recommended.get("score"),
                },
                0.85, run_id,
            ))
        emit(db, run_id, "memory.saved", "orchestrator", "运行记忆已更新", f"写入 {len(memory_writes)} 条跨运行记忆。", {
            "keys": [{"agent_id": item["agent_id"], "memory_key": item["memory_key"]} for item in memory_writes],
        })

        allocate_stage_overhead(stage_metrics, (time.perf_counter() - execution_started) * 1000)
        complete_stage_metrics(stage_metrics)
        failure_root_causes = classify_failure_root_causes(final_verification.get("blocker_summary", []))
        emit(db, run_id, "run.stage_metrics", "orchestrator", "阶段耗时统计完成", f"共 {len(stage_metrics)} 条阶段记录。", {
            "stage_metrics": stage_metrics, "failure_root_causes": failure_root_causes,
        })
        result = {
            "run_id": run_id,
            "runtime": runtime_mode,
            "runtime_label": "本地可复现编排" if runtime_mode == "local" else "AgentArts 四角色云端编排",
            "scenario": scenario,
            "event": request["event"],
            "weights": request.get("weights", {}),
            "snapshot": snapshot,
            "inventory_source": pipeline_state.get("inventory_source", "database snapshot"),
            "execution_inventory_rows": len(pipeline_state.get("execution_inventory") or inventory),
            "demand": demand_plan,
            "core_workflow": core_workflow,
            "core_workflow_runs": core_runs,
            "rounds": rounds,
            "final_plans": final_plans,
            "final_verification": final_verification,
            "delivery": delivery,
            "closed_routes": closed_routes,
            "orchestration": schedule,
            "stage_metrics": stage_metrics,
            "failure_root_causes": failure_root_causes,
            "retrieval": evidence,
            "ai_reasoning": ai_reasoning,
            "agentarts_trace": agentarts_trace,
            "agent_decision": agent_decision,
            "ai_decision_matrix": ai_decision_matrix,
            "extension_reviews": extension_reviews,
            "extension_workflow_runs": extension_workflow_runs,
            "agentarts_status": agentarts.status,
            "llm_status": llm_service.status,
            "rework_directives": rework_directives,
            "prompt_adaptations": pipeline_state.get("prompt_adaptations", []),
            "active_prompt_versions": pipeline_state.get("active_prompt_versions", {}),
            "llm_decisions": llm_decisions,
            "execution_evidence": pipeline_state.get("execution_evidence", []),
            "execution_weights": pipeline_state.get("decision_weights", request.get("weights", {})),
            "memory_context": memory_context,
            "memory_writes": [
                {"agent_id": item["agent_id"], "memory_key": item["memory_key"], "confidence": item["confidence"]}
                for item in memory_writes
            ],
            "loop_metrics": {
                "attempts": len(rounds),
                "max_attempts": max_solution_attempts,
                "initial_score": round1_verification["score"],
                "final_score": final_verification["score"],
                "score_gain": final_verification["score"] - round1_verification["score"],
                "blockers_found": len(round1_verification["blocker_summary"]),
                "final_blockers": len(final_verification["blocker_summary"]),
                "closed_routes_excluded": len(closed_routes) if len(rounds) > 1 else 0,
                "adaptive_categories": sorted({category for item in rework_directives for category in item["categories"]}),
                "adaptive_prompt_versions": [item["active_version"] for item in pipeline_state.get("prompt_adaptations", [])],
                "adaptive_tool_overrides": list(dict.fromkeys(override for item in rework_directives for override in item.get("tool_overrides", []))),
                "workflow_paths": [item.get("mode", "") for item in rounds],
                "selected_repair_policies": [item.get("selected_policy", "") for item in rework_directives],
                "rework_rounds": len(rework_directives),
                "exhausted": final_verification["verdict"] != "pass" and len(rounds) >= max_solution_attempts,
                "converged": final_verification["verdict"] == "pass",
            },
            "data_mode": "SIMULATED",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        terminal_status = "awaiting_approval" if delivery["status"] == "ready" else "needs_human"
        db.update_run(run_id, terminal_status, result)
        emit(db, run_id, "awaiting.approval" if terminal_status == "awaiting_approval" else "human.escalation", "orchestrator", "推演完成，等待人工审批" if terminal_status == "awaiting_approval" else "推演转人工研判", delivery["summary"], {
            "status": terminal_status,
            "recommended_plan": (delivery.get("recommended_plan") or {}).get("name"),
        })
    except Exception as exc:
        logger.exception("Orchestration failed for run %s", run_id)
        try:
            emit(db, run_id, "run.failed", "orchestrator", "\u63a8\u6f14\u6267\u884c\u5931\u8d25", str(exc))
        except Exception:
            logger.exception("Failed to persist orchestration failure event for %s", run_id)
        db.update_run(run_id, "failed", {"error": str(exc), "run_id": run_id})
    finally:
        pending_tasks = [task for task in background_tasks if not task.done()]
        for task in pending_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*list(background_tasks), return_exceptions=True)
        if agent_executor is not None:
            await agent_executor.close()
        if http_client is not None:
            await http_client.aclose()
