from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend" / "farmmatch.db"
OUTPUT = ROOT / "backend" / "demo" / "farmmatch-demo.db"
SELECTED_RUNS = (
    "RUN-20261117-115022-58E6",
    "RUN-20261114-210325-F769",
)
SENSITIVE_KEYS = ("password", "api_key", "authorization", "secret", "token")


def scrub(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if any(marker in key.lower() for marker in SENSITIVE_KEYS):
                result[key] = ""
            else:
                result[key] = scrub(item)
        return result
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def scrub_json(connection: sqlite3.Connection, table: str, column: str) -> None:
    rows = connection.execute(f"SELECT rowid, {column} FROM {table}").fetchall()
    for rowid, raw in rows:
        try:
            cleaned = scrub(json.loads(raw or "{}"))
            connection.execute(
                f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                (json.dumps(cleaned, ensure_ascii=False), rowid),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            pass


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"source database not found: {SOURCE}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    if OUTPUT.exists():
        OUTPUT.unlink()

    source = sqlite3.connect(SOURCE)
    target = sqlite3.connect(OUTPUT)
    try:
        source.backup(target)
        target.row_factory = sqlite3.Row
        target.execute("PRAGMA foreign_keys = OFF")
        marks = ",".join("?" for _ in SELECTED_RUNS)
        params = SELECTED_RUNS

        target.execute("DELETE FROM auth_sessions")
        target.execute("DELETE FROM users")
        target.execute("DELETE FROM login_attempts")
        target.execute("DELETE FROM audit_logs")
        target.execute("DELETE FROM memory_cleanup_audit")
        target.execute("DELETE FROM evaluation_runs")
        target.execute("DELETE FROM retry_jobs")
        target.execute("DELETE FROM workflow_node_runs")
        target.execute("DELETE FROM workflow_runs")
        target.execute("DELETE FROM operational_events")
        target.execute(f"DELETE FROM tool_calls WHERE session_id NOT IN ({marks})", params)
        target.execute(f"DELETE FROM agent_events WHERE run_id NOT IN ({marks})", params)
        target.execute(f"DELETE FROM a2a_messages WHERE run_id NOT IN ({marks})", params)
        target.execute(f"DELETE FROM dispatch_tasks WHERE run_id NOT IN ({marks})", params)
        target.execute(f"DELETE FROM inventory_reservations WHERE run_id NOT IN ({marks})", params)
        target.execute(f"DELETE FROM llm_calls WHERE run_id NOT IN ({marks})", params)
        target.execute(f"DELETE FROM agent_performance WHERE run_id NOT IN ({marks}) AND run_id <> ''", params)
        target.execute(f"DELETE FROM runs WHERE id NOT IN ({marks})", params)
        target.execute(f"UPDATE agent_memory SET source_run_id = '' WHERE source_run_id NOT IN ({marks})", params)
        target.execute("UPDATE agent_versions SET endpoint = ''")
        target.execute("UPDATE tool_definitions SET endpoint = '' WHERE kind <> 'builtin'")

        for table, column in (
            ("runs", "request_json"),
            ("runs", "result_json"),
            ("agent_events", "payload_json"),
            ("a2a_messages", "content_json"),
            ("tool_calls", "arguments_json"),
            ("tool_calls", "result_json"),
            ("agent_memory", "value_json"),
        ):
            scrub_json(target, table, column)

        target.commit()
        target.execute("VACUUM")
        target.commit()

        counts = {
            table: target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "runs", "llm_calls", "agent_events", "tool_calls", "a2a_messages",
                "agent_memory", "dispatch_tasks", "inventory_reservations",
                "agent_performance", "workflow_definitions", "agent_registry",
                "knowledge_documents", "knowledge_chunks", "inventory_snapshots",
                "inventory", "users", "auth_sessions", "login_attempts", "audit_logs",
            )
        }
        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        manifest = {
            "selected_runs": list(SELECTED_RUNS),
            "counts": counts,
            "integrity": integrity,
            "database_bytes": OUTPUT.stat().st_size,
        }
        (OUTPUT.parent / "demo_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        if integrity != "ok":
            raise SystemExit("demo database integrity check failed")
        if counts["users"] or counts["auth_sessions"]:
            raise SystemExit("demo database still contains user/session data")
        for run_id in SELECTED_RUNS:
            if not target.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone():
                raise SystemExit(f"selected run missing: {run_id}")
    finally:
        target.close()
        source.close()


if __name__ == "__main__":
    main()