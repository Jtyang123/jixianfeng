from __future__ import annotations

import json
from typing import Any


def invoke_local_registered_agent(agent: dict, payload: dict) -> dict:
    """Deterministic offline executor for registered extension agents."""
    agent_id = str(agent.get("id") or agent.get("agent_id") or "extension_agent")
    role = " ".join([
        str(agent.get("role") or ""),
        str(agent.get("name") or ""),
        str(agent.get("description") or ""),
        " ".join(str(item) for item in (agent.get("capabilities") or [])),
    ]).lower()
    verification = payload.get("verification") or {}
    if not isinstance(verification, dict):
        verification = {}
    blockers = verification.get("blockers") or verification.get("blocker_summary") or []
    warnings = verification.get("warnings") or []
    scenario = payload.get("scenario") or {}
    plans = payload.get("plans") or []

    if any(token in role for token in ("verif", "audit", "risk", "核验", "审查", "风险", "复核")):
        verdict = "rework" if blockers else "pass"
        risks = []
        for issue in blockers[:5] + warnings[:3]:
            if isinstance(issue, dict):
                code = issue.get("code") or issue.get("type") or "ISSUE"
                message = issue.get("message") or issue.get("evidence") or issue
                risks.append(f"{code}: {message}")
            else:
                risks.append(str(issue))
        parsed = {
            "verdict": verdict,
            "score": int(verification.get("score") or (70 if blockers else 95)),
            "summary": "离线规则审查完成：发现阻断项。" if blockers else "离线规则审查完成：未发现新增阻断项。",
            "risks": risks,
            "evidence_refs": ["库存快照", "车辆与路线约束", "事件道路封闭清单"],
            "agent_id": agent_id,
            "mode": "local-rule-agent",
        }
        return {"agent_id": agent_id, "mode": "local-rule-agent", "parsed": parsed, "text": json.dumps(parsed, ensure_ascii=False)}

    if any(token in role for token in ("demand", "需求", "人口", "assumption")):
        demand = payload.get("demand") or {}
        items = demand.get("items") or []
        parsed = {
            "summary": f"需求审查完成，共 {len(items)} 条安置点需求。",
            "assumptions": list(demand.get("assumptions") or [])[:5],
            "evidence_refs": list(demand.get("evidence_refs") or [])[:5],
            "risks": ["人工复核优先级与截止时间"] if items else ["需求数据为空"],
            "agent_id": agent_id,
            "mode": "local-rule-agent",
        }
        return {"agent_id": agent_id, "mode": "local-rule-agent", "parsed": parsed, "text": json.dumps(parsed, ensure_ascii=False)}

    if any(token in role for token in ("match", "plan", "dispatch", "匹配", "调度", "方案")):
        recommended = str((plans[0] or {}).get("plan_id") or "") if plans else ""
        parsed = {
            "summary": f"资源匹配复核完成，共 {len(plans)} 套候选方案。",
            "recommended_plan_id": recommended or None,
            "risks": ["AI 推荐不能覆盖未通过核验的方案"] if plans else ["没有可用候选方案"],
            "agent_id": agent_id,
            "mode": "local-rule-agent",
        }
        return {"agent_id": agent_id, "mode": "local-rule-agent", "parsed": parsed, "text": json.dumps(parsed, ensure_ascii=False)}

    parsed = {
        "summary": f"{agent.get('name') or agent_id} 已完成离线审查。",
        "scenario": scenario.get("name") or scenario.get("event_type") or "未命名场景",
        "risks": ["该 Agent 使用通用离线规则，生产前需接入专业模型或知识库"],
        "agent_id": agent_id,
        "mode": "local-rule-agent",
    }
    return {"agent_id": agent_id, "mode": "local-rule-agent", "parsed": parsed, "text": json.dumps(parsed, ensure_ascii=False)}