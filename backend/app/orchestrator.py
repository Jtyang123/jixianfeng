from __future__ import annotations

import asyncio
import json
from datetime import datetime
from uuid import uuid4

import httpx

from .agentarts import AgentArtsBridge
from .agents import build_delivery_package, build_demand_plan, generate_plans, normalize_agentarts_decision, verify_plans
from .config import AGENTARTS_TIMEOUT, AGENT_RUNTIME, DEMO_PACING_FACTOR
from .database import Database
from .knowledge import retrieve
from .knowledge_base import KnowledgeRepository
from .llm import LLMService
from .registry import AgentRegistryService, apply_agent_load
from .scheduler import build_dynamic_schedule
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
    extension_workflows = workflow_repo.list_workflows(stage="post_verification", enabled_only=True)

    async def call_agentarts(agent_id: str, payload: dict) -> dict:
        if not agentarts.enabled:
            return {"mode": "local-fallback", "reason": "AgentArts 未配置"}
        try:
            endpoint = agentarts.endpoint_for(agent_id)
            emit(db, run_id, "agentarts.called", agent_id, f"调用 AgentArts · {AGENT_NAMES.get(agent_id, agent_id)}", "已向华为云 AgentArts Runtime 提交结构化任务。", {
                "endpoint": endpoint,
            })
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
            response = await task
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

        emit(db, run_id, "data.snapshot.loaded", "orchestrator", "数据快照已冻结", f"{snapshot['label']}，共 {snapshot['row_count']} 条库存记录。", {
            "snapshot_id": snapshot["id"],
            "material_rows": len(inventory),
            "warehouses": len(warehouses),
        })
        emit(db, run_id, "scheduler.decided", "orchestrator", "动态调度策略已生成", schedule["rationale"], {
            "complexity_score": schedule["complexity_score"],
            "parallel_branch": schedule["parallel_branch"],
            "loop": schedule["loop"],
        })
        emit(db, run_id, "rag.retrieved", "orchestrator", "RAG 规则检索完成", f"检索到 {len(evidence)} 条可引用规则。", {
            "documents": [{"id": item["id"], "title": item["title"], "score": item["score"]} for item in evidence],
        })
        await pace(0.25)

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
        await pace(0.3)

        emit(db, run_id, "agent.started", "matching_agent", "资源匹配智能体开始计算", "基于物资库、库存、车辆与路网生成三种目标方案。")
        await pace(0.45)
        round1_plans = generate_plans(
            demand_plan, inventory, warehouses, routes, vehicles,
            attempt=1, closed_routes=closed_routes,
        )
        emit(db, run_id, "tool.called", "matching_agent", "调用多目标方案生成器", f"已生成 {len(round1_plans)} 套候选方案。")
        ai_reasoning["matching_agent"] = await llm_service.reflect(
            "matching_agent",
            {
                "summary": "已生成三套多目标候选方案。",
                "plans": [{"name": plan["name"], "metrics": plan["metrics"]} for plan in round1_plans],
                "scheduler": schedule,
            },
            evidence,
        )
        emit(db, run_id, "plan.generated", "matching_agent", "第一轮候选方案生成", "极速直送、成本集约、韧性均衡方案已提交核验。", {
            "plans": [
                {"plan_id": plan["plan_id"], "name": plan["name"], "metrics": plan["metrics"]}
                for plan in round1_plans
            ]
        })
        emit(db, run_id, "agent.completed", "matching_agent", "第一轮匹配完成", f"生成 {len(round1_plans)} 套候选方案。")
        await pace(0.32)

        emit(db, run_id, "agent.started", "verifier_agent", "独立核验智能体开始审查", "核验道路、库存、运力与时效约束。")
        await pace(0.5)
        round1_verification = verify_plans(
            round1_plans, demand_plan, inventory, routes, vehicles, closed_routes
        )
        emit(db, run_id, "agent.completed", "verifier_agent", "第一轮核验完成", f"核验结论：{round1_verification['verdict']}。")
        emit(db, run_id, "verification.failed", "verifier_agent", "第一轮方案未通过", f"最高评分 {round1_verification['score']}，发现 {len(round1_verification['blocker_summary'])} 类阻断项。", {
            "score": round1_verification["score"],
            "verdict": round1_verification["verdict"],
            "blockers": round1_verification["blocker_summary"],
            "required_fixes": round1_verification["required_fixes"][:5],
        })
        await pace(0.35)

        rounds = [{
            "attempt": 1,
            "plans": round1_plans,
            "verification": round1_verification,
            "mode": "initial",
        }]
        final_plans = round1_plans
        final_verification = round1_verification

        if round1_verification["verdict"] == "rework":
            emit(db, run_id, "rework.requested", "orchestrator", "触发退回重算", "匹配智能体仅接收结构化核验意见，不透传核验智能体推理过程。", {
                "attempt": 2,
                "constraints": round1_verification["required_fixes"][:5],
            })
            await pace(0.35)
            emit(db, run_id, "agent.started", "matching_agent", "资源匹配智能体重新规划", "排除封闭主通道，改用备用路线并重新平衡仓库库存。")
            await pace(0.45)
            round2_plans = generate_plans(
                demand_plan, inventory, warehouses, routes, vehicles,
                attempt=2, closed_routes=closed_routes,
            )
            emit(db, run_id, "plan.revised", "matching_agent", "第二版方案生成", "已根据道路封闭约束重新计算运输路线与库存分配。", {
                "plans": [
                    {"plan_id": plan["plan_id"], "name": plan["name"], "metrics": plan["metrics"]}
                    for plan in round2_plans
                ]
            })
            emit(db, run_id, "agent.completed", "matching_agent", "修正方案生成完成", f"生成 {len(round2_plans)} 套修正方案。")
            await pace(0.3)

            emit(db, run_id, "agent.started", "verifier_agent", "独立核验智能体复审", "对修正方案重新执行全量规则校验。")
            await pace(0.45)
            round2_verification = verify_plans(
                round2_plans, demand_plan, inventory, routes, vehicles, closed_routes
            )
            rounds.append({
                "attempt": 2,
                "plans": round2_plans,
                "verification": round2_verification,
                "mode": "revised",
            })
            final_plans = round2_plans
            final_verification = round2_verification
            emit(db, run_id, "agent.completed", "verifier_agent", "第二轮核验完成", f"核验结论：{round2_verification['verdict']}。")
            if round2_verification["verdict"] == "pass":
                emit(db, run_id, "verification.passed", "verifier_agent", "修正方案通过核验", f"最高评分 {round2_verification['score']}，可进入方案交付。", {
                    "score": round2_verification["score"],
                    "approved_plan_ids": round2_verification["approved_plan_ids"],
                })
            else:
                emit(db, run_id, "verification.failed", "verifier_agent", "复审仍未通过", "需人工确认数据或调整资源约束。", {
                    "score": round2_verification["score"],
                    "blockers": round2_verification["blocker_summary"],
                })
            await pace(0.3)
        else:
            emit(db, run_id, "agent.completed", "verifier_agent", "首轮核验完成", "首轮方案通过核验。")
            emit(db, run_id, "verification.passed", "verifier_agent", "方案首轮通过核验", f"最高评分 {round1_verification['score']}。", {
                "score": round1_verification["score"],
                "approved_plan_ids": round1_verification["approved_plan_ids"],
            })
            await pace(0.25)

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
        extension_reviews = []
        extension_workflow_runs = []
        if extension_workflows:
            workflow_context = {
                "scenario": scenario,
                "demand": demand_plan,
                "plans": [{"plan_id": plan["plan_id"], "name": plan["name"], "metrics": plan["metrics"]} for plan in final_plans],
                "verification": {
                    "verdict": final_verification["verdict"],
                    "score": final_verification["score"],
                    "blockers": final_verification["blocker_summary"],
                    "warnings": final_verification.get("warnings", []),
                },
            }

            async def invoke_workflow_agent(agent_id: str, workflow_state: dict, node: dict) -> dict:
                emit(db, run_id, "workflow.node.started", agent_id, f"工作流节点 {node['id']} 开始", f"调用 Agent：{agent_id}")
                payload = {
                    **workflow_context,
                    "node_id": node["id"],
                    "node_outputs": workflow_state.get("node_outputs", {}),
                }
                return await call_agentarts(agent_id, payload)

            for workflow_record in extension_workflows:
                try:
                    engine = WorkflowEngine(
                        db,
                        invoke_agent=invoke_workflow_agent,
                        load_workflow=lambda workflow_id: workflow_repo.get_workflow(workflow_id),
                        event_sink=lambda node_id, message: emit(
                            db, run_id, "workflow.node", node_id, message, "",
                            {"workflow_id": workflow_record["id"]},
                        ),
                        agent_metrics=runtime_metrics,
                        max_workers=4,
                    )
                    workflow_result = await engine.execute(
                        workflow_record["id"], workflow_record["definition"], workflow_context,
                    )
                    extension_workflow_runs.append({
                        "workflow_id": workflow_record["id"],
                        "workflow_name": workflow_record["name"],
                        "run_id": workflow_result["run_id"],
                        "status": workflow_result["status"],
                        "metrics": workflow_result["metrics"],
                        "outputs": workflow_result["outputs"],
                    })
                    agent_names = {item["id"]: item["name"] for item in registry.list_agents()}
                    for output in workflow_result["outputs"].values():
                        if not isinstance(output, dict) or not output.get("agent_id"):
                            continue
                        agent_id = str(output["agent_id"])
                        extension_reviews.append({
                            "agent_id": agent_id,
                            "agent_name": agent_names.get(agent_id, agent_id),
                            "role": "workflow-extension",
                            "mode": output.get("mode", ""),
                            "parsed": output.get("parsed"),
                            "text": output.get("text", ""),
                        })
                except Exception as exc:
                    extension_workflow_runs.append({
                        "workflow_id": workflow_record["id"],
                        "workflow_name": workflow_record["name"],
                        "status": "failed",
                        "error": str(exc)[:500],
                    })
                    emit(db, run_id, "workflow.failed", "orchestrator", "扩展工作流执行失败", str(exc)[:240], {
                        "workflow_id": workflow_record["id"],
                    })
        elif extension_agents:
            async def run_extension(agent: dict) -> dict:
                agent_id = agent["id"]
                emit(db, run_id, "agent.started", agent_id, f"扩展审查开始 · {agent['name']}", "动态注册 Agent 已进入核验后的扩展审查节点。")
                response = await call_agentarts(agent_id, {
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

            extension_reviews = await asyncio.gather(*(run_extension(agent) for agent in extension_agents))
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
        emit(db, run_id, "agent.completed", "delivery_agent", "交付包生成完成", delivery["summary"], {
            "status": delivery["status"],
            "recommended_plan": (delivery.get("recommended_plan") or {}).get("name"),
            "alternatives": len(delivery.get("alternatives", [])),
        })

        result = {
            "run_id": run_id,
            "runtime": AGENT_RUNTIME,
            "runtime_label": "本地可复现编排" if AGENT_RUNTIME == "local" else "AgentArts 适配模式",
            "scenario": scenario,
            "event": request["event"],
            "weights": request.get("weights", {}),
            "snapshot": snapshot,
            "demand": demand_plan,
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
            "extension_reviews": extension_reviews,
            "extension_workflow_runs": extension_workflow_runs,
            "agentarts_status": agentarts.status,
            "llm_status": llm_service.status,
            "loop_metrics": {
                "attempts": len(rounds),
                "initial_score": round1_verification["score"],
                "final_score": final_verification["score"],
                "score_gain": final_verification["score"] - round1_verification["score"],
                "blockers_found": len(round1_verification["blocker_summary"]),
                "final_blockers": len(final_verification["blocker_summary"]),
                "closed_routes_excluded": len(closed_routes) if len(rounds) > 1 else 0,
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
        if http_client is not None:
            await http_client.aclose()
