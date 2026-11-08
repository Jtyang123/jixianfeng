from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import datetime
from statistics import mean
from typing import Any, Awaitable, Callable

from .config import AGENT_RUNTIME


AGENTS = [
    {
        "id": "demand_agent", "code": "A01", "name": "需求研判智能体", "version": "v1.3",
        "role": "把灾情、安置点与物资选择转换为结构化需求，识别假设与证据。",
        "model": "AgentArts / 可配置 LLM", "status": "online", "memory": "run-scoped blackboard",
        "skills": ["灾害语义理解", "需求拆分", "优先级判定", "单位标准化"],
        "tools": ["demand_estimator", "material_catalog_query", "rag_retrieve", "unit_normalizer"],
        "guardrails": ["不得虚构库存", "不得指定运输路线", "关键假设必须进入交付包"],
        "input_schema": "EventContext + DemandSelection", "output_schema": "DemandPlan",
    },
    {
        "id": "matching_agent", "code": "A02", "name": "资源匹配智能体", "version": "v1.4",
        "role": "基于库存、车辆、路网和目标权重生成多套候选调度方案。",
        "model": "AgentArts / 可配置 LLM", "status": "online", "memory": "versioned plan artifacts",
        "skills": ["多目标规划", "仓库分配", "备用路线选择", "约束修正"],
        "tools": ["inventory_query", "vehicle_query", "route_query", "multi_objective_planner"],
        "guardrails": ["不得修改库存", "不得替代核验", "关键数字由工具计算"],
        "input_schema": "DemandPlan + ResourceSnapshot + SchedulerPolicy", "output_schema": "DispatchPlanList",
    },
    {
        "id": "verifier_agent", "code": "A03", "name": "独立核验智能体", "version": "v1.5",
        "role": "只读审查候选方案，检查道路、库存、运力、时效与需求覆盖。",
        "model": "AgentArts / 确定性规则引擎", "status": "online", "memory": "isolated verification context",
        "skills": ["交叉核验", "硬阻断识别", "风险评分", "反馈生成"],
        "tools": ["inventory_verify", "capacity_verify", "route_verify", "eta_verify", "rule_engine"],
        "guardrails": ["不得编辑方案", "不得读取生成角色内部推理", "硬阻断不得豁免"],
        "input_schema": "DispatchPlan + RawSnapshot + EventConstraints", "output_schema": "VerificationReport",
    },
    {
        "id": "delivery_agent", "code": "A04", "name": "方案交付智能体", "version": "v1.2",
        "role": "汇总已通过核验的事实，生成方案对比、执行清单、风险提示和报告。",
        "model": "AgentArts / 可配置 LLM", "status": "online", "memory": "approved artifacts only",
        "skills": ["方案比较", "执行摘要", "报告生成", "证据引用"],
        "tools": ["plan_comparator", "report_renderer", "export_pdf"],
        "guardrails": ["不得修改数字", "不得删除风险", "必须等待人工审批"],
        "input_schema": "ApprovedPlans + VerificationReports", "output_schema": "DeliveryPackage",
    },
]

TOOLS = [
    {"id": "rag_retrieve", "name": "RAG 混合检索", "protocol": "Function Calling / Knowledge Base", "mode": "read-only", "description": "检索需求、道路、库存、运力和核验规则，返回带来源的证据片段。"},
    {"id": "inventory_query", "name": "库存快照查询", "protocol": "Function Calling / SQL", "mode": "read-only", "description": "按物资编码和仓库查询当前冻结快照的可用库存。"},
    {"id": "route_query", "name": "路网与封闭事件查询", "protocol": "Function Calling", "mode": "read-only", "description": "读取主备通道、ETA、道路状态和事件封闭清单。"},
    {"id": "multi_objective_planner", "name": "多目标方案生成器", "protocol": "Custom Tool", "mode": "compute", "description": "按时效、成本和风险权重生成三套候选方案。"},
    {"id": "rule_engine", "name": "确定性规则引擎", "protocol": "Custom Tool", "mode": "read-only", "description": "执行库存、道路、载重、时效和需求覆盖硬校验。"},
    {"id": "export_pdf", "name": "报告导出", "protocol": "Server API", "mode": "write-export", "description": "将已审批方案导出为可打印报告。"},
]

CORE_WORKFLOW_DEFINITION = {
    "nodes": [
        {"id": "A01", "type": "agent", "agent_id": "demand_agent", "depends_on": [], "priority": 96},
        {"id": "A02", "type": "agent", "agent_id": "matching_agent", "depends_on": ["A01"], "priority": 88},
        {"id": "A03", "type": "agent", "agent_id": "verifier_agent", "depends_on": ["A02"], "priority": 99},
    ],
    "description": "默认主干：需求研判 → 资源匹配 → 独立核验。可在工作流设计器中替换或插入节点。",
}

