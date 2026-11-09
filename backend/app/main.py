from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import api as api_module
from .api import auth_service, db, router
from .agentarts import AgentArtsBridge
from .config import ALLOWED_ORIGINS, AUTH_COOKIE_NAME, AUTH_COOKIE_SECURE, AUTH_REQUIRED, PROJECT_DIR
from .knowledge_base import KnowledgeRepository
from .scheduler import AGENTS
from .workflow_engine import WorkflowRepository

db.initialize()
if AUTH_REQUIRED:
    auth_service.ensure_bootstrap_admin()
api_module.registry_service.sync_builtin_agents(AGENTS, AgentArtsBridge().endpoints)
WorkflowRepository(db).ensure_template_library()
KnowledgeRepository(db).ensure_seed_documents()

@asynccontextmanager
async def lifespan(_app: FastAPI):
    await api_module.start_runtime()
    try:
        yield
    finally:
        await api_module.stop_runtime()


app = FastAPI(
    title="急先锋 · 应急资源调度推演平台",
    description="四智能体协同、RAG 证据、独立核验与多方案生成",
    version="1.0.0",
    lifespan=lifespan,
)

logger = logging.getLogger(__name__)

FIELD_LABELS = {
    "title": "事件标题",
    "event_type": "事件类型",
    "severity": "事件等级",
    "location": "影响区域",
    "affected_population": "影响人口",
    "closed_routes": "封闭路线",
    "name": "名称",
    "region": "区域或地址",
    "address": "详细地址",
    "lat": "纬度",
    "lng": "经度",
    "capacity": "容量",
    "capacity_kg": "车辆载重",
    "warehouse_id": "所属仓库",
    "material_id": "物资",
    "quantity": "数量",
    "batch_no": "批次号",
    "expiry_date": "有效期",
    "plate_no": "车牌号",
    "vehicle_type": "车型",
    "code": "编码",
    "base_unit": "基本单位",
    "package_unit": "包装单位",
    "conversion_rate": "包装换算率",
    "unit_weight_kg": "单件重量",
    "priority": "优先级",
    "deadline_hours": "处理时限",
    "scenario_id": "推演场景",
    "event": "事件信息",
    "demand_items": "物资需求",
    "weights": "调度权重",
    "speed": "时效权重",
    "cost": "成本权重",
    "risk": "安全权重",
    "username": "用户名",
    "display_name": "显示名称",
    "password": "密码",
    "current_password": "当前密码",
    "new_password": "新密码",
    "role": "角色",
    "is_active": "账号状态",
    "endpoint": "调用端点",
    "version": "版本",
    "model": "模型",
    "prompt_version": "提示词版本",
    "capabilities": "能力列表",
    "description": "描述",
    "status": "状态",
    "assignee": "负责人",
    "note": "备注",
    "task": "任务内容",
    "session_id": "会话编号",
}

STATUS_MESSAGES = {
    400: "提交内容不符合要求",
    401: "登录已失效或请求未授权",
    403: "没有权限执行该操作",
    404: "请求的资源不存在",
    405: "不支持当前请求方法",
    409: "请求与当前数据状态冲突",
    422: "提交内容格式不正确",
    429: "操作过于频繁，请稍后再试",
    500: "服务器内部错误，请稍后重试",
    502: "外部服务暂时不可用，请稍后重试",
    503: "服务暂时不可用，请稍后重试",
}


def _has_chinese(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value or ""))


def _field_label(location) -> str:
    for part in reversed(list(location or [])):
        if isinstance(part, str) and part not in {"body", "query", "path"} and not part.isdigit():
            return FIELD_LABELS.get(part, "参数")
    return "参数"


