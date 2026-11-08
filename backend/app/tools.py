from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any
from uuid import uuid4

import httpx

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
]


def _matches(item: dict, arguments: dict) -> bool:
    for key in ("warehouse_id", "destination_id", "status", "temp_zone"):
        expected = arguments.get(key)
        if expected not in (None, "") and str(item.get(key) or "") != str(expected):
            return False
    material_id = arguments.get("material_id")
    if material_id not in (None, "") and int(item.get("material_id") or 0) != int(material_id):
        return False
    return True


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
                (call_id, tool_id, session_id, status, json.dumps(arguments, ensure_ascii=False),
                 json.dumps(result, ensure_ascii=False), latency_ms, now_iso()),
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
            if tool["kind"] == "builtin":
                result = self._builtin(tool_id, arguments)
            elif tool["kind"] == "http":
                if not tool["endpoint"].startswith(("http://", "https://")):
                    raise ToolError("HTTP 工具 endpoint 必须是 http/https 地址")
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
                if not tool["endpoint"].startswith(("http://", "https://")):
                    raise ToolError("MCP 工具 endpoint 必须是 http/https 地址")
                remote_name = tool.get("config", {}).get("remote_name") or tool_id
                request_payload = {
                    "jsonrpc": "2.0", "id": f"call-{uuid4().hex[:8]}", "method": "tools/call",
                    "params": {"name": remote_name, "arguments": arguments},
                }
                async with httpx.AsyncClient(timeout=float(tool.get("config", {}).get("timeout", 20))) as client:
                    response = await client.post(tool["endpoint"], json=request_payload)
                    response.raise_for_status()
                    result = response.json()
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