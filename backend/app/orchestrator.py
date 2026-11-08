from __future__ import annotations

import asyncio
import json
from datetime import datetime
from uuid import uuid4

import httpx

from .agentarts import AgentArtsBridge
from .agents import build_delivery_package, build_demand_plan, build_rework_directive, generate_plans, normalize_agentarts_decision, verify_plans
from .config import AGENTARTS_TIMEOUT, AGENT_RUNTIME, DEMO_PACING_FACTOR
from .database import Database
from .knowledge import retrieve
from .knowledge_base import KnowledgeRepository
from .local_agents import invoke_local_registered_agent
from .llm import LLMService
from .registry import AgentRegistryService, apply_agent_load
from .scheduler import AsyncPriorityExecutor, CORE_WORKFLOW, build_dynamic_schedule
from .workflow_engine import WorkflowEngine, WorkflowRepository

CORE_AGENT_IDS = {"demand_agent", "matching_agent", "verifier_agent", "delivery_agent"}

AGENT_NAMES = {
    "orchestrator": "编排控制器",
    "demand_agent": "需求研判智能体",
    "matching_agent": "资源匹配智能体",
    "verifier_agent": "独立核验智能体",
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


def build_ai_decision_matrix(agent_decision: dict, traces: dict, verification: dict, extension_reviews: list[dict], llm_status: dict) -> list[dict]:
    demand_trace = traces.get("demand_agent") or {}
    matching_trace = traces.get("matching_agent") or {}
    verifier_trace = traces.get("verifier_agent") or {}
    delivery_trace = traces.get("delivery_agent") or {}
    demand_parsed = demand_trace.get("parsed") if isinstance(demand_trace, dict) else None
    verifier_parsed = verifier_trace.get("parsed") if isinstance(verifier_trace, dict) else None
    delivery_parsed = delivery_trace.get("parsed") if isinstance(delivery_trace, dict) else None
    return [
        {
            "id": "D1", "decision": "需求理解与假设补全",
            "ai_role": "补充摘要、假设和证据引用",
            "applied": bool(demand_parsed),
            "source": demand_trace.get("mode", "local-deterministic") if isinstance(demand_trace, dict) else "local-deterministic",
            "boundary": "物资数量、单位和安置点分配仍由确定性工具计算",
            "fallback": "本地需求拆分与主数据校验",
        },
        {
            "id": "D2", "decision": "已核验方案推荐",
            "ai_role": "在通过硬规则的方案中选择推荐方案",
            "applied": bool(agent_decision.get("applied")),
            "source": agent_decision.get("source", "deterministic-fallback"),
            "boundary": "只能从已通过独立核验的方案集合中选择",
            "fallback": "按速度、成本和风险权重排序",
        },
        {
            "id": "D3", "decision": "核验解释与风险归纳",
            "ai_role": "解释核验问题并补充风险说明",
            "applied": bool(verifier_parsed),
            "source": verifier_trace.get("mode", "deterministic-rules") if isinstance(verifier_trace, dict) else "deterministic-rules",
            "boundary": "硬规则结论不可被模型覆盖",
            "fallback": "确定性规则引擎给出最终 verdict",
        },
        {
            "id": "D4", "decision": "扩展 Agent 风险合成",
            "ai_role": "补充领域风险和现场提示",
            "applied": bool(extension_reviews),
            "source": "registered-extension-agents" if extension_reviews else "none",
            "boundary": "扩展意见只追加风险，不能删除或修改主方案数字",
            "fallback": "跳过扩展审查，保留确定性风险清单",
        },
        {
            "id": "D5", "decision": "交付摘要组织",
            "ai_role": "将已核验事实组织成可读摘要",
            "applied": bool(delivery_parsed),
            "source": delivery_trace.get("mode", "local-template") if isinstance(delivery_trace, dict) else "local-template",
            "boundary": "仅使用已核验数字和事实",
            "fallback": "本地交付模板与证据链",
        },
    ]

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
    for agent_id, endpoint in registry.active_endpoints().items():
        agentarts.override_endpoint(agent_id, endpoint)
    runtime_metrics = registry.runtime_metrics()
    ai_reasoning: dict = {}
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

    async def call_agentarts(agent_id: str, payload: dict) -> dict:
        if not agentarts.enabled:
            return {"mode": "local-fallback", "reason": "AgentArts 未配置"}
        try:
            endpoint = agentarts.endpoint_for(agent_id)
            emit(db, run_id, "agentarts.called", agent_id, f"调用 AgentArts · {AGENT_NAMES.get(agent_id, agent_id)}", "已向华为云 AgentArts Runtime 提交结构化任务。", {
                "endpoint": endpoint,
            })
            async def invoke_and_wait() -> dict:
                task = asyncio.create_task(
                    agentarts.invoke(agent_id, payload, session_id=run_id, client=http_client)
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
            agentarts_trace[agent_id] = response
            emit(db, run_id, "agentarts.completed", agent_id, f"AgentArts 返回 · {AGENT_NAMES.get(agent_id, agent_id)}", "已完成云端智能体调用，结果等待确定性规则复核。", {
                "session_id": run_id,
            })
            return response
        except Exception as exc:
            fallback = {"mode": "local-fallback-after-agentarts-error", "error": str(exc)[:240]}
            agentarts_trace[agent_id] = fallback
            emit(db, run_id, "agentarts.fallback", agent_id, "AgentArts 调用回退", "云端调用失败，已使用本地确定性结果继续运行。", fallback)
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

        pipeline_state: dict = {"demand": None, "plans": None, "verification": None, "demand_workflows_done": False}
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
                    demand_remote = await call_agentarts("demand_agent", {"summary": demand_plan["summary"], "items": demand_plan["items"], "evidence": evidence})
                    remote_demand = demand_remote.get("parsed") if isinstance(demand_remote, dict) else None
                    if remote_demand:
                        demand_plan["agentarts_interpretation"] = remote_demand
                        if remote_demand.get("summary"):
                            demand_plan["summary"] = remote_demand["summary"]
                        if remote_demand.get("assumptions"):
                            demand_plan["assumptions"] = list(dict.fromkeys(demand_plan["assumptions"] + [str(item) for item in remote_demand["assumptions"]]))
                        if remote_demand.get("evidence_refs"):
                            demand_plan["evidence_refs"] = list(dict.fromkeys(demand_plan["evidence_refs"] + [str(item) for item in remote_demand["evidence_refs"]]))
                    emit(db, run_id, "tool.called", "demand_agent", "调用需求标准化工具", f"生成 {len(demand_plan['items'])} 条安置点需求。")
                    emit(db, run_id, "agent.completed", "demand_agent", "需求标准化完成", demand_plan["summary"], {
                        "items": len(demand_plan["items"]),
                        "confidence": demand_plan["confidence"],
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
                    pipeline_state["demand"], inventory, warehouses, routes, vehicles,
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
                        "scheduler": schedule,
                    },
                    evidence,
                )
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
                    pipeline_state["plans"], pipeline_state["demand"], inventory, routes, vehicles, closed_routes
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

            if agent_id == "verification_vote":
                base = pipeline_state.get("verification")
                if not base:
                    base = verify_plans(pipeline_state["plans"], pipeline_state["demand"], inventory, routes, vehicles, closed_routes)
                votes = []
                for output in workflow_state.get("node_outputs", {}).values():
                    if not isinstance(output, dict):
                        continue
                    parsed = output.get("parsed") if isinstance(output.get("parsed"), dict) else {}
                    verdict = output.get("verdict") or parsed.get("verdict")
                    if verdict:
                        votes.append({"agent_id": output.get("agent_id", ""), "verdict": str(verdict), "score": parsed.get("score")})
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
            candidate_verification = pipeline_state.get("verification")
            if not candidate_verification and pipeline_state.get("plans"):
                candidate_verification = verify_plans(
                    pipeline_state["plans"], pipeline_state["demand"], inventory, routes, vehicles, closed_routes
                )
            response = await invoke_extension_agent(agent_id, {
                "scenario": scenario,
                "demand": pipeline_state.get("demand"),
                "plans": pipeline_state.get("plans") or [],
                "verification": candidate_verification or {},
                "node_outputs": workflow_state.get("node_outputs", {}),
            })
            return {**response, "agent_id": agent_id}

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
            )
            core_result = await engine.execute(
                core_workflow["id"], core_workflow["definition"],
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
                verification = verify_plans(plans, pipeline_state["demand"], inventory, routes, vehicles, closed_routes)
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
            next_directive = build_rework_directive(verification, next_attempt)
            if not next_directive["can_retry"] or next_attempt > max_solution_attempts:
                emit(db, run_id, "rework.escalated", "orchestrator", "资源约束无法自动修复", next_directive["reason"], {
                    "attempt": next_attempt,
                    "issue_codes": next_directive["issue_codes"],
                    "categories": next_directive["categories"],
                })
                break
            rework_directives.append(next_directive)
            emit(db, run_id, "rework.requested", "orchestrator", f"触发第 {next_attempt} 轮自适应重算", next_directive["reason"], {
                "attempt": next_attempt,
                "constraints": verification["required_fixes"][:5],
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
        }
        verifier_payload = {
            "verdict": final_verification["verdict"],
            "score": final_verification["score"],
            "blocker_summary": final_verification["blocker_summary"],
            "required_fixes": final_verification["required_fixes"],
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

        matching_remote, verifier_remote = await asyncio.gather(
            call_agentarts("matching_agent", matching_payload),
            run_verifier_review(),
        )
        remote_matching = matching_remote.get("parsed") if isinstance(matching_remote, dict) else None
        if remote_matching:
            rounds[-1]["agentarts_interpretation"] = remote_matching
            reasoning = ai_reasoning.setdefault("matching_agent", {})
            if isinstance(reasoning, dict):
                reasoning["agentarts_interpretation"] = remote_matching
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
        emit(db, run_id, "agent.started", "delivery_agent", "方案交付智能体汇总结果", "仅使用已通过核验的事实生成对比与交付包。")
        await pace(0.4)
        delivery = build_delivery_package(
            final_verification, scenario, snapshot, request.get("weights", {}), agent_decision=agent_decision
        )
        delivery["extension_reviews"] = extension_reviews
        for review in extension_reviews:
            parsed = review.get("parsed") or {}
            risks = parsed.get("risks") or parsed.get("warnings") or parsed.get("risk_alerts") or []
            if isinstance(risks, str):
                risks = [risks]
            for risk in risks:
                risk_text = risk if isinstance(risk, str) else json.dumps(risk, ensure_ascii=False)
                delivery.setdefault("risk_alerts", []).append(f"{review['agent_name']}：{risk_text}")
        ai_reasoning["delivery_agent"] = await llm_service.reflect("delivery_agent", delivery, evidence)
        delivery_remote = await call_agentarts("delivery_agent", {"delivery": delivery, "evidence": evidence})
        remote_delivery = delivery_remote.get("parsed") if isinstance(delivery_remote, dict) else None
        if remote_delivery:
            delivery["agentarts_interpretation"] = remote_delivery
            reasoning = ai_reasoning.setdefault("delivery_agent", {})
            if isinstance(reasoning, dict):
                reasoning["agentarts_interpretation"] = remote_delivery
        ai_decision_matrix = build_ai_decision_matrix(
            agent_decision, agentarts_trace, final_verification, extension_reviews, llm_service.status
        )
        emit(db, run_id, "agent.completed", "delivery_agent", "交付包生成完成", delivery["summary"], {
            "status": delivery["status"],
            "recommended_plan": (delivery.get("recommended_plan") or {}).get("name"),
            "alternatives": len(delivery.get("alternatives", [])),
        })
        scheduler_metrics = agent_executor.metrics() if agent_executor else {}
        schedule["runtime_metrics"] = scheduler_metrics

        result = {
            "run_id": run_id,
            "runtime": AGENT_RUNTIME,
            "runtime_label": "本地可复现编排" if AGENT_RUNTIME == "local" else "AgentArts 适配模式",
            "scenario": scenario,
            "event": request["event"],
            "weights": request.get("weights", {}),
            "snapshot": snapshot,
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
        emit(db, run_id, "run.failed", "orchestrator", "推演执行失败", str(exc))
        db.update_run(run_id, "failed", {"error": str(exc), "run_id": run_id})
    finally:
        if agent_executor is not None:
            await agent_executor.close()
        if http_client is not None:
            await http_client.aclose()
