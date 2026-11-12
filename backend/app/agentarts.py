from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime
from typing import Any

import httpx

from .config import (
    AGENTARTS_AGENT_ENDPOINTS_JSON,
    AGENTARTS_AGENT_KEYS_JSON,
    AGENTARTS_API_KEY,
    AGENTARTS_AUTH_HEADER,
    AGENTARTS_AUTH_PREFIX,
    AGENTARTS_BASE_URL,
    AGENTARTS_ENABLED,
    AGENTARTS_INVOKE_PATH,
    AGENTARTS_SESSION_HEADER,
    AGENTARTS_TIMEOUT,
)


class AgentArtsError(RuntimeError):
    pass


class AgentArtsBridge:
    """Huawei Cloud AgentArts runtime adapter.

    AgentArts SDK agents expose POST /invocations and GET /ping. Configure either
    one workflow endpoint through AGENTARTS_BASE_URL, or per-agent deployed
    endpoints through AGENTARTS_AGENT_ENDPOINTS_JSON. The local deterministic
    pipeline remains authoritative; AgentArts outputs cannot override hard rules.
    """

    def __init__(self, db=None) -> None:
        self.db = db
        self.base_url = AGENTARTS_BASE_URL.rstrip("/")
        self.api_key = AGENTARTS_API_KEY
        self.endpoints = self._parse_endpoints()
        self.agent_keys = self._parse_agent_keys()
        self.enabled = bool(AGENTARTS_ENABLED and (self.api_key or self.agent_keys) and (self.endpoints or self.base_url))

    def _parse_endpoints(self) -> dict[str, str]:
        if not AGENTARTS_AGENT_ENDPOINTS_JSON:
            return {}
        try:
            raw = json.loads(AGENTARTS_AGENT_ENDPOINTS_JSON)
        except json.JSONDecodeError as exc:
            raise AgentArtsError("AGENTARTS_AGENT_ENDPOINTS_JSON 必须是 JSON 对象") from exc
        if not isinstance(raw, dict):
            raise AgentArtsError("AGENTARTS_AGENT_ENDPOINTS_JSON 必须是 JSON 对象")
        return {str(key): str(value).rstrip("/") for key, value in raw.items() if value}

    def _parse_agent_keys(self) -> dict[str, str]:
        if not AGENTARTS_AGENT_KEYS_JSON:
            return {}
        try:
            raw = json.loads(AGENTARTS_AGENT_KEYS_JSON)
        except json.JSONDecodeError as exc:
            raise AgentArtsError("AGENTARTS_AGENT_KEYS_JSON 必须是 JSON 对象") from exc
        if not isinstance(raw, dict):
            raise AgentArtsError("AGENTARTS_AGENT_KEYS_JSON 必须是 JSON 对象")
        return {str(key): str(value) for key, value in raw.items() if value}

    @property
    def status(self) -> dict[str, Any]:
        expected = ["demand_agent", "matching_agent", "verifier_agent", "delivery_agent"]
        configured = sorted(self.endpoints.keys())
        missing = [agent_id for agent_id in expected if agent_id not in self.endpoints]
        keys_configured = sorted(self.agent_keys.keys())
        using_global_key = [agent_id for agent_id in configured if agent_id not in self.agent_keys]
        return {
            "enabled": self.enabled,
            "runtime": "huaweicloud-agentarts",
            "protocol": "AgentArts SDK /invocations",
            "base_url_configured": bool(self.base_url),
            "api_key_configured": bool(self.api_key),
            "agent_keys_configured": keys_configured,
            "agents_using_global_key": using_global_key,
            "agent_endpoints": configured,
            "expected_agents": expected,
            "configured_agents": configured,
            "missing_agents": missing,
            "agent_coverage": f"{len(configured)}/{len(expected)}",
            "all_agents_configured": not missing,
            "invoke_path": AGENTARTS_INVOKE_PATH,
            "session_header": AGENTARTS_SESSION_HEADER,
            "stream_parser": "sse+json",
            "fallback": "local-deterministic",
        }

    def endpoint_for(self, agent_id: str) -> str:
        if agent_id in self.endpoints:
            return self.endpoints[agent_id]
        if not self.base_url:
            raise AgentArtsError(f"未配置 AgentArts 端点：{agent_id}")
        if self.base_url.endswith("/invocations"):
            return self.base_url
        if self.base_url.endswith("/ping"):
            return self.base_url[:-5] + AGENTARTS_INVOKE_PATH
        return f"{self.base_url}{AGENTARTS_INVOKE_PATH}"

    def _api_key_for(self, agent_id: str = "") -> str:
        return self.agent_keys.get(agent_id) or self.api_key

    def override_endpoint(self, agent_id: str, endpoint: str) -> None:
        if endpoint:
            self.endpoints[agent_id] = endpoint.rstrip("/")

    def _headers(self, session_id: str, agent_id: str = "") -> dict[str, str]:
        api_key = self._api_key_for(agent_id)
        value = f"{AGENTARTS_AUTH_PREFIX}{api_key}" if AGENTARTS_AUTH_PREFIX else api_key
        headers = {AGENTARTS_AUTH_HEADER: value, "Content-Type": "application/json"}
        if AGENTARTS_SESSION_HEADER:
            headers[AGENTARTS_SESSION_HEADER] = uuid.uuid5(uuid.NAMESPACE_DNS, session_id).hex
        return headers

    @staticmethod
    def _parse_stream(text: str) -> tuple[str, int]:
        final_text = ""
        last_text = ""
        events_count = 0
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            events_count += 1
            event_name = event.get("event")
            data = event.get("data") or {}
            if event_name == "message":
                if data.get("text"):
                    last_text = data["text"]
                if data.get("is_finished") and data.get("summary"):
                    final_text = data["summary"]
            if event_name == "workflow_finished":
                outputs = data.get("outputs") or {}
                for key in ("responseContent", "result", "response", "output"):
                    value = outputs.get(key)
                    if value:
                        final_text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                        break
        return final_text or last_text, events_count

    async def _invoke_with_client(
        self,
        client: httpx.AsyncClient,
        url: str,
        body: dict,
        agent_id: str,
        session_id: str,
    ) -> dict:
        response = await client.post(url, json=body, headers=self._headers(session_id, agent_id))
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        stream_events = 0
        if "text/event-stream" in content_type or response.text.lstrip().startswith("data:"):
            text, stream_events = self._parse_stream(response.text)
            data = {"stream": True, "content_type": content_type}
        else:
            data = response.json()
            text = ""
            for key in ("response", "output", "result", "content"):
                value = data.get(key) if isinstance(data, dict) else None
                if value:
                    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                    break
        return {
            "mode": "agentarts",
            "runtime": "huaweicloud-agentarts",
            "agent_id": agent_id,
            "endpoint": url,
            "response": data,
            "text": text,
            "stream_events": stream_events,
            "session_id": session_id,
        }

    async def invoke(
        self,
        agent_id: str,
        payload: dict,
        session_id: str = "farmmatch",
        client: httpx.AsyncClient | None = None,
    ) -> dict:
        if not self.enabled:
            return {
                "mode": "local-fallback",
                "runtime": "huaweicloud-agentarts",
                "agent_id": agent_id,
                "reason": "AgentArts 环境变量未完整配置",
            }
        url = self.endpoint_for(agent_id)
        message = {
            "agent_id": agent_id,
            "session_id": session_id,
            "task": payload,
            "constraints": [
                "You are one role in FarmMatch emergency dispatch orchestration.",
                "Never invent or change inventory, ETA, cost, vehicle or rule-engine values.",
                "Return concise structured JSON; deterministic rules remain authoritative.",
                "Do not repeat the full input; return only decisions, risks and evidence references.",
            ],
        }
        query = json.dumps(message, ensure_ascii=False)
        body = {
            "query": query,
            "message": query,
            "agent_id": agent_id,
            "task": payload,
            "session_id": session_id,
        }
        try:
            if client is not None:
                return await self._invoke_with_client(client, url, body, agent_id, session_id)
            async with httpx.AsyncClient(timeout=AGENTARTS_TIMEOUT) as owned_client:
                return await self._invoke_with_client(owned_client, url, body, agent_id, session_id)
        except Exception as exc:
            raise AgentArtsError(f"AgentArts 调用失败 [{agent_id}]: {exc}") from exc
    @staticmethod
    def _error_type(exc: Exception) -> str:
        if isinstance(exc, httpx.TimeoutException):
            return "timeout"
        if isinstance(exc, httpx.ConnectError):
            return "connect"
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code if exc.response is not None else 0
            if status in {401, 403}:
                return "auth"
            if status >= 500:
                return "http_5xx"
        return "unknown"

    async def _probe_endpoint(self, client: httpx.AsyncClient, agent_id: str, target: str) -> dict:
        started = time.perf_counter()
        http_status = 0
        error_type = ""
        error = ""
        invocation_probe = target.rstrip("/").endswith(AGENTARTS_INVOKE_PATH)
        try:
            if invocation_probe:
                # Published AgentArts applications expose POST /invocations; GET may
                # never answer even when the runtime is healthy.
                response = await client.post(
                    target,
                    json={"query": "health check", "agent_id": agent_id},
                    headers=self._headers("agentarts-health-check", agent_id),
                    timeout=max(5.0, min(30.0, AGENTARTS_TIMEOUT)),
                )
            else:
                response = await client.get(
                    target,
                    headers=self._headers("agentarts-health-check", agent_id),
                )
            http_status = response.status_code
            if not invocation_probe and response.status_code in {404, 405, 501}:
                response = await client.post(
                    target,
                    json={"query": "health check", "agent_id": agent_id},
                    headers=self._headers("agentarts-health-check", agent_id),
                    timeout=min(5.0, AGENTARTS_TIMEOUT),
                )
                http_status = response.status_code
            ok = response.status_code < 400
            if not ok:
                error_type = "auth" if response.status_code in {401, 403} else ("http_5xx" if response.status_code >= 500 else "unknown")
                error = f"HTTP {response.status_code}"
            return self._check_item(agent_id, target, ok, started, http_status, error_type, error)
        except Exception as exc:
            return self._check_item(agent_id, target, False, started, http_status, self._error_type(exc), str(exc)[:240])

    def _check_item(self, agent_id: str, target: str, ok: bool, started: float, http_status: int,
                    error_type: str = "", error: str = "") -> dict:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        last_success_at = None
        if self.db is not None:
            try:
                last_success_at = self.db.agent_last_success(agent_id)
            except Exception:
                last_success_at = None
        return {
            "agent_id": agent_id,
            "endpoint": target,
            "ok": bool(ok),
            "status": "online" if ok else "degraded",
            "status_code": int(http_status or 0),
            "http_status": int(http_status or 0),
            "latency_ms": latency_ms,
            "error_type": "" if ok else (error_type or "unknown"),
            "error": error,
            "last_success_at": last_success_at,
        }

    async def ping(self) -> dict:
        if not self.enabled:
            return {"status": "not_configured", "online_rate": 0.0, "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"), "checks": [], **self.status}
        targets = [(agent_id, endpoint) for agent_id, endpoint in self.endpoints.items()]
        if not targets:
            targets = [("shared", self.endpoint_for("demand_agent"))]
        timeout = min(5.0, AGENTARTS_TIMEOUT)
        async with httpx.AsyncClient(timeout=timeout) as client:
            checks = await asyncio.gather(*(self._probe_endpoint(client, agent_id, target) for agent_id, target in targets))
        online = sum(1 for item in checks if item.get("ok"))
        return {
            "status": "ok" if checks and online == len(checks) else "degraded",
            "online_rate": round(online / max(len(checks), 1), 4),
            "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "checks": list(checks),
            **self.status,
        }
