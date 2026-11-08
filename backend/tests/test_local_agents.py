from app.local_agents import invoke_local_registered_agent


def test_local_registered_verifier_returns_structured_review():
    result = invoke_local_registered_agent(
        {
            "id": "verifier_x",
            "name": "离线核验 Agent",
            "role": "verifier-review",
            "capabilities": ["route-verify", "inventory-verify"],
        },
        {
            "verification": {
                "verdict": "rework",
                "score": 42,
                "blockers": [{"code": "ROAD-X", "message": "道路封闭"}],
                "warnings": [],
            },
            "plans": [{"plan_id": "PLAN-1"}],
        },
    )
    assert result["mode"] == "local-rule-agent"
    assert result["parsed"]["verdict"] == "rework"
    assert result["parsed"]["score"] == 42
    assert any("ROAD-X" in risk for risk in result["parsed"]["risks"])


def test_local_registered_risk_agent_is_not_empty_without_endpoint():
    result = invoke_local_registered_agent(
        {
            "id": "risk_x",
            "name": "离线风险审查",
            "role": "risk-review",
            "capabilities": ["night-transport-risk"],
        },
        {"scenario": {"name": "夜间运输"}, "verification": {"verdict": "pass", "score": 100}},
    )
    assert result["parsed"]["verdict"] == "pass"
    assert result["parsed"]["summary"]
    assert result["text"]