from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from uuid import uuid4

import httpx

from .agentarts import AgentArtsBridge
from .agents import allowed_rework_policies, apply_llm_priority_adjustments, build_delivery_package, build_demand_plan, build_prompt_adaptation, build_rework_directive, generate_plans, normalize_agentarts_decision, normalize_llm_weight_profile, verify_plans
from .config import AGENTARTS_TIMEOUT, AGENT_RUNTIME, DEMO_PACING_FACTOR, RETRY_MAX_ATTEMPTS
from .database import Database
from .knowledge import retrieve
from .knowledge_base import KnowledgeRepository, sanitize_agent_payload
from .local_agents import invoke_local_registered_agent
from .llm import LLMService
from .memory import MemoryRepository
from .registry import AgentRegistryService, apply_agent_load
from .reservations import InventoryReservationService
from .retry_queue import RetryQueueRepository
from .scheduler import AsyncPriorityExecutor, CORE_WORKFLOW, build_dynamic_schedule
from .tools import ToolError, ToolService
from .workflow_engine import WorkflowEngine, WorkflowRepository

logger = logging.getLogger(__name__)

CORE_AGENT_IDS = {"demand_agent", "matching_agent", "verifier_agent", "delivery_agent"}

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


def build_ai_decision_matrix(
    agent_decision: dict, traces: dict, verification: dict, extension_reviews: list[dict],
    llm_status: dict, llm_decisions: dict | None = None,
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
    demand_priority_decision = llm_decisions.get("demand_priority") or {}
    weight_decision = llm_decisions.get("matching_weights") or {}
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
            "applied": bool(rework_parsed) and rework_trace.get("mode") == "agentarts",
            "source": rework_trace.get("mode", "deterministic-fallback") if isinstance(rework_trace, dict) else "deterministic-fallback",
            "boundary": "只能选择 allowed_policies，硬修复项仍由规则层强制执行",
            "fallback": "按道路、库存、运力、时效问题代码选择默认策略",
            "local_equivalent": "问题代码到允许策略的确定性映射；历史成功策略仅在允许集合内做 tie-break",
        },
        {
            "id": "D3", "decision": "已核验方案推荐", "level": "AI 决策",
            "ai_role": "在通过硬规则的方案中选择最终推荐方案",
            "applied": bool(agent_decision.get("applied")),
            "source": agent_decision.get("source", "deterministic-fallback"),
            "boundary": "只能从已通过独立核验的方案集合中选择",
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
            "applied": bool(delivery_parsed),
            "source": delivery_trace.get("mode", "local-template") if isinstance(delivery_trace, dict) else "local-template",
            "boundary": "仅使用已核验数字和事实",
            "fallback": "本地交付模板与证据链",
            "local_equivalent": "确定性交付模板、方案对比、执行清单、风险提示与证据引用",
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


async def orchestrate(db: Database, run_id: str, request: dict) -> None:
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
        safe_payload = sanitize_agent_payload(payload)
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
            emit(db, run_id, "agentarts.completed", agent_id, f"AgentArts 返回 · {AGENT_NAMES.get(agent_id, agent_id)}", "已完成云端智能体调用，结果等待确定性规则复核。", {
                "session_id": run_id,
            })
            return response
        except Exception as exc:
            fallback = {"mode": "local-fallback-after-agentarts-error", "error": str(exc)[:240]}
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
    try:
        db.set_run_status(run_id, "running")
        emit(db, run_id, "run.started", "orchestrator", "推演任务启动", "已加载事件、需求与最新资源快照。", {
            "runtime": AGENT_RUNTIME,
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
        selected_ids = {int(item["material_id"]) for item in request["demand_items"]}
        selected_materials = [item for item in materials if item["id"] in selected_ids]
        query = " ".join([
            scenario["event_type"], scenario["severity"], scenario["description"],
            "道路封闭 库存 运力 时效 独立核验 反馈循环",
            " ".join(item["name"] for item in selected_materials),
        ])
        evidence = retrieve(query, top_k=4)
        uploaded_evidence = KnowledgeRepository(db).search(query, top_k=3)
        evidence = sorted(evidence + uploaded_evidence, key=lambda item: float(item.get("score", 0)), reverse=True)[:7]
        memory_context = memory_repo.summary(["demand_agent", "matching_agent", "verifier_agent", "delivery_agent"])
        emit(db, run_id, "memory.loaded", "orchestrator", "跨运行记忆已加载", f"加载 {sum(len(items) for items in memory_context.values())} 条可审计记忆。", {
            "agents": {agent_id: list(items) for agent_id, items in memory_context.items()},
            "scope": "仅作上下文与安全 tie-break，不覆盖硬规则",
        })
        schedule = apply_agent_load(build_dynamic_schedule(request, closed_routes, len(request["demand_items"])), runtime_metrics)
        agent_executor = AsyncPriorityExecutor(
            {item["agent_id"]: int(item["priority"]) for item in schedule["dispatch"]},
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
        })
        emit(db, run_id, "rag.retrieved", "orchestrator", "RAG 规则检索完成", f"检索到 {len(evidence)} 条可引用规则。", {
            "documents": [{"id": item["id"], "title": item["title"], "score": item["score"]} for item in evidence],
        })
        await pace(0.25)

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
        await run_stage_workflows("post_verification", {
            "scenario": scenario,
            "demand": demand_plan,
            "plans": [{"plan_id": plan["plan_id"], "name": plan["name"], "metrics": plan["metrics"]} for plan in final_plans],
            "verification": final_verification,
        })
        if not extension_workflows and extension_agents:
            async def run_extension(agent: dict) -> dict:
                agent_id = agent["id"]
                emit(db, run_id, "agent.started", agent_id, f"扩展审查开始 · {agent['name']}", "动态注册 Agent 已进入核验后的扩展审查节点。")
                response = await invoke_extension_agent(agent_id, {
                    "scenario": scenario,
                    "demand": demand_plan,
                    "plans": [{"plan_id": plan["plan_id"], "name": plan["name"], "metrics": plan["metrics"]} for plan in final_plans],
                    "verification": {
                        "verdict": final_verification["verdict"],
                        "score": final_verification["score"],
                        "blockers": final_verification["blocker_summary"],
                        "warnings": final_verification.get("warnings", []),
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
        await pace(0.4)
        delivery = build_delivery_package(
            final_verification, scenario, snapshot, pipeline_state.get("decision_weights") or request.get("weights", {}),
            agent_decision=agent_decision,
        )
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
            if consistent:
                for field in ("risk_alerts", "actions"):
                    incoming = remote_delivery.get(field) or []
                    if isinstance(incoming, str):
                        incoming = [incoming]
                    merged = list(delivery.get(field) or [])
                    merged.extend(str(item) for item in incoming if item and str(item) not in merged)
                    delivery[field] = merged
                if remote_delivery.get("summary"):
                    delivery["agentarts_summary"] = remote_delivery["summary"]
                interpretation = {
                    **remote_delivery,
                    "review_status": "applied",
                    "reviewed_delivery_mode": "provisional-parallel",
                    "recommendation_consistency": "consistent",
                    "final_recommended_plan_id": final_recommendation,
                }
            else:
                interpretation = {
                    "review_status": "discarded",
                    "reviewed_delivery_mode": "provisional-parallel",
                    "cloud_recommended_plan_id": cloud_recommendation,
                    "final_recommended_plan_id": final_recommendation,
                    "recommendation_consistency": (
                        "discarded-plan-mismatch" if cloud_recommendation in valid_plan_ids else "discarded-stale-plan"
                    ),
                    "discarded_fields": ["summary", "risk_alerts", "actions", "alternatives"],
                    "note": "\u4e91\u7aef\u5ba1\u67e5\u57fa\u4e8e\u5e76\u884c\u9884\u89c8\u5305\uff0c\u4e0e\u6700\u7ec8\u63a8\u8350\u4e0d\u4e00\u81f4\uff0c\u5df2\u4e22\u5f03\u5176\u53d9\u4e8b\u5185\u5bb9\uff0c\u4ec5\u4fdd\u7559\u5ba1\u8ba1\u5143\u6570\u636e\u3002",
                }
            delivery["agentarts_interpretation"] = interpretation
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

        result = {
            "run_id": run_id,
            "runtime": AGENT_RUNTIME,
            "runtime_label": "本地可复现编排" if AGENT_RUNTIME == "local" else "AgentArts 适配模式",
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
