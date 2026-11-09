from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from app import api as api_module
from app.database import Database
from app.main import app
from app.operations import OperationsRepository


def test_sse_resumes_after_last_event_id(tmp_path: Path):
    test_db = Database(tmp_path / "sse.db", database_url="")
    test_db.initialize()
    api_module.db = test_db
    api_module.ops = OperationsRepository(test_db)
    test_db.create_run("RUN-SSE", "rainstorm-ii", {"scenario_id": "rainstorm-ii"})
    test_db.add_event("RUN-SSE", "run.started", "orchestrator", "编排控制器", "事件一")
    test_db.add_event("RUN-SSE", "run.completed", "orchestrator", "编排控制器", "事件二")
    test_db.update_run("RUN-SSE", "needs_human", {"ok": False})

    client = TestClient(app)
    with client.stream("GET", "/api/runs/RUN-SSE/events", headers={"Last-Event-ID": "1"}) as response:
        body = response.read().decode("utf-8")
    assert response.status_code == 200
    assert "事件二" in body
    assert "事件一" not in body
    assert "id: 2" in body
def test_concurrent_event_sequences_are_unique(tmp_path: Path):
    test_db = Database(tmp_path / "event-sequence.db", database_url="")
    test_db.initialize()
    test_db.create_run("RUN-SEQ", "rainstorm-ii", {"scenario_id": "rainstorm-ii"})

    def write_event(index):
        return test_db.add_event("RUN-SEQ", "test.event", "tester", "测试", f"事件 {index}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write_event, range(24)))

    events = test_db.events_after("RUN-SEQ")
    sequences = [event["seq"] for event in events]
    assert len(sequences) == 24
    assert sequences == list(range(1, 25))