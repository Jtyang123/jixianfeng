from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from .config import MCP_PROTOCOL_VERSION, TOOL_ALLOWED_HOSTS, TOOL_ALLOW_PRIVATE_HOSTS, TOOL_HTTP_TIMEOUT
from .database import Database, now_iso
from .knowledge import catalog as knowledge_catalog
from .knowledge_base import KnowledgeRepository


class ToolError(ValueError):
    pass


BUILTIN_TOOLS = [
    {
        "id": "inventory_query", "name": "库存快照查询", "kind": "builtin",
        "description": "按仓库、物资和状态查询最新冻结库存快照。",
        "input_schema": {"type": "object", "properties": {"warehouse_id": {"type": "string"}, "material_id": {"type": "integer"}, "status": {"type": "string"}}, "additionalProperties": False},
    },
    {
        "id": "route_query", "name": "路网与封闭路线查询", "kind": "builtin",
        "description": "查询仓库到安置点的道路、ETA、成本和开放状态。",
        "input_schema": {"type": "object", "properties": {"warehouse_id": {"type": "string"}, "destination_id": {"type": "string"}, "status": {"type": "string"}}, "additionalProperties": False},
    },
    {
        "id": "vehicle_query", "name": "车辆与运力查询", "kind": "builtin",
        "description": "按仓库、状态和温区查询可执行运输任务的车辆。",
        "input_schema": {"type": "object", "properties": {"warehouse_id": {"type": "string"}, "status": {"type": "string"}, "temp_zone": {"type": "string"}}, "additionalProperties": False},
    },
    {
        "id": "knowledge_search", "name": "知识库混合检索", "kind": "builtin",
        "description": "使用领域扩展哈希向量、BM25 和重排检索知识块。",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["query"], "additionalProperties": False},
    },
    {
        "id": "rule_catalog", "name": "规则目录查询", "kind": "builtin",
        "description": "返回内置应急调度规则目录和证据编号。",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "id": "demand_plan", "name": "需求标准化", "kind": "builtin",
        "description": "LLM Planner 专用：根据事件、安置点和物资选择生成结构化需求。",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    {
        "id": "generate_plans", "name": "多目标方案生成", "kind": "builtin",
        "description": "LLM Planner 专用：调用确定性方案生成器。",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    {
        "id": "verify_plans", "name": "独立方案核验", "kind": "builtin",
        "description": "LLM Planner 专用：调用确定性硬规则核验器。",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    {
        "id": "delivery_package", "name": "交付包生成", "kind": "builtin",
        "description": "LLM Planner 专用：从已核验方案生成交付包。",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    {
        "id": "escalate_human", "name": "转人工研判", "kind": "builtin",
        "description": "LLM Planner 专用：没有安全路径时转人工处理。",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
]


SENSITIVE_AUDIT_KEYS = {"api_key", "authorization", "password", "secret", "token", "access_key", "secret_key"}


def _sanitize_audit_value(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "<max-depth>"
    if isinstance(value, dict):
        return {
            str(key): ("<redacted>" if str(key).lower() in SENSITIVE_AUDIT_KEYS else _sanitize_audit_value(item, depth + 1))
            for key, item in list(value.items())[:100]
        }
    if isinstance(value, list):
        rows = [_sanitize_audit_value(item, depth + 1) for item in value[:100]]
        if len(value) > 100:
            rows.append({"omitted_items": len(value) - 100})
        return rows
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "<truncated>"
    return value


def _audit_json(value: Any, max_chars: int = 16000) -> str:
    serialized = json.dumps(_sanitize_audit_value(value), ensure_ascii=False)
    if len(serialized) <= max_chars:
        return serialized
    return json.dumps({
        "truncated": True,
        "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "preview": serialized[: max_chars // 2],
    }, ensure_ascii=False)


def _json_type_matches(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return True


def validate_tool_arguments(arguments: dict, schema: dict, path: str = "arguments") -> None:
    if not isinstance(arguments, dict):
        raise ToolError(f"{path} 必须是对象")
    if not schema:
        return
    expected_type = str(schema.get("type") or "")
    if expected_type and not _json_type_matches(arguments, expected_type):
        raise ToolError(f"{path} 类型必须为 {expected_type}")
    properties = schema.get("properties") or {}
    for key in schema.get("required") or []:
        if key not in arguments:
            raise ToolError(f"{path}.{key} 为必填参数")
    if schema.get("additionalProperties") is False:
        unexpected = sorted(set(arguments) - set(properties))
        if unexpected:
            raise ToolError(f"{path} 包含未定义参数：{', '.join(unexpected)}")
    for key, value in arguments.items():
        child = properties.get(key)
        if not isinstance(child, dict):
            continue
        child_type = str(child.get("type") or "")
        if child_type and not _json_type_matches(value, child_type):
            raise ToolError(f"{path}.{key} 类型必须为 {child_type}")
        if child_type in {"integer", "number"} and value is not None:
            if "minimum" in child and value < child["minimum"]:
                raise ToolError(f"{path}.{key} 不能小于 {child['minimum']}")
            if "maximum" in child and value > child["maximum"]:
                raise ToolError(f"{path}.{key} 不能大于 {child['maximum']}")


def _matches(item: dict, arguments: dict) -> bool:
    for key in ("warehouse_id", "destination_id", "status", "temp_zone"):
        expected = arguments.get(key)
        if expected not in (None, "") and str(item.get(key) or "") != str(expected):
            return False
    material_id = arguments.get("material_id")
    if material_id not in (None, "") and int(item.get("material_id") or 0) != int(material_id):
        return False
    return True


def validate_tool_endpoint(endpoint: str) -> str:
    parsed = urlparse(str(endpoint or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ToolError("工具 endpoint 必须是合法的 http/https 地址")
    host = parsed.hostname.lower()
    allowed = any(
        host == item or (item.startswith("*.") and host.endswith(item[1:])) or host.endswith("." + item)
        for item in TOOL_ALLOWED_HOSTS
    )
    if not allowed:
        raise ToolError(f"工具域名不在白名单：{host}")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ToolError(f"工具域名解析失败：{host}") from exc
    risky = []
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            risky.append(str(ip))
    if risky and not TOOL_ALLOW_PRIVATE_HOSTS:
        raise ToolError("工具 endpoint 解析到内网、回环或链路本地地址，已阻止 SSRF 风险")
    return endpoint


def _rpc_json(response: httpx.Response) -> dict:
    if "text/event-stream" in response.headers.get("content-type", ""):
        for line in reversed(response.text.splitlines()):
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    return json.loads(payload)
        raise ToolError("MCP SSE 响应没有 JSON-RPC 数据")
    return response.json()


async def mcp_call_remote(
    endpoint: str, remote_name: str, arguments: dict, config: dict | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict:
    config = config or {}
    validate_tool_endpoint(endpoint)
    protocol_version = str(config.get("protocol_version") or MCP_PROTOCOL_VERSION)
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    owned_client = client is None
    rpc_client = client or httpx.AsyncClient(timeout=float(config.get("timeout") or TOOL_HTTP_TIMEOUT))
    try:
        initialize_payload = {
            "jsonrpc": "2.0", "id": f"init-{uuid4().hex[:8]}", "method": "initialize",
            "params": {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "jixianfeng", "version": "1.0.0"},
            },
        }
        initialize_response = await rpc_client.post(endpoint, json=initialize_payload, headers=headers)
        initialize_response.raise_for_status()
        initialize_data = _rpc_json(initialize_response)
        if initialize_data.get("error"):
            raise ToolError(f"MCP initialize 失败：{initialize_data['error'].get('message', initialize_data['error'])}")
        negotiated_version = (initialize_data.get("result") or {}).get("protocolVersion", protocol_version)
        session_id = initialize_response.headers.get("Mcp-Session-Id") or initialize_response.headers.get("mcp-session-id")
        session_headers = dict(headers)
        if session_id:
            session_headers["Mcp-Session-Id"] = session_id
        initialized_response = await rpc_client.post(
            endpoint,
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            headers=session_headers,
        )
        initialized_response.raise_for_status()
        call_response = await rpc_client.post(
            endpoint,
            json={
                "jsonrpc": "2.0", "id": f"call-{uuid4().hex[:8]}", "method": "tools/call",
                "params": {"name": remote_name, "arguments": arguments},
            },
            headers=session_headers,
        )
        call_response.raise_for_status()
        call_data = _rpc_json(call_response)
        if call_data.get("error"):
            raise ToolError(f"MCP tools/call 失败：{call_data['error'].get('message', call_data['error'])}")
        return {
            "protocol_version": negotiated_version,
            "session_id": session_id or "",
            "response": call_data,
        }
    finally:
        if owned_client:
            await rpc_client.aclose()

class ToolRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def ensure_builtins(self) -> None:
        now = now_iso()
        with self.db.connect() as conn:
            for tool in BUILTIN_TOOLS:
                exists = conn.execute("SELECT id FROM tool_definitions WHERE id = ?", (tool["id"],)).fetchone()
                if exists:
                    continue
                conn.execute(
                    """INSERT INTO tool_definitions
                       (id, name, description, kind, endpoint, method, input_schema_json, config_json, enabled, created_at, updated_at)
                       VALUES (?, ?, ?, 'builtin', '', 'POST', ?, '{}', 1, ?, ?)""",
                    (tool["id"], tool["name"], tool["description"], json.dumps(tool["input_schema"], ensure_ascii=False), now, now),
                )

    @staticmethod
    def _row(row) -> dict:
        item = dict(row)
        item["input_schema"] = json.loads(item.pop("input_schema_json") or "{}")
        item["config"] = json.loads(item.pop("config_json") or "{}")
        item["enabled"] = bool(item["enabled"])
        item["protocol"] = {
            "builtin": "Function Calling / Builtin API",
            "http": "HTTP API Plugin",
            "mcp": "MCP / JSON-RPC",
        }.get(item["kind"], item["kind"])
        return item

    def list_tools(self, enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM tool_definitions"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY kind, name"
        with self.db.connect() as conn:
            return [self._row(row) for row in conn.execute(sql).fetchall()]

    def get_tool(self, tool_id: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM tool_definitions WHERE id = ?", (tool_id,)).fetchone()
            return self._row(row) if row else None
    def save_tool(self, tool_id: str, payload: dict) -> dict:
        now = now_iso()
        with self.db.connect() as conn:
            exists = conn.execute("SELECT id FROM tool_definitions WHERE id = ?", (tool_id,)).fetchone()
            values = (
                str(payload.get("name") or tool_id), str(payload.get("description") or ""),
                str(payload.get("kind") or "http"), str(payload.get("endpoint") or ""),
                str(payload.get("method") or "POST").upper(),
                json.dumps(payload.get("input_schema") or {"type": "object", "properties": {}}, ensure_ascii=False),
                json.dumps(payload.get("config") or {}, ensure_ascii=False),
                1 if payload.get("enabled", True) else 0, now,
            )
            if exists:
                conn.execute(
                    """UPDATE tool_definitions SET name=?, description=?, kind=?, endpoint=?, method=?,
                       input_schema_json=?, config_json=?, enabled=?, updated_at=? WHERE id=?""",
                    (*values, tool_id),
                )
            else:
                conn.execute(
                    """INSERT INTO tool_definitions
                       (name, description, kind, endpoint, method, input_schema_json, config_json, enabled, updated_at, id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (*values, tool_id, now),
                )
        return self.get_tool(tool_id) or {}

    def delete_tool(self, tool_id: str) -> bool:
        with self.db.connect() as conn:
            result = conn.execute("DELETE FROM tool_definitions WHERE id = ? AND kind != 'builtin'", (tool_id,))
            return result.rowcount > 0
    def record_call(self, tool_id: str, session_id: str, status: str, arguments: dict, result: dict, latency_ms: float) -> dict:
        call_id = f"TOOL-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:5].upper()}"
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO tool_calls
                   (id, tool_id, session_id, status, arguments_json, result_json, latency_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (call_id, tool_id, session_id, status, _audit_json(arguments),
                 _audit_json(result), latency_ms, now_iso()),
            )
        return {"id": call_id, "tool_id": tool_id, "status": status, "latency_ms": latency_ms}
class ToolService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.repository = ToolRepository(db)

    def _builtin(self, tool_id: str, arguments: dict) -> dict:
        if tool_id == "inventory_query":
            snapshot = self.db.latest_snapshot()
            rows = self.db.inventory_rows(snapshot["id"] if snapshot else None)
            return {"snapshot": snapshot, "items": [dict(item) for item in rows if _matches(item, arguments)][:500]}
        if tool_id == "route_query":
            rows = self.db.route_rows()
            return {"items": [dict(item) for item in rows if _matches(item, arguments)][:500]}
        if tool_id == "vehicle_query":
            rows = self.db.vehicle_rows()
            return {"items": [dict(item) for item in rows if _matches(item, arguments)][:500]}
        if tool_id == "knowledge_search":
            query = str(arguments.get("query") or "").strip()
            if not query:
                raise ToolError("knowledge_search 缺少 query")
            return {"query": query, "items": KnowledgeRepository(self.db).search(query, int(arguments.get("top_k") or 4))}
        if tool_id == "rule_catalog":
            return {"items": knowledge_catalog()}
        raise ToolError(f"未实现的内置工具：{tool_id}")
    async def call_tool(self, tool_id: str, arguments: dict | None = None, session_id: str = "") -> dict:
        self.repository.ensure_builtins()
        tool = self.repository.get_tool(tool_id)
        if not tool or not tool["enabled"]:
            raise ToolError(f"工具不存在或未启用：{tool_id}")
        arguments = arguments or {}
        started = time.perf_counter()
        try:
            validate_tool_arguments(arguments, tool.get("input_schema") or {})
            if tool["kind"] == "builtin":
                result = self._builtin(tool_id, arguments)
            elif tool["kind"] == "http":
                validate_tool_endpoint(tool["endpoint"])
                headers = tool.get("config", {}).get("headers") or {}
                timeout = float(tool.get("config", {}).get("timeout", 15))
                async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
                    if tool["method"] == "GET":
                        response = await client.get(tool["endpoint"], params=arguments)
                    else:
                        response = await client.request(tool["method"], tool["endpoint"], json=arguments)
                    response.raise_for_status()
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = {"text": response.text}
                result = {"status_code": response.status_code, "payload": payload}
            elif tool["kind"] == "mcp":
                remote_name = tool.get("config", {}).get("remote_name") or tool_id
                result = await mcp_call_remote(
                    tool["endpoint"], remote_name, arguments, tool.get("config", {})
                )
            else:
                raise ToolError(f"不支持的工具类型：{tool['kind']}")
            latency = round((time.perf_counter() - started) * 1000, 2)
            audit = self.repository.record_call(tool_id, session_id, "success", arguments, result, latency)
            return {"tool": tool, "result": result, "audit": audit}
        except Exception as exc:
            latency = round((time.perf_counter() - started) * 1000, 2)
            error = {"error": str(exc)[:500]}
            self.repository.record_call(tool_id, session_id, "failed", arguments, error, latency)
            raise ToolError(f"工具调用失败：{str(exc)[:300]}") from exc

    def mcp_tool_catalog(self) -> list[dict]:
        return [
            {"name": item["id"], "description": item["description"], "inputSchema": item["input_schema"]}
            for item in self.repository.list_tools(enabled_only=True)
        ]

    async def handle_mcp(self, payload: dict) -> dict:
        self.repository.ensure_builtins()
        request_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params") or {}
        if method == "initialize":
            return {
                "jsonrpc": "2.0", "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "jixianfeng-emergency-tools", "version": "1.0.0"},
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": self.mcp_tool_catalog()}}
        if method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            try:
                result = await self.call_tool(name, arguments, session_id=f"mcp-{request_id}")
                return {"jsonrpc": "2.0", "id": request_id, "result": result}
            except ToolError as exc:
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(exc)}}
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"不支持的方法：{method}"}}