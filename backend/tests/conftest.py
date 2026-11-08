import os

# Automated tests must never call external model services.
os.environ["AGENTARTS_ENABLED"] = "false"
os.environ["AGENTARTS_BASE_URL"] = ""
os.environ["AGENTARTS_API_KEY"] = ""
os.environ["LLM_ENABLED"] = "false"
os.environ["FARMMATCH_MODE"] = "demo"
os.environ["AUTH_REQUIRED"] = "false"
os.environ["RUN_WORKER_CONCURRENCY"] = "1"
os.environ["RUN_POLL_INTERVAL_SECONDS"] = "0.05"
os.environ["RETRY_WORKER_ENABLED"] = "false"
os.environ["RESERVATION_SWEEP_ENABLED"] = "false"
os.environ["EVALUATION_SOAK_SECONDS"] = "3"
os.environ["EVALUATION_CONCURRENCY_LEVELS"] = "1,5,10,20"
