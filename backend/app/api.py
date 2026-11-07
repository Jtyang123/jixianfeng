from __future__ import annotations

import asyncio
import html
import json
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from .agentarts import AgentArtsBridge
from .auth import AuthService
from .config import AUTH_COOKIE_NAME, AUTH_COOKIE_SECURE, AUTH_REQUIRED, BASE_DIR
from .database import Database
from .evaluation import run_benchmark, run_diagnostics
from .event_import import normalize_event, parse_event_package
from .geocoder import GeocoderError, geocode_address
from .knowledge import catalog as knowledge_catalog
from .knowledge_base import KnowledgeBaseError, KnowledgeRepository, chunk_text, extract_text
from .llm import LLMService
from .models import (
    AgentInvokeRequest, AgentRegisterRequest, AgentVersionCreate, DestinationCreate, InventoryEntryCreate,
    InventoryEntryUpdate, LoginRequest, MaterialCreate,
    MaterialImportResult, OperationalEventCreate, PasswordChange, RouteGenerateRequest, RunRequest,
    TaskStatusUpdate, UserCreate, UserUpdate, VehicleCreate, VehiclePositionInput, WarehouseCreate,
    WorkflowUpsertRequest, WorkflowValidateRequest,
)
from .operations import OperationsRepository
from .registry import AgentRegistryError, AgentRegistryService
from .routing import fetch_route_geometry, generate_route_candidates
from .orchestrator import new_run_id, orchestrate
from .scheduler import platform_manifest
from .tabular import parse_bool, parse_date, parse_number, read_table
from .workflow_engine import WorkflowRepository, WorkflowValidationError, validate_workflow

router = APIRouter(prefix="/api")
db = Database()
ops = OperationsRepository(db)
auth_service = AuthService(db)
registry_service = AgentRegistryService(db)
TEMPLATE_DIR = BASE_DIR / "templates"


class ApprovalRequest(BaseModel):
    note: str = ""