CORE_WORKFLOW = {
    "id": "core-default",
    "name": "默认主干工作流",
    "stage": "core",
    "version": 1,
    "enabled": True,
    "definition": CORE_WORKFLOW_DEFINITION,
}

WORKFLOW = {
    "entry": "event_confirmed",
    "nodes": [
        {"id": "event_confirmed", "label": "事件确认", "type": "trigger", "agent": "orchestrator"},
        {"id": "parallel_intake", "label": "并行理解与检索", "type": "parallel", "agent": "orchestrator"},
        {"id": "demand_agent", "label": "A01 需求研判", "type": "agent", "agent": "demand_agent"},
        {"id": "matching_agent", "label": "A02 资源匹配", "type": "agent", "agent": "matching_agent"},
        {"id": "verifier_agent", "label": "A03 独立核验", "type": "agent", "agent": "verifier_agent"},
        {"id": "extension_review", "label": "A05+ 动态扩展审查", "type": "dynamic", "agent": "registry"},
        {"id": "decision", "label": "通过？", "type": "condition", "agent": "orchestrator"},
        {"id": "rework", "label": "结构化反馈重算", "type": "loop", "agent": "matching_agent"},
        {"id": "human_gate", "label": "人工审批", "type": "human", "agent": "orchestrator"},
        {"id": "delivery_agent", "label": "A04 方案交付", "type": "agent", "agent": "delivery_agent"},
    ],
    "edges": [
        {"from": "event_confirmed", "to": "parallel_intake", "label": "启动"},
        {"from": "parallel_intake", "to": "demand_agent", "label": "需求分支"},
        {"from": "demand_agent", "to": "matching_agent", "label": "DemandPlan"},
        {"from": "matching_agent", "to": "verifier_agent", "label": "候选方案"},
        {"from": "verifier_agent", "to": "extension_review", "label": "核验报告"},
        {"from": "extension_review", "to": "decision", "label": "扩展意见"},
        {"from": "decision", "to": "rework", "label": "REWORK", "conditional": True},
        {"from": "rework", "to": "matching_agent", "label": "修正约束", "conditional": True},
        {"from": "decision", "to": "human_gate", "label": "PASS", "conditional": True},
        {"from": "human_gate", "to": "delivery_agent", "label": "人工确认"},
    ],
    "max_loop_attempts": 3,
    "stop_condition": "无硬阻断且核验评分不低于 80，或资源约束无法自动修复/达到三轮上限时转人工研判",
}


class AsyncPriorityExecutor:
    """Real async priority queue used by AgentArts cloud calls."""

    def __init__(self, priorities: dict[str, int], max_workers: int = 3, default_priority: int = 50) -> None:
        self.priorities = priorities
        self.max_workers = max(1, min(8, max_workers))
        self.default_priority = default_priority
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.records: list[dict] = []
        self.workers: list[asyncio.Task] = []
        self.sequence = 0
        self.active = 0
        self.peak_active = 0
        self.closed = False

    async def _ensure_started(self) -> None:
        if self.workers:
            return
        self.workers = [asyncio.create_task(self._worker(), name=f"farmmatch-agent-worker-{index}") for index in range(self.max_workers)]

    async def submit(self, agent_id: str, operation: Callable[[], Awaitable[Any]]) -> Any:
        if self.closed:
            raise RuntimeError("调度器已关闭")
        await self._ensure_started()
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self.sequence += 1
        priority = int(self.priorities.get(agent_id, self.default_priority))
        queued_at = datetime.now().astimezone()
        await self.queue.put((-priority, self.sequence, agent_id, operation, future, queued_at))
        return await future

    async def _worker(self) -> None:
        while True:
            _, _, agent_id, operation, future, queued_at = await self.queue.get()
            if operation is None:
                self.queue.task_done()
                return
            started_at = datetime.now().astimezone()
            started = time.perf_counter()
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            status = "success"
            try:
                result = await operation()
            except Exception as exc:
                status = "failed"
                if not future.done():
                    future.set_exception(exc)
                result = None
            finally:
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                completed_at = datetime.now().astimezone()
                wait_ms = round((started_at - queued_at).total_seconds() * 1000, 2)
                self.active -= 1
                self.records.append({
                    "agent_id": agent_id,
                    "priority": int(self.priorities.get(agent_id, self.default_priority)),
                    "queued_at": queued_at.isoformat(timespec="milliseconds"),
                    "started_at": started_at.isoformat(timespec="milliseconds"),
                    "completed_at": completed_at.isoformat(timespec="milliseconds"),
                    "wait_ms": wait_ms,
                    "execution_ms": elapsed_ms,
                    "status": status,
                })
                self.queue.task_done()
            if status == "success" and not future.done():
                future.set_result(result)

    def metrics(self) -> dict:
        waits = [item["wait_ms"] for item in self.records]
        executions = [item["execution_ms"] for item in self.records]
        by_agent: dict[str, list[dict]] = defaultdict(list)
        for item in self.records:
            by_agent[item["agent_id"]].append(item)
        return {
            "engine": "asyncio.PriorityQueue",
            "workers": self.max_workers,
            "total_tasks": len(self.records),
            "successful_tasks": sum(1 for item in self.records if item["status"] == "success"),
            "failed_tasks": sum(1 for item in self.records if item["status"] == "failed"),
            "peak_concurrency": self.peak_active,
            "average_wait_ms": round(mean(waits), 2) if waits else 0.0,
            "max_wait_ms": round(max(waits), 2) if waits else 0.0,
            "average_execution_ms": round(mean(executions), 2) if executions else 0.0,
            "agents": {
                agent_id: {
                    "samples": len(items),
                    "average_wait_ms": round(mean(item["wait_ms"] for item in items), 2),
                    "average_execution_ms": round(mean(item["execution_ms"] for item in items), 2),
                }
                for agent_id, items in by_agent.items()
            },
            "records": self.records,
        }

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for _ in self.workers:
            self.sequence += 1
            await self.queue.put((999, self.sequence, "", None, None, datetime.now().astimezone()))
        await asyncio.gather(*self.workers, return_exceptions=True)

