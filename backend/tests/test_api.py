import time
from pathlib import Path

from fastapi.testclient import TestClient

from app import api as api_module
from app.database import Database
from app.main import app
from app.models import DemandItemInput, EventConfig, RunRequest
from app.operations import OperationsRepository


def test_api_run_flow(tmp_path: Path):
    test_db = Database(tmp_path / "api.db", database_url="")
    test_db.initialize()
    api_module.db = test_db
    api_module.ops = OperationsRepository(test_db)
    client = TestClient(app)

    assert client.get("/api/health").status_code == 200
    materials = client.get("/api/materials").json()
    scenarios = client.get("/api/scenarios").json()
    assert materials and scenarios

    scenario = next(item for item in scenarios if item['id'] == 'rainstorm-ii')
    material_ids = {item["code"]: item["id"] for item in materials}
    payload = {
        "scenario_id": scenario["id"],
        "event": {
            "type": scenario["event_type"],
            "severity": scenario["severity"],
            "location": scenario["location"],
            "affected_population": scenario["affected_population"],
            "closed_route_ids": scenario["closed_routes"],
        },
        "demand_items": [
            {
                "material_id": material_ids[item["material_code"]],
                "quantity": item["quantity"],
                "priority": item["priority"],
                "deadline_hours": item["deadline_hours"],
            }
            for item in scenario["default_demand"]
        ],
        "weights": {"speed": 0.38, "cost": 0.27, "risk": 0.35},
    }
    response = client.post("/api/runs", json=payload)
    assert response.status_code == 200
    run_id = response.json()["run_id"]

    run = None
    for _ in range(40):
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in {"awaiting_approval", "needs_human", "failed"}:
            break
        time.sleep(0.25)

    assert run is not None
    assert run["status"] == "awaiting_approval"
    assert run["result"]["final_verification"]["verdict"] == "pass"
    assert run["result"]["delivery"]["status"] == "ready"
    assert len(run["result"]["rounds"]) == 2
    assert run["result"]["loop_metrics"]["adaptive_categories"] == ["道路通行"]
    assert run["result"]["rework_directives"][0]["policies"]["route_policy"] == "exclude_closed_routes"
    assert run["result"]["orchestration"]["parallel_branch"] is True
    assert len(run["result"]["retrieval"]) >= 2
    assert run["result"]["loop_metrics"]["converged"] is True
    assert run["result"]["loop_metrics"]["score_gain"] > 0

    benchmark = client.get("/api/evaluation/benchmark")
    assert benchmark.status_code == 200
    assert benchmark.json()["metrics"]["reproducible"] is True
    assert benchmark.json()["runtime_evidence"]["fully_cloud"]["samples"] == 0
    assert benchmark.json()["runtime_evidence"]["agents"] == {}

    diagnostics = client.post("/api/evaluation/diagnostics")
    assert diagnostics.status_code == 200
    diagnostic_data = diagnostics.json()
    assert diagnostic_data["status"] == "passed"
    assert [item["concurrency"] for item in diagnostic_data["concurrency"]] == [1, 5, 10, 20]
    assert all(item["success_rate"] == 1.0 for item in diagnostic_data["concurrency"])
    assert all(item["isolation_consistent"] for item in diagnostic_data["concurrency"])
    assert {item["id"] for item in diagnostic_data["fault_injection"]} == {
        "agentarts_timeout", "osrm_unavailable", "invalid_model_json",
        "inventory_shortage", "vehicle_shortage", "road_closure",
    }
    assert all(item["passed"] for item in diagnostic_data["fault_injection"])
    assert diagnostic_data["error_analysis"]["rows"]
    assert diagnostic_data["error_analysis"]["multi_final_unsafe_acceptance_ci95"]["low"] == 0.0

    latest = client.get("/api/evaluation/diagnostics")
    assert latest.status_code == 200
    assert latest.json()["id"] == diagnostic_data["run_id"]
    assert latest.json()["status"] == "completed"

    approve = client.post(f"/api/runs/{run_id}/approve", json={"note": "测试审批"})
    assert approve.status_code == 200
    assert approve.json()["approved"] is True
    assert len(approve.json()["tasks"]) > 0


