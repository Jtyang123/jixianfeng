from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .config import LLM_API_KEY, LLM_BASE_URL, LLM_ENABLED, LLM_MODEL


logger = logging.getLogger(__name__)


PLANNER_SYSTEM_PROMPT = """你是应急调度Agent的决策层。你需要根据灾情信息和可用工具，规划任务执行步骤。
你不能直接计算数字，你只能决定调用什么工具、按什么顺序调用、判断结果是否符合预期。
所有事实数字必须来自工具结果。生成、核验和交付工具不得被跳过。
只输出合法JSON对象，不要输出Markdown或额外解释。"""

TOOL_CONTRACTS = {
    "demand_plan": "根据事件与物资选择生成标准化需求。",
    "inventory_query": "读取冻结库存快照和可用量。",
    "route_query": "读取道路、封闭路线和 ETA。",
    "vehicle_query": "读取车辆、载重和温区。",
    "knowledge_search": "检索规则、物流方案和核验依据。",
    "generate_plans": "基于需求与资源生成多套候选方案。",
    "verify_plans": "独立核验道路、库存、运力、时效和覆盖。",
    "delivery_package": "从已核验方案生成交付包。",
    "escalate_human": "无法安全完成任务时转人工。",
}

MANDATORY_TOOLS = ("demand_plan", "generate_plans", "verify_plans", "delivery_package")


def _fallback_plan(available_tools: list[str]) -> dict:
    ordered = [tool for tool in MANDATORY_TOOLS if tool in available_tools]
    steps = []
    for index, tool in enumerate(ordered, start=1):
        steps.append({
            "id": f"S{index}",
            "tool": tool,
            "reason": TOOL_CONTRACTS.get(tool, "执行确定性工具"),
            "input": {},
            "expected_output": f"{tool}_result",
        })
    return {
        "mode": "deterministic-fallback",
        "summary": "LLM Planner 不可用，使用安全基线计划。",
        "steps": steps[:10],
    }


class LLMPlanner:
    def __init__(
        self,
        enabled: bool | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.base_url = (base_url if base_url is not None else LLM_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else LLM_API_KEY
        self.model = model if model is not None else LLM_MODEL
        configured = enabled if enabled is not None else LLM_ENABLED
        self.enabled = bool(configured and self.base_url and self.api_key and self.model and self.api_key not in {"sk-xxx", "your-key"})

    @property
    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "role": "planning-and-replanning",
            "model": self.model if self.enabled else "deterministic-fallback",
            "base_url_configured": bool(self.base_url),
            "response_format": "json_object",
        }

    async def _request_json(self, system_prompt: str, user_payload: dict) -> dict | None:
        if not self.enabled:
            return None
        request = {
            "model": self.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=request)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                return parsed if isinstance(parsed, dict) else None
        except Exception:
            logger.warning("LLM planner request failed; deterministic fallback will be used", exc_info=True)
            return None

    @staticmethod
    def _normalize_step(raw: dict, index: int, allowed_tools: set[str]) -> dict | None:
        tool = str(raw.get("tool") or raw.get("tool_name") or "").strip()
        if tool not in allowed_tools:
            return None
        return {
            "id": str(raw.get("id") or f"S{index}"),
            "tool": tool,
            "reason": str(raw.get("reason") or TOOL_CONTRACTS.get(tool, ""))[:500],
            "input": raw.get("input") if isinstance(raw.get("input"), dict) else {},
            "expected_output": str(raw.get("expected_output") or f"{tool}_result")[:200],
        }

    async def create_plan(self, user_request: dict, available_tools: list[str]) -> dict:
        allowed_tools = {tool for tool in available_tools if tool in TOOL_CONTRACTS}
        fallback = _fallback_plan(available_tools)
        raw = await self._request_json(PLANNER_SYSTEM_PROMPT, {
            "task": "create_plan",
            "user_request": user_request,
            "available_tools": [
                {"name": tool, "description": TOOL_CONTRACTS[tool]}
                for tool in sorted(allowed_tools)
            ],
            "output_schema": {
                "summary": "string",
                "steps": [{
                    "id": "string", "tool": "available_tools.name",
                    "reason": "string", "input": "object", "expected_output": "string",
                }],
            },
            "constraints": [
                "最多10步",
                "只能使用 available_tools 中的工具",
                "必须包含 demand_plan、generate_plans、verify_plans、delivery_package",
                "verify_plans 未通过时不得直接 delivery_package",
            ],
        })
        if not raw:
            return fallback
        steps = []
        for index, item in enumerate(raw.get("steps") or [], start=1):
            if not isinstance(item, dict):
                continue
            normalized = self._normalize_step(item, index, allowed_tools)
            if normalized:
                steps.append(normalized)
        present = {item["tool"] for item in steps}
        for tool in MANDATORY_TOOLS:
            if tool in allowed_tools and tool not in present:
                steps.append({
                    "id": f"S{len(steps) + 1}", "tool": tool,
                    "reason": "安全基线要求，LLM计划缺少该必需工具，已自动补齐。",
                    "input": {}, "expected_output": f"{tool}_result",
                })
        if not steps:
            return fallback
        return {
            "mode": "llm-planner",
            "summary": str(raw.get("summary") or "LLM已生成调度计划。")[:500],
            "steps": steps[:10],
        }

    async def decide(self, step_result: dict, remaining_steps: list[dict], current_context: dict) -> dict:
        next_fallback = remaining_steps[0]["tool"] if remaining_steps else "stop"
        fallback = {
            "mode": "deterministic-fallback",
            "decision": "stop" if next_fallback == "stop" else "continue",
            "next_tool": None if next_fallback == "stop" else next_fallback,
            "reason": "LLM决策不可用，按原计划继续。",
            "repair_policy": "",
        }
        raw = await self._request_json(PLANNER_SYSTEM_PROMPT, {
            "task": "decide_next_step",
            "step_result": step_result,
            "remaining_steps": remaining_steps[:10],
            "current_context": current_context,
            "output_schema": {
                "decision": "continue|replan|stop|human",
                "next_tool": "string|null",
                "reason": "string",
                "repair_policy": "string",
            },
            "constraints": [
                "不得把未通过核验的方案标记为完成",
                "verify_plans 失败时只能 replan、continue 到修复步骤或 human",
                "不能直接计算或修改工具数字",
            ],
        })
        if not raw:
            return fallback
        decision = str(raw.get("decision") or "continue").lower()
        if decision not in {"continue", "replan", "stop", "human"}:
            decision = "continue"
        next_tool_raw = str(raw.get("next_tool") or "")
        allowed_next = {item.get("tool") for item in remaining_steps}
        if decision == "replan" and next_tool_raw in TOOL_CONTRACTS:
            next_tool = next_tool_raw
        else:
            next_tool = next_tool_raw if next_tool_raw in allowed_next else fallback["next_tool"]
        return {
            "mode": "llm-planner",
            "decision": decision,
            "next_tool": next_tool,
            "reason": str(raw.get("reason") or "LLM完成下一步判断。")[:500],
            "repair_policy": str(raw.get("repair_policy") or "")[:100],
        }