def _validation_message(error: dict) -> str:
    error_type = str(error.get("type") or "")
    ctx = error.get("ctx") or {}
    raw = str(error.get("msg") or "").strip()
    if raw.startswith("Value error, "):
        raw = raw[len("Value error, "):].strip()
    messages = {
        "missing": "为必填项",
        "string_too_short": f"至少需要 {ctx.get('min_length', 1)} 个字符",
        "string_too_long": f"不能超过 {ctx.get('max_length', '')} 个字符".strip(),
        "too_short": f"至少需要 {ctx.get('min_length', 1)} 项",
        "too_long": f"不能超过 {ctx.get('max_length', '')} 项".strip(),
        "greater_than_equal": f"不能小于 {ctx.get('ge', 0)}",
        "greater_than": f"必须大于 {ctx.get('gt', 0)}",
        "less_than_equal": f"不能大于 {ctx.get('le', 0)}",
        "less_than": f"必须小于 {ctx.get('lt', 0)}",
        "int_parsing": "必须是整数",
        "float_parsing": "必须是有效数字",
        "decimal_parsing": "必须是有效数字",
        "bool_parsing": "必须是有效的是或否",
        "date_parsing": "日期格式不正确",
        "datetime_parsing": "日期时间格式不正确",
        "json_invalid": "请求内容不是合法 JSON",
        "list_type": "必须是列表",
        "dict_type": "必须是对象",
        "string_type": "必须是文本",
        "int_type": "必须是整数",
        "float_type": "必须是数字",
        "bool_type": "必须是有效的是或否",
        "literal_error": "必须选择允许的选项",
        "enum": "必须选择允许的选项",
        "extra_forbidden": "不允许提交该字段",
    }
    if error_type in messages:
        return messages[error_type]
    if _has_chinese(raw):
        return raw
    return "填写内容不符合要求"


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    normalized = []
    for error in exc.errors():
        label = _field_label(error.get("loc"))
        normalized.append({"field": label, "message": _validation_message(error)})
    detail = "；".join(f"{item['field']}：{item['message']}" for item in normalized) or "提交内容不符合要求"
    return JSONResponse(status_code=422, content={"detail": detail, "errors": normalized})


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if isinstance(detail, str) and _has_chinese(detail):
        message = detail
    elif isinstance(detail, dict) and _has_chinese(str(detail.get("message", ""))):
        message = str(detail["message"])
    else:
        message = STATUS_MESSAGES.get(exc.status_code, "请求处理失败")
    return JSONResponse(status_code=exc.status_code, content={"detail": message}, headers=exc.headers)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("未处理异常：%s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误，请稍后重试"})

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PUBLIC_API_PATHS = {"/api/health", "/api/auth/login"}
SELF_SERVICE_WRITES = {"/api/auth/logout", "/api/auth/change-password"}


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path in PUBLIC_API_PATHS or path.startswith("/api/integrations/") or path == "/api/mcp":
        return await call_next(request)
    if not AUTH_REQUIRED:
        session_user = api_module.auth_service.resolve_session(request.cookies.get(AUTH_COOKIE_NAME, ""))
        request.state.user = session_user or api_module.auth_service.demo_user()
        return await call_next(request)

    user = api_module.auth_service.resolve_session(request.cookies.get(AUTH_COOKIE_NAME, ""))
    if not user:
        return JSONResponse({"detail": "登录已过期或未登录"}, status_code=401)
    request.state.user = user
    if request.method in WRITE_METHODS and path not in SELF_SERVICE_WRITES:
        if path.startswith("/api/auth/users") and user["role"] != "admin":
            return JSONResponse({"detail": "需要系统管理员权限"}, status_code=403)
        if not path.startswith("/api/auth/users") and user["role"] not in {"dispatcher", "admin"}:
            return JSONResponse({"detail": "当前角色只有只读权限"}, status_code=403)

    response = await call_next(request)
    if request.method in WRITE_METHODS:
        try:
            api_module.auth_service.audit(
                action=f"{request.method.lower()} {path}",
                method=request.method,
                path=path,
                status_code=response.status_code,
                user=user,
                ip=request.client.host if request.client else "",
            )
        except Exception:
            logger.exception("Failed to persist API audit log for %s %s", request.method, path)
    return response




@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(self)"
    if request.url.path.startswith("/api/auth/"):
        response.headers["Cache-Control"] = "no-store"
    if AUTH_COOKIE_SECURE:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

app.include_router(router)

FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"
if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/")
def root():
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-store"})
    return JSONResponse(
        {
            "name": "急先锋 API",
            "status": "ready",
            "docs": "/docs",
            "frontend": "Run `npm run dev` in frontend/",
        }
    )


@app.get("/{path:path}")
def spa_fallback(path: str):
    index = FRONTEND_DIST / "index.html"
    if index.exists() and not path.startswith("api/"):
        return FileResponse(index, headers={"Cache-Control": "no-store"})
    return JSONResponse({"detail": "请求的资源不存在"}, status_code=404)