def test_material_and_inventory_import(tmp_path: Path):
    test_db = Database(tmp_path / "import.db", database_url="")
    test_db.initialize()
    api_module.db = test_db
    api_module.ops = OperationsRepository(test_db)
    client = TestClient(app)

    materials_csv = (
        "物资编码,物资名称,别名,分类,规格,基本单位,包装单位,包装换算,储存条件,保质期天数,是否冷链,是否危险品,单件重量kg,默认优先级\n"
        "MAT-IMPORT-TEST,折叠行军床,行军床,安置保障,1850mm/张,张,箱,10,常温,1825,否,否,7.5,P1\n"
    ).encode("utf-8-sig")
    response = client.post(
        "/api/materials/import",
        files={"file": ("materials.csv", materials_csv, "text/csv")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 1
    assert test_db.find_material("MAT-IMPORT-TEST")

    inventory_csv = (
        "仓库编码,物资编码,数量,单位,批次号,有效期,状态\n"
        "WH-A,MAT-IMPORT-TEST,120,张,BATCH-TEST,2027-12-31,available\n"
    ).encode("utf-8-sig")
    response = client.post(
        "/api/inventory/import",
        files={"file": ("inventory.csv", inventory_csv, "text/csv")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["imported"] == 1
    assert payload["snapshot"]["row_count"] == 1


def test_operational_event_and_gps_ingestion(tmp_path: Path):
    test_db = Database(tmp_path / "operations.db", database_url="")
    test_db.initialize()
    api_module.db = test_db
    api_module.ops = OperationsRepository(test_db)
    client = TestClient(app)

    event_response = client.post("/api/events", json={
        "title": "测试运营事件",
        "event_type": "暴雨洪涝",
        "severity": "II级",
        "location": "武汉市测试区域",
        "affected_population": 1000,
        "description": "运营事件测试",
        "source": "test",
        "closed_routes": [],
    })
    assert event_response.status_code == 200, event_response.text
    assert event_response.json()["id"].startswith("EVT-")

    position_response = client.post("/api/integrations/vehicles/positions", json=[{
        "vehicle_id": "V-A-01",
        "lat": 30.62,
        "lng": 114.31,
        "speed_kmh": 32,
        "source": "gps-test",
        "reported_at": "2026-11-04T18:30:00+08:00",
    }])
    assert position_response.status_code == 200, position_response.text
    assert position_response.json()["updated"] == 1

    vehicles = client.get("/api/vehicles").json()
    vehicle = next(item for item in vehicles if item["id"] == "V-A-01")
    assert vehicle["lat"] == 30.62
    assert vehicle["gps_source"] == "gps-test"

def test_warehouse_and_event_deletion(tmp_path: Path):
    test_db = Database(tmp_path / "deletion.db", database_url="")
    test_db.initialize()
    api_module.db = test_db
    api_module.ops = OperationsRepository(test_db)
    client = TestClient(app)

    created = client.post("/api/warehouses", json={
        "name": "测试应急仓库",
        "region": "武汉市江汉区测试路",
        "lat": 30.585421,
        "lng": 114.28585,
        "capacity": 5000,
        "status": "open",
    })
    assert created.status_code == 200, created.text
    warehouse_id = created.json()["id"]
    assert client.delete(f"/api/warehouses/{warehouse_id}").status_code == 200
    assert all(item["id"] != warehouse_id for item in client.get("/api/network").json()["warehouses"])

    blocked = client.delete("/api/warehouses/WH-A")
    assert blocked.status_code == 409
    forced = client.delete("/api/warehouses/WH-A?force=true")
    assert forced.status_code == 200, forced.text
    network = client.get("/api/network").json()
    assert all(item["id"] != "WH-A" for item in network["warehouses"])
    assert all(item["id"] != "V-A-01" for item in network["vehicles"])

    event = client.post("/api/events", json={
        "title": "待删除测试事件",
        "event_type": "暴雨洪涝",
        "severity": "II级",
        "location": "武汉市测试区域",
        "lat": 30.58,
        "lng": 114.28,
        "affected_population": 100,
        "description": "删除接口测试",
        "source": "test",
        "closed_routes": [],
    })
    assert event.status_code == 200, event.text
    event_id = event.json()["id"]
    deleted = client.delete(f"/api/events/{event_id}")
    assert deleted.status_code == 200, deleted.text
    assert all(item["id"] != event_id for item in client.get("/api/events").json())

def test_inventory_destination_and_vehicle_crud(tmp_path: Path):
    test_db = Database(tmp_path / "operational_crud.db", database_url="")
    test_db.initialize()
    api_module.db = test_db
    api_module.ops = OperationsRepository(test_db)
    client = TestClient(app)

    materials = client.get("/api/materials").json()
    material = next(item for item in materials if item["code"] == "MAT-WATER-001")
    before = client.get("/api/inventory").json()

    created = client.post("/api/inventory/entries", json={
        "warehouse_id": "WH-A",
        "material_id": material["id"],
        "quantity": 25,
        "batch_no": "TEST-INVENTORY-CRUD",
        "expiry_date": "2028-12-31",
        "status": "available",
    })
    assert created.status_code == 200, created.text
    assert created.json()["snapshot"]["row_count"] == before["snapshot"]["row_count"] + 1
    inventory = client.get("/api/inventory").json()
    entry = next(item for item in inventory["items"] if item["batch_no"] == "TEST-INVENTORY-CRUD")
    updated = client.patch(f"/api/inventory/entries/{entry['id']}", json={"quantity": 42, "status": "locked"})
    assert updated.status_code == 200, updated.text
    inventory = client.get("/api/inventory").json()
    entry = next(item for item in inventory["items"] if item["batch_no"] == "TEST-INVENTORY-CRUD")
    assert entry["quantity"] == 42
    assert entry["status"] == "locked"
    deleted_inventory = client.delete(f"/api/inventory/entries/{entry['id']}")
    assert deleted_inventory.status_code == 200, deleted_inventory.text
    assert all(item["batch_no"] != "TEST-INVENTORY-CRUD" for item in client.get("/api/inventory").json()["items"])

    destination = client.post("/api/destinations", json={
        "name": "测试安置点",
        "address": "武汉市江汉区测试路",
        "lat": 30.58,
        "lng": 114.28,
        "population": 300,
        "contact": "测试联系人",
    })
    assert destination.status_code == 200, destination.text
    destination_id = destination.json()["id"]
    assert destination.json()["address"] == "武汉市江汉区测试路"

    vehicle = client.post("/api/vehicles", json={
        "plate_no": "鄂A-TEST01",
        "vehicle_type": "测试运输车",
        "capacity_kg": 3000,
        "temp_zone": "常温",
        "warehouse_id": "WH-A",
        "status": "available",
    })
    assert vehicle.status_code == 200, vehicle.text
    vehicle_id = vehicle.json()["id"]
    assert vehicle.json()["warehouse_id"] == "WH-A"
    assert vehicle.json()["lat"] is not None

    assert client.delete(f"/api/vehicles/{vehicle_id}").status_code == 200
    assert client.delete(f"/api/destinations/{destination_id}?force=true").status_code == 200
    network = client.get("/api/network").json()
    assert all(item["id"] != vehicle_id for item in network["vehicles"])
    assert all(item["id"] != destination_id for item in network["destinations"])

def test_route_generation_and_deletion(tmp_path: Path, monkeypatch):
    test_db = Database(tmp_path / "routes.db", database_url="")
    test_db.initialize()
    api_module.db = test_db
    api_module.ops = OperationsRepository(test_db)
    client = TestClient(app)

    warehouse = client.post("/api/warehouses", json={
        "name": "路线测试仓库",
        "region": "武汉市江汉区测试路",
        "lat": 30.585421,
        "lng": 114.28585,
        "capacity": 1000,
        "status": "open",
    }).json()
    destination = client.post("/api/destinations", json={
        "name": "路线测试安置点",
        "address": "武汉市武昌区测试路",
        "lat": 30.5728,
        "lng": 114.3073,
        "population": 100,
        "contact": "测试",
    }).json()

    async def fake_generate_routes(warehouses, destinations):
        assert len(warehouses) == 1
        assert len(destinations) == 1
        return [{
            "id": f"R-{warehouse['id']}-{destination['id']}-OSRM",
            "warehouse_id": warehouse["id"],
            "destination_id": destination["id"],
            "road_name": "测试 OSRM 路线",
            "distance_km": 4.85,
            "eta_hours": 0.35,
            "cost": 286,
            "temp_zone": "常温",
            "is_primary": True,
            "status": "open",
            "source": "osrm",
        }], []

    monkeypatch.setattr(api_module, "generate_route_candidates", fake_generate_routes)
    generated = client.post("/api/routes/generate", json={
        "warehouse_ids": [warehouse["id"]],
        "destination_ids": [destination["id"]],
        "replace_generated": True,
    })
    assert generated.status_code == 200, generated.text
    assert generated.json()["generated"] == 1
    assert generated.json()["osrm"] == 1
    routes = client.get("/api/network").json()["routes"]
    route = next(item for item in routes if item["road_name"] == "测试 OSRM 路线")
    assert route["source"] == "osrm"
    assert client.delete(f"/api/routes/{route['id']}").status_code == 200
    routes = client.get("/api/network").json()["routes"]
    assert all(item["id"] != route["id"] for item in routes)
def test_run_request_allows_unknown_affected_population():
    request = RunRequest(
        event=EventConfig(affected_population=0),
        demand_items=[DemandItemInput(material_id=1, quantity=100)],
    )
    assert request.event.affected_population == 0
def test_validation_and_http_errors_are_chinese(tmp_path: Path):
    test_db = Database(tmp_path / "chinese-errors.db", database_url="")
    test_db.initialize()
    api_module.db = test_db
    api_module.ops = OperationsRepository(test_db)
    client = TestClient(app)

    invalid = client.post("/api/runs", json={
        "scenario_id": "rainstorm-ii",
        "event": {"affected_population": -1},
        "demand_items": [{"material_id": 1, "quantity": 10}],
    })
    assert invalid.status_code == 422
    detail = invalid.json()["detail"]
    assert "影响人口" in detail
    assert "不能小于 0" in detail
    assert "greater than" not in detail

    missing = client.get("/api/not-a-real-endpoint")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "请求的资源不存在"
def test_route_geometry_is_fetched_and_cached(tmp_path: Path, monkeypatch):
    test_db = Database(tmp_path / "route-geometry.db", database_url="")
    test_db.initialize()
    api_module.db = test_db
    api_module.ops = OperationsRepository(test_db)
    client = TestClient(app)
    route = client.get("/api/network").json()["routes"][0]
    calls = []

    async def fake_geometry(warehouse, destination):
        calls.append((warehouse["id"], destination["id"]))
        return [[warehouse["lat"], warehouse["lng"]], [destination["lat"], destination["lng"]]]

    monkeypatch.setattr(api_module, "fetch_route_geometry", fake_geometry)
    first = client.get(f"/api/routes/geometry?ids={route['id']}")
    assert first.status_code == 200, first.text
    item = first.json()["routes"][0]
    assert item["status"] == "osrm"
    assert len(item["geometry"]) == 2
    assert len(calls) == 1

    second = client.get(f"/api/routes/geometry?ids={route['id']}")
    assert second.status_code == 200
    assert second.json()["routes"][0]["status"] == "cached"
    assert len(calls) == 1
def test_workflow_definition_api(tmp_path: Path):
    test_db = Database(tmp_path / "workflow-api.db", database_url="")
    test_db.initialize()
    api_module.db = test_db
    api_module.ops = OperationsRepository(test_db)
    client = TestClient(app)

    valid = {
        "nodes": [
            {"id": "A01", "type": "agent", "agent_id": "demand_agent", "depends_on": []},
            {"id": "A05", "type": "agent", "agent_id": "risk_extension", "depends_on": ["A01"], "on_error": "continue"},
        ]
    }
    validation = client.post("/api/workflows/validate", json={"definition": valid})
    assert validation.status_code == 200, validation.text
    assert validation.json()["execution_order"] == ["A01", "A05"]

    saved = client.put("/api/workflows/review-v1", json={
        "name": "核验后审查工作流",
        "description": "API 测试",
        "stage": "post_verification",
        "definition": valid,
        "enabled": True,
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    assert client.get("/api/workflows/review-v1").json()["name"] == "核验后审查工作流"

    cycle = client.post("/api/workflows/validate", json={"definition": {
        "nodes": [
            {"id": "A", "type": "agent", "agent_id": "a", "depends_on": ["B"]},
            {"id": "B", "type": "agent", "agent_id": "b", "depends_on": ["A"]},
        ]
    }})
    assert cycle.status_code == 400
    assert "循环依赖" in cycle.json()["detail"]

def test_knowledge_upload_chunk_search_and_delete(tmp_path: Path):
    test_db = Database(tmp_path / "knowledge-api.db", database_url="")
    test_db.initialize()
    api_module.db = test_db
    api_module.ops = OperationsRepository(test_db)
    client = TestClient(app)
    content = ("冷链运输规则\n\n疫苗和急救药品必须使用温控车辆，运输过程中需要记录温度。\n\n暴雨期间主通道封闭时，应优先选择备用通道并增加出车提前量。").encode("utf-8")
    uploaded = client.post("/api/knowledge/documents", files={"file": ("应急运输规则.txt", content, "text/plain")}, data={"category": "运输规则"})
    assert uploaded.status_code == 200, uploaded.text
    document = uploaded.json()
    assert document["chunk_count"] >= 1
    chunks = client.get(f"/api/knowledge/documents/{document['id']}/chunks")
    assert chunks.status_code == 200 and chunks.json()
    search = client.get("/api/knowledge/search?q=暴雨备用通道")
    assert search.status_code == 200 and search.json()
    assert search.json()[0]["source"] == "应急运输规则"
    assert client.delete(f"/api/knowledge/documents/{document['id']}").status_code == 200
