from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from .database import Database, now_iso


class AgentRegistryError(ValueError):
    pass


class AgentRegistryService:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _public_agent(row: dict, versions: list[dict], metrics: dict) -> dict:
        active = next((item for item in versions if item["status"] == "active"), None)
        return {
            "id": row["id"],
            "name": row["name"],
            "role": row["role"],
            "description": row.get("description", ""),
            "enabled": bool(row.get("enabled", 1)),
            "capabilities": row.get("capabilities", []),
            "active_version": active["version"] if active else None,
            "endpoint_configured": bool(active and active.get("endpoint")),
            "model": active.get("model", "") if active else "",
            "prompt_version": active.get("prompt_version", "") if active else "",
            "versions": versions,
            "metrics": metrics,
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def sync_builtin_agents(self, agents: list[dict], endpoints: dict[str, str] | None = None) -> None:
        endpoints = endpoints or {}
        with self.db.connect() as conn:
            for agent in agents:
                agent_id = agent["id"]
                now = now_iso()
                existing = conn.execute("SELECT id FROM agent_registry WHERE id = ?", (agent_id,)).fetchone()
                capabilities = agent.get("skills") or []
                if not existing:
                    conn.execute(
                        """INSERT INTO agent_registry
                           (id, name, role, description, capabilities_json, enabled, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                        (agent_id, agent["name"], agent_id, agent.get("role", ""), __import__("json").dumps(capabilities, ensure_ascii=False), now, now),
                    )
                else:
                    conn.execute(
                        "UPDATE agent_registry SET name=?, description=?, capabilities_json=?, updated_at=? WHERE id=?",
                        (agent["name"], agent.get("role", ""), __import__("json").dumps(capabilities, ensure_ascii=False), now, agent_id),
                    )
                version = agent.get("version") or "v1"
                version_row = conn.execute(
                    "SELECT id FROM agent_versions WHERE agent_id = ? AND version = ?", (agent_id, version)
                ).fetchone()
                endpoint = endpoints.get(agent_id, "")
                if not version_row:
                    conn.execute(
                        """INSERT INTO agent_versions
                           (agent_id, version, endpoint, model, prompt_version, status, metrics_json, created_at, activated_at)
                           VALUES (?, ?, ?, ?, ?, 'active', '{}', ?, ?)""",
                        (agent_id, version, endpoint, agent.get("model", ""), version, now, now),
                    )
                elif endpoint:
                    conn.execute(
                        "UPDATE agent_versions SET endpoint=?, model=?, prompt_version=? WHERE agent_id=? AND version=?",
                        (endpoint, agent.get("model", ""), version, agent_id, version),
                    )

    def list_agents(self) -> list[dict]:
        metrics = self.runtime_metrics()
        with self.db.connect() as conn:
            agents = conn.execute("SELECT * FROM agent_registry ORDER BY id").fetchall()
            version_rows = conn.execute("SELECT * FROM agent_versions ORDER BY id").fetchall()
            versions_by_agent: dict[str, list[dict]] = defaultdict(list)
            for row in version_rows:
                item = dict(row)
                item["metrics"] = __import__("json").loads(item.pop("metrics_json", "{}") or "{}")
                versions_by_agent[item["agent_id"]].append(item)
            result = []
            for row in agents:
                item = dict(row)
                item["capabilities"] = __import__("json").loads(item.pop("capabilities_json", "[]") or "[]")
                result.append(self._public_agent(item, versions_by_agent.get(item["id"], []), metrics.get(item["id"], {})))
            return result

    def discover(self, role: str | None = None) -> list[dict]:
        agents = self.list_agents()
        return [item for item in agents if item["enabled"] and (not role or item["role"] == role)]

    def active_endpoints(self) -> dict[str, str]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT r.id, v.endpoint FROM agent_registry r
                   JOIN agent_versions v ON v.agent_id = r.id
                   WHERE r.enabled = 1 AND v.status = 'active' AND v.endpoint <> ''"""
            ).fetchall()
            return {row["id"]: row["endpoint"] for row in rows}

    def register_agent(self, payload: dict) -> dict:
        agent_id = payload["id"].strip()
        version = payload["version"].strip()
        now = now_iso()
        with self.db.connect() as conn:
            if conn.execute("SELECT id FROM agent_registry WHERE id = ?", (agent_id,)).fetchone():
                raise AgentRegistryError("Agent ID 已存在")
            conn.execute(
                """INSERT INTO agent_registry
                   (id, name, role, description, capabilities_json, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                (agent_id, payload["name"].strip(), payload["role"].strip(), payload.get("description", ""),
                 __import__("json").dumps(payload.get("capabilities", []), ensure_ascii=False), now, now),
            )
            self._insert_version(conn, agent_id, version, payload, activate=True)
        return next(item for item in self.list_agents() if item["id"] == agent_id)

    def add_version(self, agent_id: str, payload: dict) -> dict:
        now = now_iso()
        with self.db.connect() as conn:
            if not conn.execute("SELECT id FROM agent_registry WHERE id = ?", (agent_id,)).fetchone():
                raise AgentRegistryError("Agent 不存在")
            if payload.get("activate"):
                conn.execute("UPDATE agent_versions SET status='retired' WHERE agent_id = ? AND status = 'active'", (agent_id,))
            self._insert_version(conn, agent_id, payload["version"].strip(), payload, activate=bool(payload.get("activate")))
            conn.execute("UPDATE agent_registry SET updated_at=? WHERE id=?", (now, agent_id))
        return next(item for item in self.list_agents() if item["id"] == agent_id)

    def _insert_version(self, conn, agent_id: str, version: str, payload: dict, activate: bool) -> None:
        now = now_iso()
        try:
            conn.execute(
                """INSERT INTO agent_versions
                   (agent_id, version, endpoint, model, prompt_version, status, metrics_json, created_at, activated_at)
                   VALUES (?, ?, ?, ?, ?, ?, '{}', ?, ?)""",
                (agent_id, version, payload.get("endpoint", ""), payload.get("model", ""),
                 payload.get("prompt_version", ""), "active" if activate else "inactive", now,
                 now if activate else None),
            )
        except Exception as exc:
            raise AgentRegistryError("该 Agent 版本已存在") from exc

    def activate_version(self, agent_id: str, version: str) -> dict:
        with self.db.connect() as conn:
            exists = conn.execute(
                "SELECT id FROM agent_versions WHERE agent_id = ? AND version = ?", (agent_id, version)
            ).fetchone()
            if not exists:
                raise AgentRegistryError("Agent 版本不存在")
            conn.execute("UPDATE agent_versions SET status='retired' WHERE agent_id = ?", (agent_id,))
            conn.execute(
                "UPDATE agent_versions SET status='active', activated_at=? WHERE agent_id = ? AND version = ?",
                (now_iso(), agent_id, version),
            )
            conn.execute("UPDATE agent_registry SET updated_at=? WHERE id=?", (now_iso(), agent_id))
        return next(item for item in self.list_agents() if item["id"] == agent_id)

    def runtime_metrics(self) -> dict[str, dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT run_id, agent_id, type, created_at
                   FROM agent_events
                   WHERE type IN ('agentarts.called','agentarts.completed','agentarts.fallback','agentarts.conflict')
                   ORDER BY id DESC LIMIT 2000"""
            ).fetchall()
        timed: dict[tuple[str, str], datetime] = {}
        durations: dict[str, list[float]] = defaultdict(list)
        calls: dict[str, int] = defaultdict(int)
        success: dict[str, int] = defaultdict(int)
        fallback: dict[str, int] = defaultdict(int)
        pending: dict[str, int] = defaultdict(int)
        for row in reversed(rows):
            item = dict(row)
            key = (item["run_id"], item["agent_id"])
            try:
                when = datetime.fromisoformat(item["created_at"])
            except Exception:
                continue
            if item["type"] == "agentarts.called":
                timed[key] = when
                calls[item["agent_id"]] += 1
                pending[item["agent_id"]] += 1
            elif item["type"] in {"agentarts.completed", "agentarts.fallback", "agentarts.conflict"}:
                start = timed.pop(key, None)
                if start and item["type"] != "agentarts.conflict":
                    durations[item["agent_id"]].append(max(0, (when - start).total_seconds() * 1000))
                pending[item["agent_id"]] = max(0, pending[item["agent_id"]] - 1)
                if item["type"] == "agentarts.completed":
                    success[item["agent_id"]] += 1
                elif item["type"] == "agentarts.fallback":
                    fallback[item["agent_id"]] += 1
        result = {}
        for agent_id in set(calls) | set(success) | set(fallback):
            avg_latency = sum(durations[agent_id]) / len(durations[agent_id]) if durations[agent_id] else 0
            completed = success[agent_id] + fallback[agent_id]
            failure_rate = fallback[agent_id] / completed if completed else 0
            load_score = min(100, round(avg_latency / 200 + failure_rate * 40 + pending[agent_id] * 10))
            result[agent_id] = {
                "sample_count": calls[agent_id],
                "average_latency_ms": round(avg_latency),
                "success_rate": round(1 - failure_rate, 4),
                "failure_rate": round(failure_rate, 4),
                "pending_calls": pending[agent_id],
                "load_score": load_score,
            }
        return result


def apply_agent_load(schedule: dict, metrics: dict[str, dict]) -> dict:
    dispatch = []
    for item in schedule.get("dispatch", []):
        copy = dict(item)
        load = metrics.get(copy["agent_id"], {})
        penalty = min(25, round(float(load.get("average_latency_ms", 0)) / 800 + float(load.get("failure_rate", 0)) * 20 + float(load.get("pending_calls", 0)) * 5))
        copy["base_priority"] = copy.get("priority", 0)
        copy["load_penalty"] = penalty
        copy["effective_priority"] = max(1, copy["base_priority"] - penalty)
        copy["load_score"] = load.get("load_score", 0)
        dispatch.append(copy)
    dispatch.sort(key=lambda item: item["effective_priority"], reverse=True)
    enriched = dict(schedule)
    enriched["dispatch"] = dispatch
    enriched["engine"] = "load-aware capability-weighted DAG scheduler"
    enriched["agent_load"] = metrics
    return enriched
