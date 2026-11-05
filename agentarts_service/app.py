"""FarmMatch AgentArts runtime.

Deploy this service to Huawei Cloud AgentArts. The FastAPI backend calls the
AgentArts SDK endpoint POST /invocations for each of the four agent roles.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
from agentarts.sdk import AgentArtsRuntimeApp

app = AgentArtsRuntimeApp()

SYSTEM_PROMPTS = {
    "demand_agent": (
        "你是急先锋需求研判智能体。根据事件、安置点、物资选择和证据生成结构化需求摘要。"
        "禁止虚构库存、路线或认证信息。只返回JSON。"
    ),
    "matching_agent": (
        "你是急先锋资源匹配智能体。解释多目标方案和反馈修正策略。"
        "不得改写工具返回的库存、成本、ETA和载重数字。只返回JSON。"
    ),
    "verifier_agent": (
        "你是急先锋独立核验智能体。硬规则已经给出结论，你只能解释阻断原因和修正方向。"
        "存在硬阻断时必须保持rework。只返回JSON。"
    ),
    "delivery_agent": (
        "你是急先锋方案交付智能体。基于已通过核验的事实生成执行摘要。"
        "不得修改数字、删除风险或绕过人工审批。只返回JSON。"
    ),
}


def _call_model(agent_id: str, task: dict[str, Any]) -> dict[str, Any]:
    base_url = os.getenv("MODEL_BASE_URL", "").rstrip("/")
    api_key = os.getenv("MODEL_API_KEY", "")
    model = os.getenv("MODEL_NAME", "")
    if not (base_url and api_key and model):
        return {
            "mode": "configuration-required",
            "agent_id": agent_id,
            "error": "MODEL_BASE_URL / MODEL_API_KEY / MODEL_NAME 未配置",
        }
    payload = {
        "model": model,
        "temperature": 0.15,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPTS.get(agent_id, "你是应急调度智能体。返回JSON。")},
            {"role": "user", "content": json.dumps({"agent_id": agent_id, "task": task}, ensure_ascii=False)},
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=45) as client:
        response = client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return {
            "mode": "model",
            "agent_id": agent_id,
            "model": model,
            "result": json.loads(content),
        }


@app.entrypoint
def handler(payload: dict) -> dict:
    agent_id = payload.get("agent_id") or "demand_agent"
    task = payload.get("task")
    if task is None and payload.get("message"):
        try:
            parsed = json.loads(payload["message"])
            task = parsed.get("task", parsed)
            agent_id = parsed.get("agent_id", agent_id)
        except Exception:
            task = {"message": payload["message"]}
    result = _call_model(agent_id, task or {})
    return {
        "response": json.dumps(result, ensure_ascii=False),
        "result": result,
        "session_id": payload.get("session_id", "farmmatch"),
        "agent_id": agent_id,
    }


if __name__ == "__main__":
    handler.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
