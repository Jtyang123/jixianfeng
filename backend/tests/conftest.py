import os

# Automated tests must never call external model services.
os.environ["AGENTARTS_ENABLED"] = "false"
os.environ["AGENTARTS_BASE_URL"] = ""
os.environ["AGENTARTS_API_KEY"] = ""
os.environ["LLM_ENABLED"] = "false"
os.environ["FARMMATCH_MODE"] = "demo"
os.environ["AUTH_REQUIRED"] = "false"
