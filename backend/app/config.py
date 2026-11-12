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
DEMO_INVENTORY_MULTIPLIER = max(0.1, float(os.getenv("DEMO_INVENTORY_MULTIPLIER", "2.0")))
AGENT_RUNTIME = os.getenv("AGENT_RUNTIME", "local").strip().lower()
DEMO_PACING_FACTOR = max(0.0, min(1.0, float(os.getenv("DEMO_PACING_FACTOR", "0"))))
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
AGENT_TASK_CAPABILITY_MAP_JSON = os.getenv("AGENT_TASK_CAPABILITY_MAP_JSON", "").strip()
A2A_ENABLED = os.getenv("A2A_ENABLED", "true").strip().lower() == "true"
PERF_BENCHMARK_REPORT = os.getenv("PERF_BENCHMARK_REPORT", "data/benchmark_latest.json").strip()
AMAP_WEB_SERVICE_KEY = os.getenv("AMAP_WEB_SERVICE_KEY", "").strip()
OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org").rstrip("/")
OSRM_TIMEOUT = float(os.getenv("OSRM_TIMEOUT", "20"))
TOOL_ALLOWED_HOSTS = [
    item.strip().lower()
    for item in os.getenv("TOOL_ALLOWED_HOSTS", "example.com").split(",")
    if item.strip()
]
TOOL_ALLOW_PRIVATE_HOSTS = os.getenv("TOOL_ALLOW_PRIVATE_HOSTS", "false").strip().lower() == "true"
TOOL_HTTP_TIMEOUT = float(os.getenv("TOOL_HTTP_TIMEOUT", "20"))
MCP_PROTOCOL_VERSION = os.getenv("MCP_PROTOCOL_VERSION", "2024-11-05").strip()
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
LLM_TIMEOUT = max(1.0, min(60.0, float(os.getenv("LLM_TIMEOUT", "15"))))
LLM_TEMPERATURE = max(0.0, min(2.0, float(os.getenv("LLM_TEMPERATURE", "0.1"))))
LLM_JSON_MODE = os.getenv("LLM_JSON_MODE", "true").strip().lower() == "true"

RUN_WORKER_CONCURRENCY = max(1, min(16, int(os.getenv("RUN_WORKER_CONCURRENCY", "2"))))
RUN_QUEUE_MAX_SIZE = max(1, int(os.getenv("RUN_QUEUE_MAX_SIZE", "100")))
RUN_LEASE_SECONDS = max(30, int(os.getenv("RUN_LEASE_SECONDS", "120")))
RUN_POLL_INTERVAL_SECONDS = max(0.05, float(os.getenv("RUN_POLL_INTERVAL_SECONDS", "0.25")))

EVALUATION_SOAK_SECONDS = max(2.0, float(os.getenv("EVALUATION_SOAK_SECONDS", "30")))
EVALUATION_CONCURRENCY_LEVELS = [
    max(1, min(50, int(item.strip())))
    for item in os.getenv("EVALUATION_CONCURRENCY_LEVELS", "1,5,10,20").split(",")
    if item.strip()
]

EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "hash").strip().lower()
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "").rstrip("/")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "")
EMBEDDING_DIMENSIONS = max(64, min(4096, int(os.getenv("EMBEDDING_DIMENSIONS", "256"))))
EMBEDDING_TIMEOUT = max(1.0, float(os.getenv("EMBEDDING_TIMEOUT", "20")))
EMBEDDING_BATCH_SIZE = max(1, min(256, int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))))
LOCAL_EMBEDDING_ENABLED = os.getenv("LOCAL_EMBEDDING_ENABLED", "false").strip().lower() == "true"
LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2").strip()

MEMORY_DECAY_DAYS = max(1.0, float(os.getenv("MEMORY_DECAY_DAYS", "30")))
MEMORY_RETENTION_DAYS = max(1, int(os.getenv("MEMORY_RETENTION_DAYS", "90")))
MEMORY_MIN_CONFIDENCE = max(0.0, min(1.0, float(os.getenv("MEMORY_MIN_CONFIDENCE", "0.35"))))
MEMORY_CONTEXT_BUDGET_ITEMS = max(1, min(200, int(os.getenv("MEMORY_CONTEXT_BUDGET_ITEMS", "20"))))
MEMORY_CONTEXT_BUDGET_CHARS = max(500, min(100000, int(os.getenv("MEMORY_CONTEXT_BUDGET_CHARS", "8000"))))
MEMORY_ENTRY_SUMMARY_CHARS = max(120, min(5000, int(os.getenv("MEMORY_ENTRY_SUMMARY_CHARS", "600"))))

RESERVATION_TTL_HOURS = max(1, int(os.getenv("RESERVATION_TTL_HOURS", "24")))
RESERVATION_SWEEP_INTERVAL_SECONDS = max(5.0, float(os.getenv("RESERVATION_SWEEP_INTERVAL_SECONDS", "60")))
RESERVATION_SWEEP_ENABLED = os.getenv("RESERVATION_SWEEP_ENABLED", "true").strip().lower() == "true"
RETRY_WORKER_ENABLED = os.getenv("RETRY_WORKER_ENABLED", "true").strip().lower() == "true"
RETRY_MAX_ATTEMPTS = max(1, min(10, int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))))
RETRY_POLL_INTERVAL_SECONDS = max(1.0, float(os.getenv("RETRY_POLL_INTERVAL_SECONDS", "5")))
