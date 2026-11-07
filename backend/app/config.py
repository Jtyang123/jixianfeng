from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BASE_DIR.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(PROJECT_DIR / ".env")
def resolve_project_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path.resolve()


DB_PATH = resolve_project_path(os.getenv("FARMMATCH_DB", str(BASE_DIR / "farmmatch.db")))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
FARMMATCH_MODE = os.getenv("FARMMATCH_MODE", "demo").strip().lower()
AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "false").strip().lower() == "true"
AUTH_COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "farmmatch_session").strip()
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "false").strip().lower() == "true"
AUTH_SESSION_HOURS = int(os.getenv("AUTH_SESSION_HOURS", "8"))
AUTH_MAX_LOGIN_ATTEMPTS = int(os.getenv("AUTH_MAX_LOGIN_ATTEMPTS", "5"))
AUTH_LOGIN_WINDOW_MINUTES = int(os.getenv("AUTH_LOGIN_WINDOW_MINUTES", "15"))
AUTH_LOCKOUT_MINUTES = int(os.getenv("AUTH_LOCKOUT_MINUTES", "15"))
AUTH_BOOTSTRAP_USERNAME = os.getenv("AUTH_BOOTSTRAP_USERNAME", "admin").strip()
AUTH_BOOTSTRAP_PASSWORD = os.getenv("AUTH_BOOTSTRAP_PASSWORD", "")
SEED_DEMO = os.getenv("FARMMATCH_SEED_DEMO", "true").strip().lower() == "true"
AGENT_RUNTIME = os.getenv("AGENT_RUNTIME", "local").strip().lower()
DEMO_PACING_FACTOR = max(0.0, min(1.0, float(os.getenv("DEMO_PACING_FACTOR", "0.2"))))
AGENTARTS_ENABLED = os.getenv("AGENTARTS_ENABLED", "false").strip().lower() == "true"
AGENTARTS_BASE_URL = os.getenv("AGENTARTS_BASE_URL", "").rstrip("/")
AGENTARTS_API_KEY = os.getenv("AGENTARTS_API_KEY", "")
AGENTARTS_INVOKE_PATH = os.getenv("AGENTARTS_INVOKE_PATH", "/invocations")
AGENTARTS_AGENT_ENDPOINTS_JSON = os.getenv("AGENTARTS_AGENT_ENDPOINTS_JSON", "").strip()
AGENTARTS_AGENT_KEYS_JSON = os.getenv("AGENTARTS_AGENT_KEYS_JSON", "").strip()
AGENTARTS_AUTH_HEADER = os.getenv("AGENTARTS_AUTH_HEADER", "Authorization").strip()
AGENTARTS_AUTH_PREFIX = os.getenv("AGENTARTS_AUTH_PREFIX", "Bearer ").lstrip()
if AGENTARTS_AUTH_PREFIX and not AGENTARTS_AUTH_PREFIX.endswith(" "):
    AGENTARTS_AUTH_PREFIX += " "
AGENTARTS_TIMEOUT = float(os.getenv("AGENTARTS_TIMEOUT", "20"))
AMAP_WEB_SERVICE_KEY = os.getenv("AMAP_WEB_SERVICE_KEY", "").strip()
OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org").rstrip("/")
OSRM_TIMEOUT = float(os.getenv("OSRM_TIMEOUT", "20"))
AGENTARTS_SESSION_HEADER = os.getenv("AGENTARTS_SESSION_HEADER", "x-hw-agentarts-session-id").strip()

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "FARMMATCH_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

LLM_ENABLED = os.getenv("LLM_ENABLED", "false").strip().lower() == "true"
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
