from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api as api_module
from .api import auth_service, db, router
from .agentarts import AgentArtsBridge
from .config import ALLOWED_ORIGINS, AUTH_COOKIE_NAME, AUTH_COOKIE_SECURE, AUTH_REQUIRED, PROJECT_DIR
from .scheduler import AGENTS

db.initialize()
if AUTH_REQUIRED:
    auth_service.ensure_bootstrap_admin()
api_module.registry_service.sync_builtin_agents(AGENTS, AgentArtsBridge().endpoints)

app = FastAPI(
    title="急先锋 · 应急资源调度推演平台",
    description="四智能体协同、RAG 证据、独立核验与多方案生成",
    version="1.0.0",
)

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
    if not path.startswith("/api/") or path in PUBLIC_API_PATHS or path.startswith("/api/integrations/"):
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
            pass
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
    return JSONResponse({"detail": "Not Found"}, status_code=404)
