from pathlib import Path

import pytest

from app.database import Database
from app.reservations import InventoryReservationError, InventoryReservationService


def _plan(inventory_row: dict) -> dict:
    return {
        "plan_id": "PLAN-APPROVAL-TEST",
        "assignments": [{
            "warehouse_id": inventory_row["warehouse_id"],
            "warehouse_name": inventory_row["warehouse_name"],
            "material_id": inventory_row["material_id"],
            "quantity": inventory_row["quantity"],
        }],
        "metrics": {},
    }


def test_reservation_prevents_double_commit_and_release_restores_inventory(tmp_path: Path):
    db = Database(tmp_path / "reservations.db", database_url="")
    db.initialize()
    inventory = db.inventory_rows(db.latest_snapshot()["id"])
    row = next(item for item in inventory if item["quantity"] > 10 and item["locked_quantity"] == 0)
    plan = _plan(row)
    result = {
        "delivery": {"recommended_plan": {"plan_id": plan["plan_id"]}},
        "rounds": [{"plans": [plan]}],
    }
    for run_id in ("RUN-RESERVE-1", "RUN-RESERVE-2"):
        db.create_run(run_id, "rainstorm-ii", {"scenario_id": "rainstorm-ii"})
        db.update_run(run_id, "awaiting_approval", result)

    service = InventoryReservationService(db)
    first = service.reserve("RUN-RESERVE-1", plan, {"id": 7, "username": "dispatcher", "display_name": "值班员"}, "同意")
    assert first["count"] == 1
    with pytest.raises(InventoryReservationError):
        service.reserve("RUN-RESERVE-2", plan, {"id": 8, "username": "admin"}, "同意")

    stored = db.get_run("RUN-RESERVE-1")
    assert stored["status"] == "approved"
    assert stored["approved_by_name"] == "值班员"
    assert stored["result"]["approval"]["inventory_reservation"]["count"] == 1
    with db.connect() as conn:
        locked = conn.execute("SELECT locked_quantity FROM inventory WHERE id=?", (row["id"],)).fetchone()["locked_quantity"]
    assert locked >= row["quantity"]

    released = service.release("RUN-RESERVE-1")
    assert released["count"] == 1
    second = service.reserve("RUN-RESERVE-2", plan, {"id": 8, "username": "admin"}, "重试审批")
    assert second["count"] == 1