def build_dynamic_schedule(request: dict, closed_routes: list[str], demand_count: int) -> dict:
    affected = int(request.get("event", {}).get("affected_population", 0))
    complexity = min(100, round(18 + demand_count * 5 + len(closed_routes) * 2 + affected / 600))
    urgency = 1.0 if request.get("event", {}).get("severity") in {"I级", "II级"} else 0.7
    priorities = [
        {"agent_id": "demand_agent", "priority": 96, "batch": "parallel", "reason": "P0物资依赖需求标准化结果"},
        {"agent_id": "rag_retrieve", "priority": 94, "batch": "parallel", "reason": "规则检索与需求理解可并行执行"},
        {"agent_id": "matching_agent", "priority": round(88 * urgency), "batch": "sequential", "reason": f"场景复杂度 {complexity}，需要多目标生成"},
        {"agent_id": "verifier_agent", "priority": 99, "batch": "gated", "reason": "安全关键节点，必须全量硬规则核验"},
        {"agent_id": "delivery_agent", "priority": 70, "batch": "post-approval", "reason": "只消费已核验事实"},
    ]
    return {
        "engine": "capability-weighted DAG scheduler",
        "runtime": AGENT_RUNTIME,
        "complexity_score": complexity,
        "parallel_branch": True,
        "dispatch": priorities,
        "loop": {
            "enabled": True,
            "max_attempts": 3,
            "feedback_channel": "structured verification issue codes only",
            "escalation": "human_escalation",
        },
        "rationale": "并行执行需求研判与规则检索；匹配完成后由独立核验门控；核验失败仅回传问题代码、证据和修正要求，按道路、库存、运力、时效问题选择修复策略，最多执行三轮。",
    }


def platform_manifest() -> dict[str, Any]:
    return {
        "platform": "急先锋 · 应急资源调度推演平台",
        "version": "1.0.0",
        "runtime": AGENT_RUNTIME,
        "agents": AGENTS,
        "tools": TOOLS,
        "workflow": WORKFLOW,
        "knowledge": {
            "retrieval": "Chinese character bigram + keyword hybrid retrieval",
            "source_count": 6,
            "citation_required": True,
        },
        "memory": {
            "type": "versioned blackboard",
            "persistence": "SQLite agent_events + run artifacts",
            "replay_supported": True,
        },
        "score_alignment": [
            {"item": "平台功能完整性", "evidence": "Agent 定义、动态扩展审查、DAG 编排、运行监控、独立核验和反馈回路"},
            {"item": "技术可靠性与可复现性", "evidence": "本地确定性运行、库存快照、事件轨迹、自动化测试和失败转人工"},
            {"item": "人工智能使用合理性", "evidence": "AgentArts 受限推荐、扩展审查、RAG 证据和确定性核验护栏"},
            {"item": "文档质量", "evidence": "方案书、演示脚本、Agent 契约、接口说明和可复现运行记录"},
        ],
    }