def _require_admin(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要系统管理员权限")
    return user


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    ip = request.client.host if request.client else ""
    attempt_key = auth_service.attempt_key(payload.username, ip)
    retry_after = auth_service.check_login_allowed(attempt_key)
    if retry_after:
        auth_service.audit("login_rate_limited", "POST", "/api/auth/login", 429, None, ip=ip, detail=payload.username[:64])
        raise HTTPException(status_code=429, detail="登录失败次数过多，请稍后再试", headers={"Retry-After": str(retry_after)})
    user = auth_service.authenticate(payload.username, payload.password)
    if not user:
        locked_for = auth_service.record_login_failure(attempt_key)
        auth_service.audit("login_failed", "POST", "/api/auth/login", 429 if locked_for else 401, None, ip=ip, detail=payload.username[:64])
        if locked_for:
            raise HTTPException(status_code=429, detail="登录失败次数过多，账号已临时锁定", headers={"Retry-After": str(locked_for)})
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    auth_service.reset_login_failures(attempt_key)
    token, expires_at, max_age = auth_service.create_session(
        user["id"], ip=ip, user_agent=request.headers.get("user-agent", ""),
    )
    response.set_cookie(
        key=AUTH_COOKIE_NAME, value=token, max_age=max_age, httponly=True,
        secure=AUTH_COOKIE_SECURE, samesite="lax", path="/",
    )
    auth_service.audit("login_success", "POST", "/api/auth/login", 200, user, ip=ip)
    return {"user": user, "auth_required": AUTH_REQUIRED, "expires_at": expires_at}


@router.get("/auth/me")
def auth_me(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return {"user": user, "auth_required": AUTH_REQUIRED}


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(AUTH_COOKIE_NAME, "")
    auth_service.revoke_session(token)
    user = getattr(request.state, "user", None)
    auth_service.audit("logout", "POST", "/api/auth/logout", 200, user,
                       ip=request.client.host if request.client else "")
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/auth/change-password")
def change_password(payload: PasswordChange, request: Request, response: Response) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        auth_service.change_password(user["id"], payload.current_password, payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    auth_service.audit("password_changed", "POST", "/api/auth/change-password", 200, user)
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return {"ok": True, "message": "密码已修改，请重新登录"}


@router.get("/auth/users")
def list_users(request: Request) -> list[dict]:
    _require_admin(request)
    return auth_service.list_users()


@router.post("/auth/users")
def create_user(payload: UserCreate, request: Request) -> dict:
    _require_admin(request)
    try:
        user = auth_service.create_user(
            payload.username, payload.password, role=payload.role, display_name=payload.display_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    auth_service.audit("user_created", "POST", "/api/auth/users", 200,
                       request.state.user, resource_type="user", resource_id=str(user["id"]))
    return user


@router.patch("/auth/users/{user_id}")
def update_user(user_id: int, payload: UserUpdate, request: Request) -> dict:
    current = _require_admin(request)
    if user_id == current["id"] and payload.is_active is False:
        raise HTTPException(status_code=400, detail="不能停用当前登录账号")
    try:
        user = auth_service.update_user(user_id, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    auth_service.audit("user_updated", "PATCH", f"/api/auth/users/{user_id}", 200,
                       current, resource_type="user", resource_id=str(user_id))
    return user


@router.delete("/auth/users/{user_id}")
def delete_user(user_id: int, request: Request) -> dict:
    current = _require_admin(request)
    if user_id == current["id"]:
        raise HTTPException(status_code=400, detail="不能删除当前登录账号")
    try:
        user = auth_service.delete_user(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    auth_service.audit("user_deleted", "DELETE", f"/api/auth/users/{user_id}", 200,
                       current, resource_type="user", resource_id=str(user_id))
    return {"deleted": True, "user": user}

@router.get("/auth/audit-logs")
def audit_logs(request: Request, limit: int = 200) -> list[dict]:
    _require_admin(request)
    return auth_service.list_audit_logs(limit=max(1, min(limit, 500)))


@router.get("/agents")
def list_agents(request: Request) -> list[dict]:
    return registry_service.list_agents()


@router.get("/agents/discover")
def discover_agents(request: Request, role: str | None = None) -> list[dict]:
    return registry_service.discover(role=role)


@router.post("/agents")
def register_agent(payload: AgentRegisterRequest, request: Request) -> dict:
    _require_admin(request)
    try:
        return registry_service.register_agent(payload.model_dump())
    except AgentRegistryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/agents/{agent_id}/versions")
def add_agent_version(agent_id: str, payload: AgentVersionCreate, request: Request) -> dict:
    _require_admin(request)
    try:
        return registry_service.add_version(agent_id, payload.model_dump())
    except AgentRegistryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/agents/{agent_id}/versions/{version}/activate")
def activate_agent_version(agent_id: str, version: str, request: Request) -> dict:
    _require_admin(request)
    try:
        return registry_service.activate_version(agent_id, version)
    except AgentRegistryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workflows")
def list_workflows(stage: str | None = None, enabled_only: bool = False) -> list[dict]:
    return WorkflowRepository(db).list_workflows(stage=stage, enabled_only=enabled_only)


@router.post("/workflows/validate")
def validate_workflow_definition(payload: WorkflowValidateRequest) -> dict:
    try:
        definition = validate_workflow(payload.definition)
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"valid": True, "execution_order": definition["execution_order"], "definition": definition}


@router.get("/workflow-runs/{run_id}")
def get_workflow_run(run_id: str) -> dict:
    run = WorkflowRepository(db).get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="工作流运行记录不存在")
    return run

@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str) -> dict:
    workflow = WorkflowRepository(db).get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return workflow


@router.put("/workflows/{workflow_id}")
def save_workflow(workflow_id: str, payload: WorkflowUpsertRequest, request: Request) -> dict:
    current = _require_admin(request)
    try:
        workflow = WorkflowRepository(db).save_workflow(workflow_id, payload.model_dump())
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    auth_service.audit("workflow_saved", "PUT", f"/api/workflows/{workflow_id}", 200,
                       current, resource_type="workflow", resource_id=workflow_id)
    return workflow


@router.delete("/workflows/{workflow_id}")
def delete_workflow(workflow_id: str, request: Request) -> dict:
    current = _require_admin(request)
    if not WorkflowRepository(db).delete_workflow(workflow_id):
        raise HTTPException(status_code=404, detail="工作流不存在")
    auth_service.audit("workflow_deleted", "DELETE", f"/api/workflows/{workflow_id}", 200,
                       current, resource_type="workflow", resource_id=workflow_id)
    return {"deleted": True}

@router.post("/agents/{agent_id}/invoke")
async def invoke_registered_agent(agent_id: str, payload: AgentInvokeRequest, request: Request) -> dict:
    _require_admin(request)
    endpoints = registry_service.active_endpoints()
    endpoint = endpoints.get(agent_id, "")
    if not endpoint:
        raise HTTPException(status_code=400, detail="该 Agent 没有已激活的调用端点")
    bridge = AgentArtsBridge()
    bridge.override_endpoint(agent_id, endpoint)
    session_id = payload.session_id or f"registry-test-{uuid4().hex[:10]}"
    try:
        response = await bridge.invoke(agent_id, payload.task, session_id=session_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Agent 调用失败：{str(exc)[:240]}") from exc
    auth_service.audit("agent_invoked", "POST", f"/api/agents/{agent_id}/invoke", 200,
                       request.state.user, resource_type="agent", resource_id=agent_id)
    return {"agent_id": agent_id, "endpoint": endpoint, "session_id": session_id, "response": response}


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "FarmMatch Emergency Dispatch", "time": datetime.now().isoformat()}


@router.get("/system/status")
def system_status() -> dict:
    snapshot = db.latest_snapshot()
    return {
        "database": {
            "engine": db.engine,
            "path": str(db.path) if db.engine == "sqlite" else None,
            "database_url_configured": bool(db.database_url),
            "exists": db.path.exists() if db.engine == "sqlite" else True,
            "size_bytes": db.path.stat().st_size if db.engine == "sqlite" and db.path.exists() else None,
            "latest_snapshot": snapshot,
            "dashboard": db.dashboard(),
        },
        "agentarts": AgentArtsBridge().status,
        "llm": LLMService().status,
        "readiness": ops.readiness(),
        "map": {
            "provider": "Leaflet + AMap-compatible tiles (no key; OSM configurable)",
            "runtime": "Leaflet",
            "business_points": "simulated-drill-data",
        },
    }


@router.get("/agentarts/check")
async def agentarts_check() -> dict:
    return await AgentArtsBridge().ping()


@router.get("/platform/manifest")
def manifest() -> dict:
    data = platform_manifest()
    data["knowledge"]["documents"] = knowledge_catalog()
    data["llm"] = LLMService().status
    data["agentarts"] = AgentArtsBridge().status
    return data


@router.post("/evaluation/diagnostics")
def evaluation_diagnostics() -> dict:
    return run_diagnostics(db)


@router.get("/evaluation/diagnostics")
def latest_evaluation_diagnostics() -> dict:
    rows = db.list_evaluation_runs(limit=1)
    return rows[0] if rows else {"status": "empty", "message": "暂无诊断评估记录"}


@router.get("/evaluation/benchmark")
def benchmark() -> dict:
    data = run_benchmark(db)
    runtime_registry = AgentRegistryService(db)
    all_runs = runtime_registry.run_performance_metrics()
    cloud_runs = runtime_registry.fully_cloud_run_metrics()
    data["runtime_evidence"] = {
        "agents": runtime_registry.runtime_metrics(run_ids=cloud_runs["run_ids"]),
        "runs": all_runs,
        "fully_cloud": cloud_runs,
    }
    return data


def require_integration_key(x_integration_key: str | None = Header(default=None, alias="X-Integration-Key")) -> None:
    expected = os.getenv("INTEGRATION_API_KEY", "").strip()
    if expected and x_integration_key != expected:
        raise HTTPException(status_code=401, detail="接入密钥无效")
    if AUTH_REQUIRED and not expected:
        raise HTTPException(status_code=503, detail="已启用登录鉴权，但未配置 INTEGRATION_API_KEY")


@router.get("/readiness")
def readiness() -> dict:
    return ops.readiness()


@router.get("/events")
def list_events(limit: int = 50) -> list[dict]:
    return ops.list_events(limit=limit)


@router.get("/geocode")
async def geocode(address: str) -> dict:
    try:
        return await geocode_address(address)
    except GeocoderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/events/template")
def operational_event_template() -> FileResponse:
    return FileResponse(TEMPLATE_DIR / "operational_event_template.xlsx", filename="真实应急场景导入模板.xlsx", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.post("/events/import")
async def import_operational_event(file: UploadFile = File(...)) -> dict:
    payload = await file.read()
    try:
        raw_event, raw_demand = parse_event_package(file.filename or "event.xlsx", payload)
        event_data, demand_items = normalize_event(raw_event, raw_demand)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"事件文件解析失败：{exc}") from exc
    if not event_data.get("title") or not event_data.get("location"):
        raise HTTPException(status_code=400, detail="事件标题和影响区域不能为空")
    event_data["id"] = f"EVT-{uuid4().hex[:12].upper()}"
    event_data["demand_items"] = demand_items
    event = ops.add_event(event_data)
    return event


@router.post("/events")
def create_event(payload: OperationalEventCreate) -> dict:
    data = payload.model_dump()
    data["id"] = f"EVT-{uuid4().hex[:12].upper()}"
    data["status"] = "confirmed"
    data["confirmed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return ops.add_event(data)


@router.delete("/events/{event_id}")
def delete_operational_event(event_id: str, force: bool = False) -> dict:
    result = ops.delete_event(event_id, force=force)
    if result is None:
        raise HTTPException(status_code=404, detail="事件不存在或已被删除")
    if not result["deleted"]:
        refs = result["references"]
        raise HTTPException(
            status_code=409,
            detail=f"该事件已有 {refs['runs']} 条调度记录，确认删除后会同时清理相关运行历史",
        )
    return result


@router.post("/integrations/events")
def integration_event(payload: OperationalEventCreate, x_integration_key: str | None = Header(default=None, alias="X-Integration-Key")) -> dict:
    require_integration_key(x_integration_key)
    data = payload.model_dump()
    data["id"] = f"EVT-{uuid4().hex[:12].upper()}"
    data["source"] = payload.source or "webhook"
    data["status"] = "confirmed"
    data["confirmed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return ops.add_event(data)


@router.get("/vehicles")
def vehicles() -> list[dict]:
    return db.vehicle_rows()


@router.get("/vehicles/{vehicle_id}/positions")
def vehicle_positions(vehicle_id: str, limit: int = 100) -> list[dict]:
    return ops.vehicle_history(vehicle_id, limit=limit)


@router.post("/integrations/vehicles/positions")
def ingest_vehicle_positions(
    positions: list[VehiclePositionInput],
    x_integration_key: str | None = Header(default=None, alias="X-Integration-Key"),
) -> dict:
    require_integration_key(x_integration_key)
    known = {vehicle["id"] for vehicle in db.vehicle_rows()}
    updated = []
    errors = []
    for position in positions:
        if position.vehicle_id not in known:
            errors.append(f"车辆不存在：{position.vehicle_id}")
            continue
        updated.append(ops.ingest_vehicle_position(position.model_dump()))
    return {"updated": len(updated), "vehicles": updated, "errors": errors}


@router.get("/tasks")
def tasks(run_id: str | None = None) -> list[dict]:
    return ops.list_tasks(run_id=run_id)


@router.patch("/tasks/{task_id}")
def update_task(task_id: int, payload: TaskStatusUpdate) -> dict:
    task = ops.update_task(task_id, payload.status, payload.assignee)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/dashboard")
def dashboard() -> dict:
    return db.dashboard()


@router.get("/knowledge/documents")
def knowledge_documents() -> list[dict]:
    return KnowledgeRepository(db).list_documents()


@router.post("/knowledge/documents")
async def upload_knowledge_document(
    request: Request,
    file: UploadFile = File(...),
    category: str = Form("其他"),
) -> dict:
    current = _require_admin(request)
    payload = await file.read()
    if len(payload) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="知识文档不能超过 20 MB")
    try:
        text = extract_text(file.filename or "knowledge.txt", payload)
        chunks = chunk_text(text)
        document = KnowledgeRepository(db).insert_document(
            Path(file.filename or "知识文档").stem, category, "upload", chunks,
        )
    except KnowledgeBaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    auth_service.audit("knowledge_uploaded", "POST", "/api/knowledge/documents", 200,
                       current, resource_type="knowledge_document", resource_id=str(document["id"]))
    return document


@router.get("/knowledge/documents/{document_id}/chunks")
def knowledge_chunks(document_id: int) -> list[dict]:
    return KnowledgeRepository(db).list_chunks(document_id)


@router.delete("/knowledge/documents/{document_id}")
def delete_knowledge_document(document_id: int, request: Request) -> dict:
    current = _require_admin(request)
    if not KnowledgeRepository(db).delete_document(document_id):
        raise HTTPException(status_code=404, detail="知识文档不存在")
    auth_service.audit("knowledge_deleted", "DELETE", f"/api/knowledge/documents/{document_id}", 200,
                       current, resource_type="knowledge_document", resource_id=str(document_id))
    return {"deleted": True}


@router.get("/knowledge/search")
def search_knowledge(q: str, top_k: int = 4) -> list[dict]:
    return KnowledgeRepository(db).search(q, top_k=max(1, min(top_k, 20)))

@router.get("/materials")
def materials(search: str = "", category: str = "") -> list[dict]:
    return db.list_materials(search=search, category=category)


@router.get("/materials/categories")
def material_categories() -> list[str]:
    return sorted({item["category"] for item in db.list_materials()})


@router.post("/materials")
def create_material(payload: MaterialCreate) -> dict:
    if db.get_material_by_code(payload.code):
        raise HTTPException(status_code=409, detail="物资编码已存在")
    return db.create_material(payload.model_dump())


@router.get("/materials/template")
def material_template() -> FileResponse:
    return FileResponse(TEMPLATE_DIR / "materials_template.csv", filename="物资库导入模板.csv", media_type="text/csv")


@router.post("/materials/import", response_model=MaterialImportResult)
async def import_materials(file: UploadFile = File(...)) -> MaterialImportResult:
    payload = await file.read()
    try:
        rows = read_table(file.filename or "materials.csv", payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    imported = 0
    updated = 0
    errors: list[str] = []
    warnings: list[str] = []
    for index, row in enumerate(rows, start=2):
        name = str(row.get("name") or "").strip()
        if not name:
            errors.append(f"第 {index} 行缺少物资名称")
            continue
        code = str(row.get("code") or "").strip() or f"MAT-IMP-{uuid4().hex[:8].upper()}"
        data = {
            "code": code,
            "name": name,
            "aliases": str(row.get("aliases") or ""),
            "category": str(row.get("category") or "其他"),
            "specification": str(row.get("specification") or ""),
            "base_unit": str(row.get("base_unit") or row.get("unit") or "件"),
            "package_unit": str(row.get("package_unit") or ""),
            "conversion_rate": parse_number(row.get("conversion_rate"), 1) or 1,
            "storage_type": str(row.get("storage_type") or "常温"),
            "shelf_life_days": int(parse_number(row.get("shelf_life_days"), 0) or 0) or None,
            "is_cold_chain": parse_bool(row.get("is_cold_chain")),
            "is_hazardous": parse_bool(row.get("is_hazardous")),
            "unit_weight_kg": parse_number(row.get("unit_weight_kg"), 0.5) or 0.5,
            "default_priority": str(row.get("default_priority") or "P1").upper(),
            "source": "import",
            "status": "active",
        }
        if not data["shelf_life_days"]:
            warnings.append(f"第 {index} 行缺少保质期，已留空")
        _, was_update = db.upsert_imported_material(data)
        if was_update:
            updated += 1
        else:
            imported += 1
    return MaterialImportResult(imported=imported, updated=updated, errors=errors, warnings=warnings)


@router.get("/inventory")
def inventory() -> dict:
    snapshot = db.latest_snapshot()
    return {
        "snapshot": snapshot,
        "summary": db.inventory_summary(),
        "items": db.inventory_rows(snapshot["id"] if snapshot else None),
        "warehouses": db.warehouse_rows(),
    }


def _mutable_inventory_rows(snapshot_id: int | None) -> list[dict]:
    if snapshot_id is None:
        return []
    fields = ("warehouse_id", "material_id", "quantity", "unit", "batch_no", "expiry_date", "status")
    return [{field: row.get(field) for field in fields} for row in db.inventory_rows(snapshot_id)]


@router.post("/inventory/entries")
def create_inventory_entry(payload: InventoryEntryCreate) -> dict:
    warehouse = ops.get_warehouse(payload.warehouse_id)
    if not warehouse:
        raise HTTPException(status_code=404, detail="仓库不存在")
    material = db.get_material(payload.material_id)
    if not material:
        raise HTTPException(status_code=404, detail="物资不存在")
    snapshot = db.latest_snapshot()
    rows = _mutable_inventory_rows(snapshot["id"] if snapshot else None)
    rows.append({
        "warehouse_id": payload.warehouse_id,
        "material_id": payload.material_id,
        "quantity": payload.quantity,
        "unit": payload.unit or material["base_unit"],
        "batch_no": payload.batch_no,
        "expiry_date": payload.expiry_date,
        "status": payload.status,
    })
    created = db.create_inventory_snapshot(rows, f"手工入库-{datetime.now():%m%d-%H%M%S}", source="manual")
    return {"snapshot": created, "imported": 1, "errors": [], "warnings": []}


@router.patch("/inventory/entries/{entry_id}")
def update_inventory_entry(entry_id: int, payload: InventoryEntryUpdate) -> dict:
    snapshot = db.latest_snapshot()
    if not snapshot:
        raise HTTPException(status_code=404, detail="库存记录不存在")
    rows = _mutable_inventory_rows(snapshot["id"])
    current = db.inventory_rows(snapshot["id"])
    row_index = next((index for index, item in enumerate(current) if item["id"] == entry_id), None)
    if row_index is None:
        raise HTTPException(status_code=404, detail="库存记录不存在或不属于最新快照")
    target = rows[row_index]
    updates = payload.model_dump(exclude_unset=True)
    for field in ("quantity", "unit", "batch_no", "status"):
        if field in updates and updates[field] is not None:
            target[field] = updates[field]
    if "expiry_date" in updates:
        target["expiry_date"] = updates["expiry_date"]
    created = db.create_inventory_snapshot(rows, f"库存调整-{datetime.now():%m%d-%H%M%S}", source="manual")
    return {"snapshot": created, "updated": 1, "errors": [], "warnings": []}


@router.delete("/inventory/entries/{entry_id}")
def delete_inventory_entry(entry_id: int) -> dict:
    snapshot = db.latest_snapshot()
    if not snapshot:
        raise HTTPException(status_code=404, detail="库存记录不存在")
    rows = _mutable_inventory_rows(snapshot["id"])
    current = db.inventory_rows(snapshot["id"])
    row = next((item for item in current if item["id"] == entry_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="库存记录不存在或不属于最新快照")
    remaining = [item for index, item in enumerate(rows) if current[index]["id"] != entry_id]
    created = db.create_inventory_snapshot(remaining, f"库存删除-{datetime.now():%m%d-%H%M%S}", source="manual")
    return {"snapshot": created, "deleted": 1, "errors": [], "warnings": []}


@router.get("/inventory/template")
def inventory_template() -> FileResponse:
    return FileResponse(TEMPLATE_DIR / "inventory_template.csv", filename="库存导入模板.csv", media_type="text/csv")


@router.post("/inventory/import")
async def import_inventory(file: UploadFile = File(...)) -> dict:
    payload = await file.read()
    try:
        rows = read_table(file.filename or "inventory.csv", payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    warehouses = {item["id"]: item for item in db.warehouse_rows()}
    warehouse_names = {item["name"]: item for item in warehouses.values()}
    normalized: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []
    for index, row in enumerate(rows, start=2):
        warehouse_value = str(row.get("warehouse_code") or row.get("warehouse_name") or "").strip()
        warehouse = warehouses.get(warehouse_value) or warehouse_names.get(warehouse_value)
        if not warehouse:
            errors.append(f"第 {index} 行仓库不存在：{warehouse_value or '空'}")
            continue
        material_value = str(row.get("code") or row.get("name") or "").strip()
        material = db.find_material(material_value) if material_value else None
        if not material:
            errors.append(f"第 {index} 行物资未在物资库中：{material_value or '空'}")
            continue
        quantity = parse_number(row.get("quantity"))
        if quantity is None or quantity <= 0:
            errors.append(f"第 {index} 行数量无效")
            continue
        expiry = parse_date(row.get("expiry_date"))
        if not expiry:
            warnings.append(f"第 {index} 行未提供有效期")
        normalized.append({
            "warehouse_id": warehouse["id"],
            "material_id": material["id"],
            "quantity": quantity,
            "unit": str(row.get("unit") or row.get("base_unit") or material["base_unit"]),
            "batch_no": str(row.get("batch_no") or ""),
            "expiry_date": expiry,
            "status": str(row.get("status") or "available"),
        })
    if not normalized:
        raise HTTPException(status_code=400, detail={"message": "没有可导入的有效库存记录", "errors": errors})
    label = f"{Path(file.filename or '库存上传').stem}-{datetime.now():%m%d-%H%M}"
    snapshot = db.create_inventory_snapshot(normalized, label)
    return {"snapshot": snapshot, "imported": len(normalized), "errors": errors, "warnings": warnings}


async def _read_upload(file: UploadFile) -> list[dict]:
    payload = await file.read()
    try:
        return read_table(file.filename or "upload.csv", payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/warehouses/template")
def warehouse_template() -> FileResponse:
    return FileResponse(TEMPLATE_DIR / "warehouses_template.csv", filename="仓库导入模板.csv", media_type="text/csv")


@router.post("/warehouses")
def create_warehouse(payload: WarehouseCreate) -> dict:
    warehouse_id = f"WH-{uuid4().hex[:8].upper()}"
    ops.upsert_warehouse({
        "id": warehouse_id,
        "name": payload.name.strip(),
        "region": payload.region.strip(),
        "lat": payload.lat,
        "lng": payload.lng,
        "capacity": payload.capacity,
        "status": payload.status,
    })
    return ops.get_warehouse(warehouse_id)


@router.delete("/warehouses/{warehouse_id}")
def delete_warehouse(warehouse_id: str, force: bool = False) -> dict:
    result = ops.delete_warehouse(warehouse_id, force=force)
    if result is None:
        raise HTTPException(status_code=404, detail="仓库不存在或已被删除")
    if not result["deleted"]:
        refs = result["references"]
        raise HTTPException(
            status_code=409,
            detail=f"该仓库仍关联 {refs['routes']} 条路线、{refs['vehicles']} 辆车辆和 {refs['inventory']} 条库存记录",
        )
    return result


@router.post("/warehouses/import")
async def import_warehouses(file: UploadFile = File(...)) -> dict:
    rows = await _read_upload(file)
    imported = 0
    errors = []
    for index, row in enumerate(rows, start=2):
        name = str(row.get("name") or "").strip()
        if not name:
            errors.append(f"第 {index} 行缺少仓库名称")
            continue
        wh_id = str(row.get("id") or row.get("warehouse_code") or f"WH-{uuid4().hex[:8].upper()}").strip()
        lat = parse_number(row.get("lat"))
        lng = parse_number(row.get("lng"))
        if lat is None or lng is None:
            errors.append(f"第 {index} 行经纬度无效")
            continue
        ops.upsert_warehouse({
            "id": wh_id, "name": name, "region": str(row.get("region") or ""),
            "lat": lat, "lng": lng, "capacity": parse_number(row.get("capacity"), 0),
            "status": str(row.get("status") or "open"),
        })
        imported += 1
    return {"imported": imported, "errors": errors}


@router.get("/destinations/template")
def destination_template() -> FileResponse:
    return FileResponse(TEMPLATE_DIR / "destinations_template.csv", filename="安置点导入模板.csv", media_type="text/csv")


@router.post("/destinations")
def create_destination(payload: DestinationCreate) -> dict:
    destination_id = f"DST-{uuid4().hex[:8].upper()}"
    ops.upsert_destination({
        "id": destination_id,
        "name": payload.name.strip(),
        "address": payload.address.strip(),
        "lat": payload.lat,
        "lng": payload.lng,
        "population": payload.population,
        "contact": payload.contact.strip(),
    })
    return ops.get_destination(destination_id)


@router.delete("/destinations/{destination_id}")
def delete_destination(destination_id: str, force: bool = False) -> dict:
    result = ops.delete_destination(destination_id, force=force)
    if result is None:
        raise HTTPException(status_code=404, detail="安置点不存在或已被删除")
    if not result["deleted"]:
        refs = result["references"]
        raise HTTPException(status_code=409, detail=f"该安置点仍关联 {refs['routes']} 条路线")
    return result


@router.post("/destinations/import")
async def import_destinations(file: UploadFile = File(...)) -> dict:
    rows = await _read_upload(file)
    imported = 0
    errors = []
    for index, row in enumerate(rows, start=2):
        name = str(row.get("name") or "").strip()
        if not name:
            errors.append(f"第 {index} 行缺少安置点名称")
            continue
        lat = parse_number(row.get("lat"))
        lng = parse_number(row.get("lng"))
        if lat is None or lng is None:
            errors.append(f"第 {index} 行经纬度无效")
            continue
        dst_id = str(row.get("destination_id") or row.get("id") or f"DST-{uuid4().hex[:8].upper()}").strip()
        ops.upsert_destination({
            "id": dst_id, "name": name,
            "address": str(row.get("address") or row.get("region") or ""),
            "lat": lat, "lng": lng,
            "population": int(parse_number(row.get("population"), 0) or 0),
            "contact": str(row.get("contact") or ""),
        })
        imported += 1
    return {"imported": imported, "errors": errors}


@router.get("/vehicles/template")
def vehicle_template() -> FileResponse:
    return FileResponse(TEMPLATE_DIR / "vehicles_template.csv", filename="车辆导入模板.csv", media_type="text/csv")


@router.post("/vehicles")
def create_vehicle(payload: VehicleCreate) -> dict:
    warehouse = ops.get_warehouse(payload.warehouse_id)
    if not warehouse:
        raise HTTPException(status_code=404, detail="所属仓库不存在")
    vehicle_id = f"V-{uuid4().hex[:8].upper()}"
    lat = payload.lat if payload.lat is not None else warehouse.get("lat")
    lng = payload.lng if payload.lng is not None else warehouse.get("lng")
    ops.upsert_vehicle({
        "id": vehicle_id,
        "plate_no": payload.plate_no.strip(),
        "vehicle_type": payload.vehicle_type.strip(),
        "capacity_kg": payload.capacity_kg,
        "temp_zone": payload.temp_zone.strip() or "常温",
        "warehouse_id": payload.warehouse_id,
        "status": payload.status,
        "lat": lat,
        "lng": lng,
        "last_position_at": None,
        "gps_source": "warehouse",
    })
    return ops.get_vehicle(vehicle_id)


@router.delete("/vehicles/{vehicle_id}")
def delete_vehicle(vehicle_id: str) -> dict:
    result = ops.delete_vehicle(vehicle_id)
    if result is None:
        raise HTTPException(status_code=404, detail="车辆不存在或已被删除")
    return result


@router.post("/vehicles/import")
async def import_vehicles(file: UploadFile = File(...)) -> dict:
    rows = await _read_upload(file)
    warehouse_ids = {item["id"] for item in db.warehouse_rows()}
    imported = 0
    errors = []
    for index, row in enumerate(rows, start=2):
        vehicle_id = str(row.get("vehicle_id") or row.get("id") or f"V-{uuid4().hex[:8].upper()}").strip()
        warehouse_id = str(row.get("warehouse_code") or row.get("warehouse_id") or "").strip()
        if warehouse_id not in warehouse_ids:
            errors.append(f"第 {index} 行仓库不存在：{warehouse_id or '空'}")
            continue
        plate = str(row.get("plate_no") or vehicle_id).strip()
        vehicle_type = str(row.get("vehicle_type") or "应急运输车辆").strip()
        capacity = parse_number(row.get("capacity_kg"), 0) or 0
        if capacity <= 0:
            errors.append(f"第 {index} 行载重无效")
            continue
        ops.upsert_vehicle({
            "id": vehicle_id, "plate_no": plate, "vehicle_type": vehicle_type,
            "capacity_kg": capacity, "temp_zone": str(row.get("temp_zone") or "常温"),
            "warehouse_id": warehouse_id, "status": str(row.get("status") or "available"),
            "lat": parse_number(row.get("lat")), "lng": parse_number(row.get("lng")),
            "last_position_at": None, "gps_source": str(row.get("gps_source") or "manual"),
        })
        imported += 1
    return {"imported": imported, "errors": errors}


@router.get("/routes/template")
def route_template() -> FileResponse:
    return FileResponse(TEMPLATE_DIR / "routes_template.csv", filename="路线导入模板.csv", media_type="text/csv")


@router.post("/routes/generate")
async def generate_routes(payload: RouteGenerateRequest) -> dict:
    warehouse_filter = set(payload.warehouse_ids)
    destination_filter = set(payload.destination_ids)
    warehouses = [
        item for item in db.warehouse_rows()
        if item.get("lat") is not None and item.get("lng") is not None
        and item.get("status", "open") == "open"
        and (not warehouse_filter or item["id"] in warehouse_filter)
    ]
    destinations = [
        item for item in db.destination_rows()
        if item.get("lat") is not None and item.get("lng") is not None
        and (not destination_filter or item["id"] in destination_filter)
    ]
    if not warehouses or not destinations:
        raise HTTPException(status_code=400, detail="至少需要一个已定位仓库和一个已定位安置点")
    routes, warnings = await generate_route_candidates(warehouses, destinations)
    if payload.replace_generated:
        warehouse_ids = {item["id"] for item in warehouses}
        destination_ids = {item["id"] for item in destinations}
        for route in db.route_rows():
            if route.get("source") in {"osrm", "fallback"} and route["warehouse_id"] in warehouse_ids and route["destination_id"] in destination_ids:
                ops.delete_route(route["id"])
    for route in routes:
        ops.upsert_route(route)
    return {
        "generated": len(routes),
        "osrm": sum(1 for item in routes if item["source"] == "osrm"),
        "fallback": sum(1 for item in routes if item["source"] == "fallback"),
        "warnings": warnings,
        "routes": routes,
    }


@router.get("/routes/geometry")
async def route_geometries(ids: str = "") -> dict:
    requested = [item.strip() for item in ids.split(",") if item.strip()][:100]
    if not requested:
        return {"routes": []}
    route_map = {item["id"]: item for item in db.route_rows()}
    warehouse_map = {item["id"]: item for item in db.warehouse_rows()}
    destination_map = {item["id"]: item for item in db.destination_rows()}
    semaphore = asyncio.Semaphore(4)
    results = []

    async def resolve(route_id: str) -> dict:
        route = route_map.get(route_id)
        if not route:
            return {"id": route_id, "geometry": [], "status": "missing"}
        geometry = route.get("geometry") or []
        if geometry:
            return {"id": route_id, "geometry": geometry, "status": "cached"}
        warehouse = warehouse_map.get(route["warehouse_id"])
        destination = destination_map.get(route["destination_id"])
        if not warehouse or not destination:
            return {"id": route_id, "geometry": [], "status": "missing-points"}
        async with semaphore:
            try:
                geometry = await fetch_route_geometry(warehouse, destination)
                if geometry:
                    ops.update_route_geometry(route_id, geometry)
                    return {"id": route_id, "geometry": geometry, "status": "osrm"}
            except Exception:
                pass
        return {"id": route_id, "geometry": [], "status": "unavailable"}

    results = await asyncio.gather(*(resolve(route_id) for route_id in requested))
    return {"routes": results}

@router.delete("/routes/{route_id}")
def delete_route(route_id: str) -> dict:
    result = ops.delete_route(route_id)
    if result is None:
        raise HTTPException(status_code=404, detail="路线不存在或已被删除")
    return result


@router.post("/routes/import")
async def import_routes(file: UploadFile = File(...)) -> dict:
    rows = await _read_upload(file)
    warehouse_ids = {item["id"] for item in db.warehouse_rows()}
    destination_ids = {item["id"] for item in db.destination_rows()}
    imported = 0
    errors = []
    for index, row in enumerate(rows, start=2):
        route_id = str(row.get("id") or f"R-{uuid4().hex[:8].upper()}").strip()
        wh_id = str(row.get("warehouse_code") or row.get("warehouse_id") or "").strip()
        dst_id = str(row.get("destination_id") or "").strip()
        if wh_id not in warehouse_ids or dst_id not in destination_ids:
            errors.append(f"第 {index} 行仓库或安置点不存在")
            continue
        distance = parse_number(row.get("distance_km"), 0) or 0
        eta = parse_number(row.get("eta_hours"), 0) or 0
        cost = parse_number(row.get("cost"), 0) or 0
        if distance <= 0 or eta <= 0:
            errors.append(f"第 {index} 行距离或 ETA 无效")
            continue
        ops.upsert_route({
            "id": route_id, "warehouse_id": wh_id, "destination_id": dst_id,
            "road_name": str(row.get("road_name") or f"{wh_id}→{dst_id}"),
            "distance_km": distance, "eta_hours": eta, "cost": cost,
            "temp_zone": str(row.get("temp_zone") or "常温"),
            "is_primary": parse_bool(row.get("is_primary")),
            "status": str(row.get("status") or "open"),
        })
        imported += 1
    return {"imported": imported, "errors": errors}


@router.get("/network")
def network() -> dict:
    return {
        "warehouses": db.warehouse_rows(),
        "destinations": db.destination_rows(),
        "routes": db.route_rows(),
        "vehicles": db.vehicle_rows(),
    }


@router.get("/scenarios")
def scenarios() -> list[dict]:
    return db.scenario_rows()

@router.post("/runs")
def start_run(payload: RunRequest, background_tasks: BackgroundTasks) -> dict:
    event_record = ops.get_event(payload.event_id) if payload.event_id else None
    if event_record:
        scenario = ops.ensure_operational_scenario(event_record)
    else:
        scenario = db.get_scenario(payload.scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="推演场景或运营事件不存在")
    material_ids = {item["id"] for item in db.list_materials()}
    invalid = [item.material_id for item in payload.demand_items if item.material_id not in material_ids]
    if invalid:
        raise HTTPException(status_code=400, detail=f"物资不存在：{invalid}")
    request_payload = payload.model_dump()
    request_payload["scenario_id"] = scenario["id"]
    if event_record:
        request_payload["event"] = {
            "type": event_record["event_type"],
            "severity": event_record["severity"],
            "location": event_record["location"],
            "affected_population": event_record["affected_population"],
            "closed_route_ids": event_record["closed_routes"],
            "note": event_record.get("description", ""),
        }
    if not request_payload["event"]["closed_route_ids"]:
        request_payload["event"]["closed_route_ids"] = scenario["closed_routes"]
    run_id = new_run_id()
    db.create_run(run_id, scenario["id"], request_payload)
    background_tasks.add_task(orchestrate, db, run_id, request_payload)
    return {"run_id": run_id, "status": "queued", "scenario_id": scenario["id"], "event_id": payload.event_id}


@router.get("/runs")
def list_runs(limit: int = 20) -> list[dict]:
    return db.list_runs(limit=limit)


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return run


@router.post("/runs/{run_id}/approve")
def approve_run(run_id: str, payload: ApprovalRequest) -> dict:
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    if run["status"] != "awaiting_approval":
        raise HTTPException(status_code=409, detail="当前方案尚未进入待审批状态")
    db.approve_run(run_id, payload.note)
    db.add_event(
        run_id=run_id,
        event_type="human.approved",
        agent_id="orchestrator",
        agent_name="编排控制器",
        title="人工审批通过",
        detail=payload.note or "值班负责人已确认方案并进入执行准备。",
        payload={"approved": True},
    )
    tasks = []
    result = run.get("result") or {}
    recommended_id = ((result.get("delivery") or {}).get("recommended_plan") or {}).get("plan_id")
    approved_plan = None
    for round_item in result.get("rounds", []):
        for plan in round_item.get("plans", []):
            if plan.get("plan_id") == recommended_id:
                approved_plan = plan
                break
    if approved_plan:
        tasks = ops.replace_tasks(run_id, approved_plan)
        db.add_event(
            run_id=run_id,
            event_type="tasks.created",
            agent_id="orchestrator",
            agent_name="编排控制器",
            title="执行任务已生成",
            detail=f"已生成 {len(tasks)} 条运输执行任务。",
            payload={"task_count": len(tasks)},
        )
    return {"run_id": run_id, "approved": True, "tasks": tasks}


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request) -> StreamingResponse:
    if not db.get_run(run_id):
        raise HTTPException(status_code=404, detail="运行记录不存在")

    async def event_stream():
        after = 0
        terminal = {"awaiting_approval", "needs_human", "failed"}
        idle_after_terminal = 0
        while True:
            if await request.is_disconnected():
                break
            events = db.events_after(run_id, after)
            for event in events:
                after = max(after, event["seq"])
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            run = db.get_run(run_id)
            if run and run["status"] in terminal:
                idle_after_terminal += 1
                if not events and idle_after_terminal >= 2:
                    break
            await asyncio.sleep(0.2)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get("/runs/{run_id}/report", response_class=HTMLResponse)
def run_report(run_id: str) -> str:
    run = db.get_run(run_id)
    if not run or not run["result"]:
        raise HTTPException(status_code=404, detail="报告尚未生成")
    result = run["result"]
    delivery = result["delivery"]
    plan = delivery.get("recommended_plan") or {}
    metrics = plan.get("metrics") or {}
    esc = html.escape
    rows = "".join(
        f"<tr><td>{esc(str(item['warehouse_name']))}</td><td>{esc(str(item['destination_name']))}</td>"
        f"<td>{esc(str(item['material_name']))}</td><td>{item['quantity']:g} {esc(str(item['base_unit']))}</td>"
        f"<td>{esc(str(item['route_name']))}</td><td>{item['route_eta_hours']}h</td></tr>"
        for round_item in result["rounds"]
        for plan_item in round_item["plans"]
        if plan_item["plan_id"] == plan.get("plan_id")
        for item in plan_item["assignments"]
    )
    return f"""
    <!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
    <title>急先锋调度方案 {run_id}</title>
    <style>
      body{{font-family:'Microsoft YaHei',sans-serif;max-width:980px;margin:40px auto;color:#152b2b;line-height:1.7}}
      h1{{border-bottom:4px solid #e65b35;padding-bottom:12px}} .meta{{color:#667;font-size:13px}}
      .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0}}
      .card{{background:#f4f1e8;border:1px solid #d9d4c8;padding:14px;border-radius:10px}}
      .num{{font-size:24px;font-weight:800}} table{{width:100%;border-collapse:collapse;font-size:13px}}
      th,td{{border:1px solid #ddd;padding:8px;text-align:left}} th{{background:#e9f2e9}}
      .warn{{background:#fff3e7;border-left:4px solid #e65b35;padding:12px;margin-top:16px}}
      @media print{{body{{margin:16px}}}} </style></head><body>
      <h1>急先锋 · 应急资源调度方案</h1>
      <div class="meta">运行编号：{esc(str(run_id))} · 场景：{esc(str(result['scenario']['name']))} · 数据模式：{esc(str(result['data_mode']))}（演练数据）</div>
      <div class="grid">
        <div class="card"><div>推荐方案</div><div class="num">{esc(str(plan.get('name','人工研判')))}</div></div>
        <div class="card"><div>预估成本</div><div class="num">¥{metrics.get('total_cost',0):,.0f}</div></div>
        <div class="card"><div>最长时效</div><div class="num">{metrics.get('max_eta_hours',0):.2f}h</div></div>
        <div class="card"><div>核验评分</div><div class="num">{plan.get('verification_score',0)}</div></div>
      </div>
      <h2>物资分配明细</h2>
      <table><thead><tr><th>仓库</th><th>安置点</th><th>物资</th><th>数量</th><th>路线</th><th>ETA</th></tr></thead><tbody>{rows}</tbody></table>
      <h2>风险与应急提示</h2><div class="warn">{'<br>'.join(esc(str(x)) for x in delivery.get('risk_alerts', []))}</div>
      <h2>交付说明</h2><p>{esc(str(delivery.get('summary','')))}</p>
      <p class="meta">{esc(str(delivery.get('disclaimer','')))}</p>
    </body></html>
    """


@router.post("/demo/reset")
def reset_demo() -> dict:
    db.reset_demo()
    return {"status": "reset", "message": "演示数据已恢复"}
