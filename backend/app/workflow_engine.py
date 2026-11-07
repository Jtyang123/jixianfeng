from __future__ import annotations

import asyncio
import inspect
import json
import time
from datetime import datetime
from typing import Any, Awaitable, Callable
from uuid import uuid4

from .database import Database, now_iso


NODE_TYPES = {"agent", "condition", "subworkflow"}
CONDITION_OPERATORS = {"eq", "ne", "in", "exists", "truthy", "contains"}
MAX_SUBWORKFLOW_DEPTH = 5


class WorkflowValidationError(ValueError):
    pass


def _field_value(context: dict, path: str) -> Any:
    value: Any = context
    for part in str(path or "").split("."):
        if not part:
            continue
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit():
            index = int(part)
            value = value[index] if 0 <= index < len(value) else None
        else:
            return None
    return value


def evaluate_rule(rule: dict | None, context: dict) -> bool:
    if not rule:
        return True
    field = str(rule.get("field") or "")
    operator = str(rule.get("operator") or "truthy")
    expected = rule.get("value")
    actual = _field_value(context, field)
    if operator == "eq":
        return actual == expected
    if operator == "ne":
        return actual != expected
    if operator == "in":
        return actual in (expected or [])
    if operator == "exists":
        return actual is not None
    if operator == "contains":
        if isinstance(actual, str):
            return str(expected) in actual
        if isinstance(actual, (list, tuple, set, dict)):
            return expected in actual
        return False
    if operator == "truthy":
        return bool(actual)
    raise WorkflowValidationError(f"不支持的条件操作符：{operator}")


def validate_workflow(definition: dict) -> dict:
    if not isinstance(definition, dict):
        raise WorkflowValidationError("工作流定义必须是 JSON 对象")
    nodes = definition.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise WorkflowValidationError("工作流至少需要一个节点")
    normalized_nodes = []
    node_ids = set()
    for index, raw in enumerate(nodes):
        if not isinstance(raw, dict):
            raise WorkflowValidationError(f"第 {index + 1} 个节点格式不正确")
        node = dict(raw)
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            raise WorkflowValidationError(f"第 {index + 1} 个节点缺少 ID")
        if node_id in node_ids:
            raise WorkflowValidationError(f"节点 ID 重复：{node_id}")
        node_ids.add(node_id)
        node_type = str(node.get("type") or "agent")
        if node_type not in NODE_TYPES:
            raise WorkflowValidationError(f"节点 {node_id} 的类型不支持：{node_type}")
        if node_type == "agent" and not str(node.get("agent_id") or "").strip():
            raise WorkflowValidationError(f"Agent 节点 {node_id} 缺少 agent_id")
        if node_type == "subworkflow" and not str(node.get("workflow_id") or "").strip():
            raise WorkflowValidationError(f"子工作流节点 {node_id} 缺少 workflow_id")
        dependencies = node.get("depends_on") or []
        if not isinstance(dependencies, list):
            raise WorkflowValidationError(f"节点 {node_id} 的 depends_on 必须是数组")
        node["id"] = node_id
        node["type"] = node_type
        node["depends_on"] = [str(item) for item in dependencies]
        node["priority"] = max(1, min(100, int(node.get("priority", 50))))
        when = node.get("when")
        if when is not None:
            if not isinstance(when, dict):
                raise WorkflowValidationError(f"节点 {node_id} 的 when 必须是对象")
            if str(when.get("operator") or "truthy") not in CONDITION_OPERATORS:
                raise WorkflowValidationError(f"节点 {node_id} 使用了不支持的条件操作符")
        retry = node.get("retry")
        if retry is not None:
            if not isinstance(retry, dict):
                raise WorkflowValidationError(f"节点 {node_id} 的 retry 必须是对象")
            node["retry"] = {
                "max_attempts": max(1, min(5, int(retry.get("max_attempts", 1)))),
                "when": retry.get("when") if isinstance(retry.get("when"), dict) else None,
            }
        normalized_nodes.append(node)
    for node in normalized_nodes:
        for dependency in node["depends_on"]:
            if dependency not in node_ids:
                raise WorkflowValidationError(f"节点 {node['id']} 依赖不存在的节点：{dependency}")
            if dependency == node["id"]:
                raise WorkflowValidationError(f"节点 {node['id']} 不能依赖自身")
    indegree = {node["id"]: len(node["depends_on"]) for node in normalized_nodes}
    dependents: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for node in normalized_nodes:
        for dependency in node["depends_on"]:
            dependents[dependency].append(node["id"])
    ready = [node_id for node_id, count in indegree.items() if count == 0]
    order = []
    while ready:
        node_id = ready.pop(0)
        order.append(node_id)
        for dependent in dependents[node_id]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    if len(order) != len(normalized_nodes):
        raise WorkflowValidationError("工作流存在循环依赖，无法执行")
    result = dict(definition)
    result["nodes"] = normalized_nodes
    result["execution_order"] = order
    return result

class WorkflowRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_workflows(self, stage: str | None = None, enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM workflow_definitions"
        clauses = []
        params: list[Any] = []
        if stage:
            clauses.append("stage = ?")
            params.append(stage)
        if enabled_only:
            clauses.append("enabled = 1")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC, id"
        with self.db.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["definition"] = json.loads(item.pop("definition_json") or "{}")
            item["enabled"] = bool(item["enabled"])
            result.append(item)
        return result

    def get_workflow(self, workflow_id: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM workflow_definitions WHERE id = ?", (workflow_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["definition"] = json.loads(item.pop("definition_json") or "{}")
        item["enabled"] = bool(item["enabled"])
        return item

    def save_workflow(self, workflow_id: str, payload: dict) -> dict:
        definition = validate_workflow(payload.get("definition") or {})
        now = now_iso()
        with self.db.connect() as conn:
            existing = conn.execute("SELECT version, created_at FROM workflow_definitions WHERE id = ?", (workflow_id,)).fetchone()
            if existing:
                version = int(existing["version"]) + 1
                conn.execute(
                    """UPDATE workflow_definitions
                       SET name=?, version=?, description=?, stage=?, definition_json=?, enabled=?, updated_at=?
                       WHERE id=?""",
                    (
                        str(payload.get("name") or workflow_id).strip(),
                        version,
                        str(payload.get("description") or ""),
                        str(payload.get("stage") or "post_verification"),
                        json.dumps(definition, ensure_ascii=False),
                        1 if payload.get("enabled", True) else 0,
                        now,
                        workflow_id,
                    ),
                )
            else:
                version = 1
                conn.execute(
                    """INSERT INTO workflow_definitions
                       (id, name, version, description, stage, definition_json, enabled, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        workflow_id,
                        str(payload.get("name") or workflow_id).strip(),
                        version,
                        str(payload.get("description") or ""),
                        str(payload.get("stage") or "post_verification"),
                        json.dumps(definition, ensure_ascii=False),
                        1 if payload.get("enabled", True) else 0,
                        now,
                        now,
                    ),
                )
        return self.get_workflow(workflow_id) or {}

    def delete_workflow(self, workflow_id: str) -> bool:
        with self.db.connect() as conn:
            row = conn.execute("SELECT id FROM workflow_definitions WHERE id = ?", (workflow_id,)).fetchone()
            if not row:
                return False
            conn.execute("DELETE FROM workflow_definitions WHERE id = ?", (workflow_id,))
        return True

    def create_run(self, workflow_id: str, context: dict) -> str:
        run_id = f"WFR-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:4].upper()}"
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO workflow_runs (id, workflow_id, status, context_json, created_at) VALUES (?, ?, 'running', ?, ?)",
                (run_id, workflow_id, json.dumps(context, ensure_ascii=False), now_iso()),
            )
        return run_id

    def save_node_run(self, run_id: str, record: dict) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO workflow_node_runs
                   (workflow_run_id, node_id, node_type, agent_id, status, attempts,
                    queued_at, started_at, completed_at, wait_ms, execution_ms, output_json, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    record["node_id"],
                    record["node_type"],
                    record.get("agent_id", ""),
                    record["status"],
                    record.get("attempts", 0),
                    record.get("queued_at"),
                    record.get("started_at"),
                    record.get("completed_at"),
                    record.get("wait_ms", 0),
                    record.get("execution_ms", 0),
                    json.dumps(record.get("output") or {}, ensure_ascii=False),
                    str(record.get("error") or "")[:1000],
                ),
            )

    def get_run(self, run_id: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM workflow_runs WHERE id = ?", (run_id,)).fetchone()
            if not row:
                return None
            node_rows = conn.execute(
                "SELECT * FROM workflow_node_runs WHERE workflow_run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        result = dict(row)
        result["context"] = json.loads(result.pop("context_json") or "{}")
        result["metrics"] = json.loads(result.pop("metrics_json") or "{}")
        result["nodes"] = []
        for node_row in node_rows:
            node = dict(node_row)
            node["output"] = json.loads(node.pop("output_json") or "{}")
            result["nodes"].append(node)
        return result
    def finish_run(self, run_id: str, status: str, context: dict, metrics: dict) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE workflow_runs SET status=?, context_json=?, metrics_json=?, completed_at=? WHERE id=?",
                (status, json.dumps(context, ensure_ascii=False), json.dumps(metrics, ensure_ascii=False), now_iso(), run_id),
            )

class WorkflowEngine:
    def __init__(
        self,
        db: Database,
        invoke_agent: Callable[[str, dict, dict], Awaitable[dict]],
        load_workflow: Callable[[str], dict | None],
        event_sink: Callable[[str, str], Any] | None = None,
        agent_metrics: dict[str, dict] | None = None,
        max_workers: int = 4,
    ) -> None:
        self.db = db
        self.invoke_agent = invoke_agent
        self.load_workflow = load_workflow
        self.event_sink = event_sink
        self.agent_metrics = agent_metrics or {}
        self.max_workers = max(1, min(max_workers, 16))
        self.repository = WorkflowRepository(db)

    async def _event(self, node_id: str, message: str) -> None:
        if not self.event_sink:
            return
        result = self.event_sink(node_id, message)
        if inspect.isawaitable(result):
            await result

    def _priority(self, node: dict) -> int:
        base = int(node.get("priority", 50))
        if node.get("type") != "agent":
            return base
        metrics = self.agent_metrics.get(str(node.get("agent_id") or ""), {})
        penalty = min(
            35,
            round(float(metrics.get("average_latency_ms", 0)) / 800)
            + round(float(metrics.get("failure_rate", 0)) * 20)
            + min(10, int(metrics.get("pending_calls", 0)) * 3),
        )
        return max(1, base - penalty)

    async def _execute_node(self, node: dict, context: dict, depth: int, prefix: str) -> tuple[dict, int]:
        retry = node.get("retry") or {}
        max_attempts = int(retry.get("max_attempts", 1))
        for attempt in range(1, max_attempts + 1):
            try:
                if node["type"] == "agent":
                    output = await self.invoke_agent(str(node["agent_id"]), context, node)
                elif node["type"] == "condition":
                    output = {"matched": evaluate_rule(node.get("when"), context)}
                else:
                    if depth >= MAX_SUBWORKFLOW_DEPTH:
                        raise WorkflowValidationError("子工作流嵌套超过最大深度")
                    child = self.load_workflow(str(node["workflow_id"]))
                    if not child:
                        raise WorkflowValidationError(f"子工作流不存在：{node['workflow_id']}")
                    child_definition = validate_workflow(child.get("definition") or child)
                    child_result = await self._run_scope(
                        child_definition["nodes"], context, depth + 1, prefix=f"{prefix}{node['id']}."
                    )
                    output = {
                        "workflow_id": node["workflow_id"],
                        "status": child_result["status"],
                        "outputs": child_result["outputs"],
                    }
                normalized = output if isinstance(output, dict) else {"result": output}
                retry_context = {**context, **normalized}
                if attempt < max_attempts and retry.get("when") and evaluate_rule(retry["when"], retry_context):
                    await self._event(node["id"], f"节点 {node['id']} 第 {attempt} 次结果触发重试")
                    continue
                return normalized, attempt
            except Exception:
                if attempt >= max_attempts:
                    raise
                await self._event(node["id"], f"节点 {node['id']} 第 {attempt} 次执行失败，准备重试")
        return {}, max_attempts

    async def _run_scope(self, nodes: list[dict], context: dict, depth: int, prefix: str) -> dict:
        node_map = {item["id"]: item for item in nodes}
        pending = set(node_map)
        completed: set[str] = set()
        skipped: set[str] = set()
        failed: set[str] = set()
        enqueued: set[str] = set()
        queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        results: asyncio.Queue = asyncio.Queue()
        sequence = 0
        active = 0
        peak_active = 0
        stop = False
        outputs: dict[str, dict] = {}

        async def worker() -> None:
            nonlocal active, peak_active
            while True:
                _, _, payload = await queue.get()
                if payload is None:
                    queue.task_done()
                    break
                node = payload["node"]
                queued_at = payload["queued_at"]
                started_at = datetime.now().astimezone()
                started_perf = time.perf_counter()
                active += 1
                peak_active = max(peak_active, active)
                try:
                    output, attempts = await self._execute_node(node, context, depth, prefix)
                    completed_at = datetime.now().astimezone()
                    await results.put({
                        "status": "ok", "node": node, "output": output, "attempts": attempts,
                        "queued_at": queued_at, "started_at": started_at, "completed_at": completed_at,
                        "wait_ms": max(0, (started_at - queued_at).total_seconds() * 1000),
                        "execution_ms": (time.perf_counter() - started_perf) * 1000,
                    })
                except Exception as exc:
                    completed_at = datetime.now().astimezone()
                    await results.put({
                        "status": "error", "node": node, "error": str(exc), "attempts": 1,
                        "queued_at": queued_at, "started_at": started_at, "completed_at": completed_at,
                        "wait_ms": max(0, (started_at - queued_at).total_seconds() * 1000),
                        "execution_ms": (time.perf_counter() - started_perf) * 1000,
                    })
                finally:
                    active -= 1
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(min(self.max_workers, len(nodes)))]
        try:
            while pending or enqueued or active:
                if not stop:
                    for node_id in list(pending):
                        node = node_map[node_id]
                        dependencies = node.get("depends_on", [])
                        if any(dep in failed for dep in dependencies):
                            pending.remove(node_id)
                            failed.add(node_id)
                            continue
                        if not all(dep in completed or dep in skipped for dep in dependencies):
                            continue
                        pending.remove(node_id)
                        if not evaluate_rule(node.get("when"), context):
                            skipped.add(node_id)
                            self.repository.save_node_run(context["_workflow_run_id"], {
                                "node_id": f"{prefix}{node_id}", "node_type": node["type"],
                                "agent_id": node.get("agent_id", ""), "status": "skipped",
                                "queued_at": now_iso(), "completed_at": now_iso(),
                            })
                            await self._event(node_id, f"节点 {node_id} 条件不满足，已跳过")
                            continue
                        enqueued.add(node_id)
                        sequence += 1
                        await queue.put((-self._priority(node), sequence, {
                            "node": node, "queued_at": datetime.now().astimezone(),
                        }))
                if stop and not enqueued and active == 0:
                    skipped.update(pending)
                    pending.clear()
                    break
                if not enqueued and not active and not pending:
                    break
                result = await results.get()
                node = result["node"]
                enqueued.discard(node["id"])
                if result["status"] == "ok":
                    completed.add(node["id"])
                    outputs[node["id"]] = result["output"]
                    context["node_outputs"][f"{prefix}{node['id']}"] = result["output"]
                    context.update({key: value for key, value in result["output"].items() if key not in context})
                elif node.get("on_error") == "continue":
                    skipped.add(node["id"])
                else:
                    failed.add(node["id"])
                    stop = True
                self.repository.save_node_run(context["_workflow_run_id"], {
                    "node_id": f"{prefix}{node['id']}", "node_type": node["type"],
                    "agent_id": node.get("agent_id", ""), "status": result["status"],
                    "attempts": result.get("attempts", 0),
                    "queued_at": result["queued_at"].isoformat(timespec="milliseconds"),
                    "started_at": result["started_at"].isoformat(timespec="milliseconds"),
                    "completed_at": result["completed_at"].isoformat(timespec="milliseconds"),
                    "wait_ms": round(result.get("wait_ms", 0), 2),
                    "execution_ms": round(result.get("execution_ms", 0), 2),
                    "output": result.get("output") or {}, "error": result.get("error", ""),
                })
                await self._event(node["id"], f"节点 {node['id']} 执行{'完成' if result['status'] == 'ok' else '失败'}")
            for _ in workers:
                await queue.put((0, 0, None))
            await asyncio.gather(*workers)
        finally:
            for task in workers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        status = "success" if not failed else "failed"
        return {
            "status": status, "outputs": outputs, "completed": sorted(completed),
            "skipped": sorted(skipped), "failed": sorted(failed), "peak_concurrency": peak_active,
        }

    async def execute(self, workflow_id: str, definition: dict, initial_context: dict) -> dict:
        normalized = validate_workflow(definition)
        run_id = self.repository.create_run(workflow_id, initial_context)
        context = dict(initial_context)
        context["node_outputs"] = {}
        context["_workflow_run_id"] = run_id
        started = time.perf_counter()
        try:
            result = await self._run_scope(normalized["nodes"], context, 0, "")
            metrics = {
                "peak_concurrency": result["peak_concurrency"],
                "end_to_end_ms": round((time.perf_counter() - started) * 1000, 2),
                "completed_nodes": len(result["completed"]),
                "skipped_nodes": len(result["skipped"]),
                "failed_nodes": len(result["failed"]),
            }
            self.repository.finish_run(run_id, result["status"], context, metrics)
            return {"run_id": run_id, **result, "metrics": metrics}
        except Exception as exc:
            self.repository.finish_run(run_id, "failed", context, {"error": str(exc)})
            raise
