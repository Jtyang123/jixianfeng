import asyncio
from pathlib import Path

import pytest

from app.a2a import A2ABus, A2AError
from app.database import Database
from app.orchestrator import maybe_a2a_cross_warehouse


def test_a2a_request_reply_and_persistence(tmp_path: Path):
    db = Database(tmp_path / "a2a.db", database_url="")
    db.initialize()
    events = []
    bus = A2ABus(db, "RUN-A2A-1", event_sink=events.append)

    async def scenario():
        request_task = asyncio.create_task(
            bus.request("demand_agent", "matching_agent", {"question": "cross warehouse feasible?"}, timeout=1)
        )
        request_message = await bus.receive("matching_agent")
        reply = await bus.reply(
            request_message, "matching_agent", {"feasible": True, "warehouses": 2}
        )
        response = await request_task
        return request_message, reply, response

    request_message, reply, response = asyncio.run(scenario())
    assert request_message["message_type"] == "request"
    assert reply["message_type"] == "reply"
    assert reply["reply_to"] == request_message["message_id"]
    assert response["correlation_id"] == request_message["correlation_id"]
    assert response["content"]["feasible"] is True
    history = bus.history()
    assert len(history) == 2
    assert {item["message_type"] for item in history} == {"request", "reply"}
    assert [item["message_type"] for item in events] == ["request", "reply"]


def test_a2a_broadcast_reaches_multiple_agents(tmp_path: Path):
    db = Database(tmp_path / "a2a-broadcast.db", database_url="")
    db.initialize()
    bus = A2ABus(db, "RUN-A2A-2")

    async def scenario():
        await bus.receive("matching_agent", timeout=0.001)
        await bus.receive("verifier_agent", timeout=0.001)
        message = await bus.broadcast("demand_agent", {"demand_ready": True})
        matching_message = await bus.receive("matching_agent")
        verifier_message = await bus.receive("verifier_agent")
        return message, matching_message, verifier_message

    message, matching_message, verifier_message = asyncio.run(scenario())
    assert message["message_type"] == "broadcast"
    assert matching_message["message_id"] == message["message_id"]
    assert verifier_message["message_id"] == message["message_id"]


def test_a2a_disabled_bus_raises_for_blackboard_fallback(tmp_path: Path):
    db = Database(tmp_path / "a2a-disabled.db", database_url="")
    db.initialize()
    bus = A2ABus(db, "RUN-A2A-3", enabled=False)
    with pytest.raises(A2AError):
        asyncio.run(bus.send("demand_agent", "matching_agent", "request", {"x": 1}))

def test_a2a_failure_falls_back_to_blackboard(tmp_path: Path):
    db = Database(tmp_path / "a2a-fallback.db", database_url="")
    db.initialize()

    class FailingBus:
        async def send(self, *args, **kwargs):
            raise RuntimeError("A2A unavailable")

    fallback_events = []
    demand_plan = {
        "items": [{"material_id": 1, "quantity": 10}],
    }
    inventory = [
        {"warehouse_id": "WH-A", "material_id": 1, "quantity": 4, "locked_quantity": 0, "status": "available"},
        {"warehouse_id": "WH-B", "material_id": 1, "quantity": 8, "locked_quantity": 0, "status": "available"},
    ]
    result = asyncio.run(
        maybe_a2a_cross_warehouse(
            FailingBus(), demand_plan, inventory, fallback_events.append
        )
    )
    assert result["mode"] == "blackboard-fallback"
    assert result["used"] is True
    assert result["feasible"] is None
    assert fallback_events and "A2A unavailable" in str(fallback_events[0])


def test_reset_demo_clears_a2a_history(tmp_path: Path):
    db = Database(tmp_path / "a2a-reset.db", database_url="")
    db.initialize()
    bus = A2ABus(db, "RUN-A2A-RESET")
    asyncio.run(bus.send("demand_agent", "matching_agent", "broadcast", {"ready": True}))
    assert db.list_a2a_messages("RUN-A2A-RESET")
    db.reset_demo()
    assert db.list_a2a_messages("RUN-A2A-RESET") == []
