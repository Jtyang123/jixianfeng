from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import DATABASE_URL, DB_PATH, DEMO_INVENTORY_MULTIPLIER, SEED_DEMO


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


class _PgCursor:
    def __init__(self, cursor) -> None:
        self._cursor = cursor

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def __iter__(self):
        return iter(self._cursor)


class _PgConnection:
    def __init__(self, connection) -> None:
        self._connection = connection

    def execute(self, sql: str, params=()):
        if isinstance(params, dict):
            sql = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"%(\1)s", sql)
        else:
            sql = sql.replace("?", "%s")
        return _PgCursor(self._connection.execute(sql, params))

    def executemany(self, sql: str, seq_of_params):
        sql = sql.replace("?", "%s")
        cursor = self._connection.cursor()
        cursor.executemany(sql, seq_of_params)
        return _PgCursor(cursor)

    def executescript(self, script: str) -> None:
        script = script.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        for statement in [part.strip() for part in script.split(";") if part.strip()]:
            self._connection.execute(statement)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


class Database:
    def __init__(self, path: Path | str = DB_PATH, database_url: str | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.database_url = (database_url if database_url is not None else DATABASE_URL).strip()
        self.engine = "postgresql" if self.database_url else "sqlite"

    @contextmanager
    def connect(self):
        if self.engine == "postgresql":
            import psycopg
            from psycopg.rows import dict_row
            conn = _PgConnection(psycopg.connect(self.database_url, row_factory=dict_row))
        else:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _insert_id(self, conn, sql: str, params=()) -> int:
        if self.engine == "postgresql":
            row = conn.execute(sql + " RETURNING id", params).fetchone()
            return int(row["id"])
        cursor = conn.execute(sql, params)
        return int(cursor.lastrowid)

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS materials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    aliases TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '其他',
                    specification TEXT NOT NULL DEFAULT '',
                    base_unit TEXT NOT NULL DEFAULT '件',
                    package_unit TEXT NOT NULL DEFAULT '',
                    conversion_rate REAL NOT NULL DEFAULT 1,
                    storage_type TEXT NOT NULL DEFAULT '常温',
                    shelf_life_days INTEGER,
                    is_cold_chain INTEGER NOT NULL DEFAULT 0,
                    is_hazardous INTEGER NOT NULL DEFAULT 0,
                    unit_weight_kg REAL NOT NULL DEFAULT 0.5,
                    default_priority TEXT NOT NULL DEFAULT 'P1',
                    source TEXT NOT NULL DEFAULT 'manual',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS warehouses (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    region TEXT NOT NULL,
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    lat REAL,
                    lng REAL,
                    capacity REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'open',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS destinations (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    address TEXT NOT NULL DEFAULT '',
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    lat REAL,
                    lng REAL,
                    population INTEGER NOT NULL DEFAULT 0,
                    contact TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS inventory_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'seed',
                    status TEXT NOT NULL DEFAULT 'active',
                    row_count INTEGER NOT NULL DEFAULT 0,
                    uploaded_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS inventory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER NOT NULL,
                    warehouse_id TEXT NOT NULL,
                    material_id INTEGER NOT NULL,
                    quantity REAL NOT NULL,
                    unit TEXT NOT NULL,
                    batch_no TEXT NOT NULL DEFAULT '',
                    expiry_date TEXT,
                    locked_quantity REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'available',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (snapshot_id) REFERENCES inventory_snapshots(id),
                    FOREIGN KEY (warehouse_id) REFERENCES warehouses(id),
                    FOREIGN KEY (material_id) REFERENCES materials(id)
                );

                CREATE TABLE IF NOT EXISTS inventory_reservations (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    inventory_id INTEGER NOT NULL,
                    warehouse_id TEXT NOT NULL,
                    material_id INTEGER NOT NULL,
                    quantity REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    released_at TEXT,
                    UNIQUE(run_id, inventory_id),
                    FOREIGN KEY (inventory_id) REFERENCES inventory(id)
                );

                CREATE INDEX IF NOT EXISTS idx_inventory_reservations_run
                ON inventory_reservations(run_id, status);
                CREATE INDEX IF NOT EXISTS idx_inventory_reservations_expiry
                ON inventory_reservations(status, expires_at);

                CREATE TABLE IF NOT EXISTS vehicles (
                    id TEXT PRIMARY KEY,
                    plate_no TEXT NOT NULL,
                    vehicle_type TEXT NOT NULL,
                    capacity_kg REAL NOT NULL,
                    temp_zone TEXT NOT NULL DEFAULT '常温',
                    warehouse_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'available',
                    lat REAL,
                    lng REAL,
                    last_position_at TEXT,
                    gps_source TEXT NOT NULL DEFAULT 'warehouse',
                    FOREIGN KEY (warehouse_id) REFERENCES warehouses(id)
                );

                CREATE TABLE IF NOT EXISTS routes (
                    id TEXT PRIMARY KEY,
                    warehouse_id TEXT NOT NULL,
                    destination_id TEXT NOT NULL,
                    road_name TEXT NOT NULL,
                    distance_km REAL NOT NULL,
                    eta_hours REAL NOT NULL,
                    cost REAL NOT NULL,
                    temp_zone TEXT NOT NULL DEFAULT '常温',
                    is_primary INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'open',
                    source TEXT NOT NULL DEFAULT 'manual',
                    geometry_json TEXT NOT NULL DEFAULT '[]',
                    FOREIGN KEY (warehouse_id) REFERENCES warehouses(id),
                    FOREIGN KEY (destination_id) REFERENCES destinations(id)
                );

                CREATE TABLE IF NOT EXISTS scenarios (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    location TEXT NOT NULL,
                    lat REAL,
                    lng REAL,
                    affected_population INTEGER NOT NULL,
                    closed_routes TEXT NOT NULL DEFAULT '[]',
                    default_demand TEXT NOT NULL DEFAULT '[]',
                    risk_notes TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    scenario_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    queued_at TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    priority_score REAL NOT NULL DEFAULT 0,
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_expires_at TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    last_error TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    approved INTEGER NOT NULL DEFAULT 0,
                    approved_at TEXT,
                    approved_by INTEGER,
                    approved_by_name TEXT NOT NULL DEFAULT '',
                    approval_note TEXT NOT NULL DEFAULT '',
                    reserved_at TEXT,
                    FOREIGN KEY (scenario_id) REFERENCES scenarios(id)
                );


                CREATE TABLE IF NOT EXISTS agent_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE (run_id, seq),
                    FOREIGN KEY (run_id) REFERENCES runs(id)
                );

                CREATE INDEX IF NOT EXISTS idx_events_run_seq
                ON agent_events(run_id, seq);

                CREATE TABLE IF NOT EXISTS operational_events (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    location TEXT NOT NULL,
                    lat REAL,
                    lng REAL,
                    affected_population INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'confirmed',
                    source TEXT NOT NULL DEFAULT 'manual',
                    source_ref TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    closed_routes TEXT NOT NULL DEFAULT '[]',
                    demand_json TEXT NOT NULL DEFAULT '[]',
                    confirmed_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS vehicle_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vehicle_id TEXT NOT NULL,
                    lat REAL NOT NULL,
                    lng REAL NOT NULL,
                    speed_kmh REAL,
                    heading REAL,
                    accuracy_m REAL,
                    source TEXT NOT NULL DEFAULT 'gps',
                    reported_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
                );

                CREATE TABLE IF NOT EXISTS dispatch_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    assignee TEXT NOT NULL DEFAULT '',
                    due_at TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_vehicle_positions_vehicle_time
                ON vehicle_positions(vehicle_id, received_at DESC);

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT 'viewer',
                    password_hash TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_hash TEXT UNIQUE NOT NULL,
                    user_id INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    ip TEXT NOT NULL DEFAULT '',
                    user_agent TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL DEFAULT '',
                    resource_id TEXT NOT NULL DEFAULT '',
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    status_code INTEGER,
                    ip TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token_hash);
                CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at DESC);

                CREATE TABLE IF NOT EXISTS login_attempts (
                    attempt_key TEXT PRIMARY KEY,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    window_started_at TEXT NOT NULL,
                    locked_until TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_registry (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    capabilities_json TEXT NOT NULL DEFAULT '[]',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    endpoint TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    prompt_version TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'inactive',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    activated_at TEXT,
                    UNIQUE(agent_id, version),
                    FOREIGN KEY (agent_id) REFERENCES agent_registry(id)
                );

                CREATE INDEX IF NOT EXISTS idx_agent_versions_agent ON agent_versions(agent_id, status);

                CREATE TABLE IF NOT EXISTS agent_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    run_id TEXT NOT NULL DEFAULT '',
                    operation TEXT NOT NULL DEFAULT 'invoke',
                    success INTEGER NOT NULL DEFAULT 0,
                    latency_ms REAL NOT NULL DEFAULT 0,
                    error_type TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_agent_performance_agent
                ON agent_performance(agent_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS agent_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    memory_key TEXT NOT NULL,
                    value_json TEXT NOT NULL DEFAULT '{}',
                    confidence REAL NOT NULL DEFAULT 0.5,
                    source_run_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(agent_id, memory_key)
                );

                CREATE INDEX IF NOT EXISTS idx_agent_memory_agent ON agent_memory(agent_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS knowledge_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '其他',
                    source TEXT NOT NULL DEFAULT 'upload',
                    status TEXT NOT NULL DEFAULT 'active',
                    content_hash TEXT NOT NULL DEFAULT '',
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    seq INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    keywords TEXT NOT NULL DEFAULT '',
                    embedding_json TEXT NOT NULL DEFAULT '[]',
                    embedding_provider TEXT NOT NULL DEFAULT 'hash',
                    embedding_model TEXT NOT NULL DEFAULT 'domain-hash-v1',
                    embedding_dim INTEGER NOT NULL DEFAULT 256,
                    page_no INTEGER,
                    section TEXT NOT NULL DEFAULT '',
                    char_start INTEGER NOT NULL DEFAULT 0,
                    char_end INTEGER NOT NULL DEFAULT 0,
                    security_flags_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document ON knowledge_chunks(document_id, seq);                CREATE TABLE IF NOT EXISTS workflow_definitions (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    description TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL DEFAULT 'post_verification',
                    definition_json TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    is_template INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workflow_runs (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS workflow_node_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_run_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    queued_at TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    wait_ms REAL NOT NULL DEFAULT 0,
                    execution_ms REAL NOT NULL DEFAULT 0,
                    output_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow ON workflow_runs(workflow_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_workflow_node_runs_run ON workflow_node_runs(workflow_run_id, id);

                CREATE TABLE IF NOT EXISTS tool_definitions (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'builtin',
                    endpoint TEXT NOT NULL DEFAULT '',
                    method TEXT NOT NULL DEFAULT 'POST',
                    input_schema_json TEXT NOT NULL DEFAULT '{}',
                    config_json TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tool_calls (
                    id TEXT PRIMARY KEY,
                    tool_id TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    arguments_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    latency_ms REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tool_calls_tool ON tool_calls(tool_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS retry_jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    run_id TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    next_attempt_at TEXT NOT NULL,
                    last_error TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_retry_jobs_due
                ON retry_jobs(status, next_attempt_at);

                CREATE TABLE IF NOT EXISTS evaluation_runs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    scenario_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'queued',
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_evaluation_runs_created ON evaluation_runs(created_at DESC);
                """
            )
            self._ensure_geo_columns(conn)
            material_count = conn.execute("SELECT COUNT(*) AS c FROM materials").fetchone()["c"]
            if material_count == 0 and SEED_DEMO:
                self._seed(conn)
            self._ensure_vehicle_columns(conn)
            self._ensure_scenario_columns(conn)
            self._ensure_event_columns(conn)
            self._ensure_route_columns(conn)
            self._ensure_knowledge_columns(conn)
            self._ensure_document_columns(conn)
            self._ensure_workflow_columns(conn)
            self._ensure_run_columns(conn)
            self._ensure_demo_shortage_scenario(conn)

    def reset_demo(self) -> None:
        with self.connect() as conn:
            for table in [
                "agent_memory", "retry_jobs", "inventory_reservations",
                "workflow_node_runs", "workflow_runs", "agent_events", "runs", "inventory", "inventory_snapshots",
                "vehicles", "routes", "scenarios", "destinations", "warehouses", "materials",
            ]:
                conn.execute(f"DELETE FROM {table}")
            if self.engine == "sqlite":
                conn.execute("DELETE FROM sqlite_sequence")
            self._seed(conn)

    def _ensure_geo_columns(self, conn: sqlite3.Connection) -> None:
        for table in ("warehouses", "destinations"):
            if self.engine == "postgresql":
                columns = {row["column_name"] for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = ?", (table,)
                ).fetchall()}
            else:
                columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "lat" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN lat REAL")
            if "lng" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN lng REAL")
            if table == "destinations" and "address" not in columns:
                conn.execute("ALTER TABLE destinations ADD COLUMN address TEXT NOT NULL DEFAULT ''")
        warehouse_coords = {
            "WH-A": (30.6110, 114.3030), "WH-B": (30.5050, 114.3970),
            "WH-C": (30.5530, 114.2180), "WH-D": (30.5440, 114.3430),
        }
        destination_coords = {
            "DST-01": (30.5728, 114.3073), "DST-02": (30.4920, 114.4100),
            "DST-03": (30.5530, 114.2600),
        }
        for wh_id, (lat, lng) in warehouse_coords.items():
            conn.execute("UPDATE warehouses SET lat = ?, lng = ? WHERE id = ? AND (lat IS NULL OR lng IS NULL)", (lat, lng, wh_id))
        for dst_id, (lat, lng) in destination_coords.items():
            conn.execute("UPDATE destinations SET lat = ?, lng = ? WHERE id = ? AND (lat IS NULL OR lng IS NULL)", (lat, lng, dst_id))

    def _ensure_knowledge_columns(self, conn) -> None:
        if self.engine == "postgresql":
            columns = {row["column_name"] for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?", ("knowledge_chunks",)
            ).fetchall()}
        else:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge_chunks)").fetchall()}
        additions = {
            "embedding_json": "TEXT NOT NULL DEFAULT '[]'",
            "security_flags_json": "TEXT NOT NULL DEFAULT '[]'",
            "embedding_provider": "TEXT NOT NULL DEFAULT 'hash'",
            "embedding_model": "TEXT NOT NULL DEFAULT 'domain-hash-v1'",
            "embedding_dim": "INTEGER NOT NULL DEFAULT 256",
            "page_no": "INTEGER",
            "section": "TEXT NOT NULL DEFAULT ''",
            "char_start": "INTEGER NOT NULL DEFAULT 0",
            "char_end": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE knowledge_chunks ADD COLUMN {name} {sql_type}")

    def _ensure_run_columns(self, conn) -> None:
        if self.engine == "postgresql":
            columns = {row["column_name"] for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?", ("runs",)
            ).fetchall()}
        else:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        additions = {
            "queued_at": "TEXT",
            "started_at": "TEXT",
            "priority_score": "REAL NOT NULL DEFAULT 0",
            "lease_owner": "TEXT NOT NULL DEFAULT ''",
            "lease_expires_at": "TEXT",
            "attempts": "INTEGER NOT NULL DEFAULT 0",
            "max_attempts": "INTEGER NOT NULL DEFAULT 3",
            "last_error": "TEXT NOT NULL DEFAULT ''",
            "idempotency_key": "TEXT NOT NULL DEFAULT ''",
            "approved_by": "INTEGER",
            "approved_by_name": "TEXT NOT NULL DEFAULT ''",
            "reserved_at": "TEXT",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {sql_type}")
        conn.execute("UPDATE runs SET queued_at = created_at WHERE queued_at IS NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_queue ON runs(status, priority_score DESC, queued_at)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_idempotency ON runs(idempotency_key) WHERE idempotency_key <> ''")

    def _ensure_document_columns(self, conn) -> None:
        if self.engine == "postgresql":
            columns = {row["column_name"] for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?", ("knowledge_documents",)
            ).fetchall()}
        else:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge_documents)").fetchall()}
        if "content_hash" not in columns:
            conn.execute("ALTER TABLE knowledge_documents ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_documents_hash ON knowledge_documents(content_hash) WHERE content_hash <> ''")

    def _ensure_workflow_columns(self, conn) -> None:
        if self.engine == "postgresql":
            columns = {row["column_name"] for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?", ("workflow_definitions",)
            ).fetchall()}
        else:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(workflow_definitions)").fetchall()}
        if "is_template" not in columns:
            conn.execute("ALTER TABLE workflow_definitions ADD COLUMN is_template INTEGER NOT NULL DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_definitions_template ON workflow_definitions(is_template, stage)")

    def _ensure_scenario_columns(self, conn) -> None:
        if self.engine == "postgresql":
            columns = {row["column_name"] for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?", ("scenarios",)
            ).fetchall()}
        else:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(scenarios)").fetchall()}
        if "lat" not in columns:
            conn.execute("ALTER TABLE scenarios ADD COLUMN lat REAL")
        if "lng" not in columns:
            conn.execute("ALTER TABLE scenarios ADD COLUMN lng REAL")
        conn.execute("UPDATE scenarios SET lat = 30.5960, lng = 114.3100 WHERE id = 'rainstorm-ii' AND (lat IS NULL OR lng IS NULL)")
        conn.execute("UPDATE scenarios SET lat = 30.5500, lng = 114.3050 WHERE id = 'heatwave-iii' AND (lat IS NULL OR lng IS NULL)")
    def _ensure_event_columns(self, conn) -> None:
        if self.engine == "postgresql":
            columns = {row["column_name"] for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?", ("operational_events",)
            ).fetchall()}
        else:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(operational_events)").fetchall()}
        if "demand_json" not in columns:
            conn.execute("ALTER TABLE operational_events ADD COLUMN demand_json TEXT NOT NULL DEFAULT '[]'")
        duplicate_runs = conn.execute(
            """SELECT run_id FROM agent_events GROUP BY run_id, seq
               HAVING COUNT(*) > 1 LIMIT 100"""
        ).fetchall()
        for duplicate in duplicate_runs:
            run_id = duplicate["run_id"]
            rows = conn.execute(
                "SELECT id FROM agent_events WHERE run_id=? ORDER BY seq, id", (run_id,)
            ).fetchall()
            for index, row in enumerate(rows, start=1):
                conn.execute("UPDATE agent_events SET seq=? WHERE id=?", (index, row["id"]))
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_run_seq_unique ON agent_events(run_id, seq)"
        )

    def _ensure_vehicle_columns(self, conn) -> None:
        if self.engine == "postgresql":
            columns = {row["column_name"] for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?", ("vehicles",)
            ).fetchall()}
        else:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(vehicles)").fetchall()}
        additions = {
            "lat": "REAL", "lng": "REAL", "last_position_at": "TEXT",
            "gps_source": "TEXT NOT NULL DEFAULT 'warehouse'",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE vehicles ADD COLUMN {name} {sql_type}")
        conn.execute(
            """
            UPDATE vehicles
            SET lat = (SELECT w.lat FROM warehouses w WHERE w.id = vehicles.warehouse_id),
                lng = (SELECT w.lng FROM warehouses w WHERE w.id = vehicles.warehouse_id),
                gps_source = COALESCE(NULLIF(gps_source, ''), 'warehouse')
            WHERE lat IS NULL OR lng IS NULL OR gps_source IS NULL OR gps_source = ''
            """
        )

    def _ensure_demo_shortage_scenario(self, conn) -> None:
        if not SEED_DEMO:
            return
        existing = conn.execute("SELECT id FROM scenarios WHERE id = 'shortage-ii'").fetchone()
        if existing:
            return
        demand = [
            {"material_code": "MAT-WATER-001", "quantity": 60000, "priority": "P0", "deadline_hours": 6},
            {"material_code": "MAT-MED-001", "quantity": 900, "priority": "P0", "deadline_hours": 8},
            {"material_code": "MAT-GEN-001", "quantity": 60, "priority": "P1", "deadline_hours": 12},
        ]
        conn.execute(
            """
            INSERT INTO scenarios
            (id, name, description, event_type, severity, location, lat, lng,
             affected_population, closed_routes, default_demand, risk_notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "shortage-ii", "库存缺口与跨仓压力演练",
                "道路正常但库存总量不足，用于演示跨仓拆分、缺口识别和转人工研判。",
                "物资短缺", "II级", "武汉市·模拟多片区", 30.5800, 114.3100,
                15000, "[]", _json(demand),
                "该场景刻意设置超过库存上限的需求，预期经过多轮匹配后转人工研判。",
            ),
        )

    def _ensure_route_columns(self, conn) -> None:
        if self.engine == "postgresql":
            columns = {row["column_name"] for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?", ("routes",)
            ).fetchall()}
        else:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(routes)").fetchall()}
        if "source" not in columns:
            conn.execute("ALTER TABLE routes ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")
        if "geometry_json" not in columns:
            conn.execute("ALTER TABLE routes ADD COLUMN geometry_json TEXT NOT NULL DEFAULT '[]'")

    def _seed(self, conn: sqlite3.Connection) -> None:
        created = now_iso()
        materials = [
            ("MAT-WATER-001", "瓶装饮用水", "饮用水,矿泉水,水", "生活保障", "550ml/瓶", "瓶", "箱", 24, "常温", 365, 0, 0, 0.55, "P0"),
            ("MAT-FOOD-001", "自热方便食品", "方便食品,自热饭,食品", "生活保障", "280g/盒", "盒", "箱", 12, "常温", 365, 0, 0, 0.45, "P0"),
            ("MAT-TENT-001", "12㎡救灾帐篷", "帐篷,安置帐篷", "安置保障", "12㎡/顶", "顶", "件", 1, "常温", 1825, 0, 0, 18.0, "P1"),
            ("MAT-BLANKET-001", "应急棉被", "棉被,被褥", "安置保障", "1.5m×2m/床", "床", "包", 10, "常温", 1825, 0, 0, 2.0, "P1"),
            ("MAT-GEN-001", "5kW汽油发电机", "发电机,供电设备", "电力设备", "5kW/台", "台", "台", 1, "常温", 3650, 0, 0, 75.0, "P1"),
            ("MAT-MED-001", "急救药品包", "急救包,药品包,常用药品", "医疗救护", "基础急救包/包", "包", "箱", 20, "阴凉", 730, 0, 0, 2.0, "P0"),
            ("MAT-LIGHT-001", "应急照明灯", "照明灯,应急灯", "电力设备", "LED 20W/盏", "盏", "箱", 12, "常温", 3650, 0, 0, 0.8, "P1"),
            ("MAT-MASK-001", "医用防护口罩", "口罩,防护口罩", "医疗救护", "N95/只", "只", "箱", 500, "常温", 1095, 0, 0, 0.02, "P1"),
            ("MAT-PUMP-001", "移动排水泵", "排水泵,抽水泵", "抢险设备", "380V/台", "台", "台", 1, "常温", 3650, 0, 1, 55.0, "P1"),
            ("MAT-DISINFECT-001", "消毒泡腾片", "消毒片,消毒用品", "卫生防疫", "100片/瓶", "瓶", "箱", 60, "常温", 730, 0, 0, 0.2, "P2"),
            ("MAT-HYGIENE-001", "应急卫生包", "卫生包,洗漱包", "生活保障", "1套/包", "包", "箱", 20, "常温", 1095, 0, 0, 3.0, "P2"),
            ("MAT-RAINCOAT-001", "分体式雨衣", "雨衣,雨具", "抢险装备", "均码/套", "套", "箱", 50, "常温", 1825, 0, 0, 0.6, "P2"),
        ]
        conn.executemany(
            """
            INSERT INTO materials
            (code, name, aliases, category, specification, base_unit, package_unit,
             conversion_rate, storage_type, shelf_life_days, is_cold_chain,
             is_hazardous, unit_weight_kg, default_priority, source, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'builtin', 'active', ?)
            """,
            [(*m, created) for m in materials],
        )
        warehouses = [
            ("WH-A", "江岸中心库", "江岸片区", 22, 28, 30.6110, 114.3030, 20000, "open", created),
            ("WH-B", "洪山储备库", "洪山片区", 70, 66, 30.5050, 114.3970, 16000, "open", created),
            ("WH-C", "汉阳协议仓", "汉阳片区", 27, 76, 30.5530, 114.2180, 12000, "open", created),
            ("WH-D", "武昌应急仓", "武昌片区", 74, 30, 30.5440, 114.3430, 14000, "open", created),
        ]
        conn.executemany(
            "INSERT INTO warehouses (id, name, region, x, y, lat, lng, capacity, status, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            warehouses,
        )
        destinations = [
            ("DST-01", "江城体育馆安置点", 50, 47, 30.5728, 114.3073, 4200, "现场联络员 A"),
            ("DST-02", "南湖学校安置点", 65, 79, 30.4920, 114.4100, 3600, "现场联络员 B"),
            ("DST-03", "汉江社区安置点", 24, 56, 30.5530, 114.2600, 4200, "现场联络员 C"),
        ]
        conn.executemany(
            "INSERT INTO destinations (id, name, x, y, lat, lng, population, contact) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            destinations,
        )
        route_rows: list[tuple[Any, ...]] = []
        for wh_id, wh_name, _, wx, wy, _, _, *_ in warehouses:
            for dst_id, dst_name, dx, dy, _, _, *_ in destinations:
                distance = round(math.dist((wx, wy), (dx, dy)) * 0.58, 1)
                eta = round(0.35 + distance / 38, 2)
                base_cost = round(180 + distance * 16, 2)
                primary_id = f"R-{wh_id}-{dst_id}-P"
                alternate_id = f"R-{wh_id}-{dst_id}-A"
                route_rows.append((primary_id, wh_id, dst_id, f"{wh_name}→{dst_name} 主通道", distance, eta, base_cost, "常温", 1, "open"))
                route_rows.append((alternate_id, wh_id, dst_id, f"{wh_name}→{dst_name} 备用通道", round(distance * 1.28, 1), round(eta * 1.42 + 0.18, 2), round(base_cost * 1.16, 2), "常温", 0, "open"))
        conn.executemany(
            """
            INSERT INTO routes
            (id, warehouse_id, destination_id, road_name, distance_km, eta_hours,
             cost, temp_zone, is_primary, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            route_rows,
        )
        vehicles: list[tuple[Any, ...]] = []
        for wh_id, *_ in warehouses:
            for index in range(1, 4):
                vehicles.append((
                    f"V-{wh_id[-1]}-{index:02d}", f"鄂A·{wh_id[-1]}{index:02d}应急",
                    "4.2米厢式货车" if index < 3 else "6.8米高栏货车",
                    4200 if index < 3 else 8000, "常温", wh_id, "available",
                ))
        conn.executemany(
            "INSERT INTO vehicles (id, plate_no, vehicle_type, capacity_kg, temp_zone, warehouse_id, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            vehicles,
        )

        snapshot_id = self._insert_id(
            conn,
            "INSERT INTO inventory_snapshots (label, source, status, row_count, uploaded_at) VALUES (?, 'seed', 'active', 0, ?)",
            ("初始演练数据", created),
        )
        material_rows = conn.execute("SELECT id, code, base_unit FROM materials").fetchall()
        material_ids = {row["code"]: row["id"] for row in material_rows}
        base_units = {row["code"]: row["base_unit"] for row in material_rows}
        inventory_by_material = {
            "MAT-WATER-001": {"WH-A": 9000, "WH-B": 7000, "WH-C": 5000, "WH-D": 6000},
            "MAT-FOOD-001": {"WH-A": 3000, "WH-B": 2200, "WH-C": 1800, "WH-D": 1600},
            "MAT-TENT-001": {"WH-A": 80, "WH-B": 70, "WH-C": 50, "WH-D": 60},
            "MAT-BLANKET-001": {"WH-A": 300, "WH-B": 220, "WH-C": 140, "WH-D": 180},
            "MAT-GEN-001": {"WH-A": 8, "WH-B": 7, "WH-C": 5, "WH-D": 4},
            "MAT-MED-001": {"WH-A": 120, "WH-B": 90, "WH-C": 70, "WH-D": 80},
            "MAT-LIGHT-001": {"WH-A": 100, "WH-B": 80, "WH-C": 70, "WH-D": 60},
            "MAT-MASK-001": {"WH-A": 4000, "WH-B": 3000, "WH-C": 2500, "WH-D": 3000},
            "MAT-PUMP-001": {"WH-A": 8, "WH-B": 4, "WH-C": 2, "WH-D": 3},
            "MAT-DISINFECT-001": {"WH-A": 500, "WH-B": 300, "WH-C": 220, "WH-D": 260},
            "MAT-HYGIENE-001": {"WH-A": 180, "WH-B": 120, "WH-C": 100, "WH-D": 140},
            "MAT-RAINCOAT-001": {"WH-A": 800, "WH-B": 500, "WH-C": 300, "WH-D": 450},
        }
        inventory_rows: list[tuple[Any, ...]] = []
        for material_code, per_warehouse in inventory_by_material.items():
            material_id = material_ids[material_code]
            for wh_id, quantity in per_warehouse.items():
                quantity = round(quantity * DEMO_INVENTORY_MULTIPLIER, 2)
                unit = base_units[material_code]
                inventory_rows.append((
                    snapshot_id, wh_id, material_id, quantity, unit,
                    f"BATCH-{material_code[-3:]}-{wh_id[-1]}",
                    (datetime.now() + timedelta(days=540)).date().isoformat(),
                    0, "available", created,
                ))
        conn.executemany(
            """
            INSERT INTO inventory
            (snapshot_id, warehouse_id, material_id, quantity, unit, batch_no,
             expiry_date, locked_quantity, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            inventory_rows,
        )
        conn.execute("UPDATE inventory_snapshots SET row_count = ? WHERE id = ?", (len(inventory_rows), snapshot_id))

        primary_closures = [row[0] for row in route_rows if str(row[0]).endswith("-P")]
        rain_demand = [
            {"material_code": "MAT-WATER-001", "quantity": 12000, "priority": "P0", "deadline_hours": 6},
            {"material_code": "MAT-FOOD-001", "quantity": 6000, "priority": "P0", "deadline_hours": 8},
            {"material_code": "MAT-MED-001", "quantity": 300, "priority": "P0", "deadline_hours": 6},
            {"material_code": "MAT-TENT-001", "quantity": 180, "priority": "P1", "deadline_hours": 12},
            {"material_code": "MAT-GEN-001", "quantity": 16, "priority": "P1", "deadline_hours": 10},
            {"material_code": "MAT-MASK-001", "quantity": 6000, "priority": "P1", "deadline_hours": 8},
        ]
        heat_demand = [
            {"material_code": "MAT-WATER-001", "quantity": 9000, "priority": "P0", "deadline_hours": 8},
            {"material_code": "MAT-MED-001", "quantity": 180, "priority": "P0", "deadline_hours": 8},
            {"material_code": "MAT-GEN-001", "quantity": 10, "priority": "P1", "deadline_hours": 12},
            {"material_code": "MAT-LIGHT-001", "quantity": 120, "priority": "P1", "deadline_hours": 12},
        ]
        scenarios = [
            (
                "rainstorm-ii", "武汉暴雨洪涝应急演练",
                "主通道多处积水封闭，检验多仓协同、独立核验和自动重规划能力。",
                "暴雨洪涝", "II级", "武汉市·模拟沿江区", 30.5960, 114.3100, 12000,
                _json(primary_closures), _json(rain_demand),
                "本场景为主通道全部封闭的压力测试，第一轮方案应被独立核验智能体退回。",
            ),
            (
                "heatwave-iii", "持续高温生活保障推演",
                "道路正常，重点检验时效和成本多目标权衡。",
                "高温热浪", "III级", "武汉市·模拟中心城区", 30.5500, 114.3050, 9000,
                "[]", _json(heat_demand), "适合展示正常条件下的多方案对比。",
            ),
        ]
        conn.executemany(
            """
            INSERT INTO scenarios
            (id, name, description, event_type, severity, location, lat, lng,
             affected_population, closed_routes, default_demand, risk_notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            scenarios,
        )

    def list_materials(self, search: str = "", category: str = "") -> list[dict]:
        sql = "SELECT * FROM materials WHERE status = 'active'"
        params: list[Any] = []
        if search:
            sql += " AND (name LIKE ? OR aliases LIKE ? OR code LIKE ?)"
            term = f"%{search}%"
            params.extend([term, term, term])
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY category, default_priority, name"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def get_material(self, material_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
            return dict(row) if row else None

    def get_material_by_code(self, code: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM materials WHERE code = ?", (code,)).fetchone()
            return dict(row) if row else None

    def find_material(self, value: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM materials WHERE code = ? OR name = ? OR aliases LIKE ? LIMIT 1",
                (value, value, f"%{value}%"),
            ).fetchone()
            return dict(row) if row else None

    def create_material(self, data: dict) -> dict:
        values = {
            "aliases": "", "category": "其他", "specification": "", "base_unit": "件",
            "package_unit": "", "conversion_rate": 1, "storage_type": "常温",
            "shelf_life_days": None, "is_cold_chain": 0, "is_hazardous": 0,
            "unit_weight_kg": 0.5, "default_priority": "P1", "source": "manual",
            "status": "active",
        }
        values.update(data)
        values["is_cold_chain"] = int(bool(values["is_cold_chain"]))
        values["is_hazardous"] = int(bool(values["is_hazardous"]))
        values["created_at"] = now_iso()
        with self.connect() as conn:
            material_id = self._insert_id(
                conn,
                """
                INSERT INTO materials
                (code, name, aliases, category, specification, base_unit, package_unit,
                 conversion_rate, storage_type, shelf_life_days, is_cold_chain,
                 is_hazardous, unit_weight_kg, default_priority, source, status, created_at)
                VALUES (:code, :name, :aliases, :category, :specification, :base_unit,
                        :package_unit, :conversion_rate, :storage_type, :shelf_life_days,
                        :is_cold_chain, :is_hazardous, :unit_weight_kg, :default_priority,
                        :source, :status, :created_at)
                """,
                values,
            )
            row = conn.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
            return dict(row)

    def upsert_imported_material(self, data: dict) -> tuple[dict, bool]:
        existing = self.get_material_by_code(data["code"])
        if existing:
            with self.connect() as conn:
                conn.execute(
                    """
                    UPDATE materials SET name=?, aliases=?, category=?, specification=?,
                    base_unit=?, package_unit=?, conversion_rate=?, storage_type=?,
                    shelf_life_days=?, is_cold_chain=?, is_hazardous=?, unit_weight_kg=?,
                    default_priority=?, source='import', status='active'
                    WHERE id=?
                    """,
                    (
                        data["name"], data.get("aliases", ""), data.get("category", "其他"),
                        data.get("specification", ""), data.get("base_unit", "件"),
                        data.get("package_unit", ""), data.get("conversion_rate", 1),
                        data.get("storage_type", "常温"), data.get("shelf_life_days"),
                        int(bool(data.get("is_cold_chain", False))),
                        int(bool(data.get("is_hazardous", False))),
                        data.get("unit_weight_kg", 0.5),
                        data.get("default_priority", "P1"), existing["id"],
                    ),
                )
            return self.get_material(existing["id"]), True
        return self.create_material(data), False

    def warehouse_rows(self) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM warehouses ORDER BY id")]

    def destination_rows(self) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM destinations ORDER BY id")]

    def route_rows(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM routes ORDER BY warehouse_id, destination_id, is_primary DESC").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                try:
                    item["geometry"] = json.loads(item.pop("geometry_json", "[]") or "[]")
                except (TypeError, json.JSONDecodeError):
                    item["geometry"] = []
                result.append(item)
            return result

    def vehicle_rows(self) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM vehicles ORDER BY warehouse_id, id")]

    def latest_snapshot(self) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM inventory_snapshots ORDER BY id DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    def inventory_rows(self, snapshot_id: int | None = None) -> list[dict]:
        with self.connect() as conn:
            if snapshot_id is None:
                snapshot = conn.execute("SELECT id FROM inventory_snapshots ORDER BY id DESC LIMIT 1").fetchone()
                if not snapshot:
                    return []
                snapshot_id = snapshot["id"]
            rows = conn.execute(
                """
                SELECT i.*, m.code AS material_code, m.name AS material_name,
                       m.category, m.default_priority, m.unit_weight_kg,
                       m.is_cold_chain, m.is_hazardous, w.name AS warehouse_name
                FROM inventory i
                JOIN materials m ON m.id = i.material_id
                JOIN warehouses w ON w.id = i.warehouse_id
                WHERE i.snapshot_id = ?
                ORDER BY w.id, m.category, m.name
                """,
                (snapshot_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_inventory_snapshot(self, rows: list[dict], label: str, source: str = "upload") -> dict:
        uploaded_at = now_iso()
        with self.connect() as conn:
            conn.execute("UPDATE inventory_snapshots SET status = 'archived' WHERE status = 'active'")
            snapshot_id = self._insert_id(
                conn,
                "INSERT INTO inventory_snapshots (label, source, status, row_count, uploaded_at) VALUES (?, ?, 'active', ?, ?)",
                (label, source, len(rows), uploaded_at),
            )
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO inventory
                    (snapshot_id, warehouse_id, material_id, quantity, unit, batch_no,
                     expiry_date, locked_quantity, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        snapshot_id, row["warehouse_id"], row["material_id"], row["quantity"],
                        row["unit"], row.get("batch_no", ""), row.get("expiry_date"),
                        row.get("status", "available"), uploaded_at,
                    ),
                )
            snapshot = conn.execute("SELECT * FROM inventory_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
            return dict(snapshot)

    def inventory_summary(self) -> list[dict]:
        with self.connect() as conn:
            snapshot = conn.execute("SELECT id FROM inventory_snapshots ORDER BY id DESC LIMIT 1").fetchone()
            if not snapshot:
                return []
            rows = conn.execute(
                """
                SELECT m.id AS material_id, m.code, m.name, m.category, m.base_unit,
                       SUM(i.quantity) AS total_quantity,
                       SUM(CASE WHEN i.expiry_date IS NULL OR i.expiry_date >= CURRENT_DATE
                                THEN i.quantity ELSE 0 END) AS usable_quantity,
                       SUM(i.locked_quantity) AS locked_quantity,
                       SUM(CASE WHEN i.expiry_date IS NULL OR i.expiry_date >= CURRENT_DATE
                                THEN CASE WHEN i.quantity > i.locked_quantity THEN i.quantity - i.locked_quantity ELSE 0 END ELSE 0 END) AS available_quantity,
                       COUNT(DISTINCT i.warehouse_id) AS warehouse_count,
                       MAX(i.updated_at) AS updated_at
                FROM inventory i
                JOIN materials m ON m.id = i.material_id
                WHERE i.snapshot_id = ? AND i.status = 'available'
                GROUP BY m.id
                ORDER BY m.category, m.name
                """,
                (snapshot["id"],),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_active_reservations(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.id, r.run_id, r.warehouse_id, w.name AS warehouse_name,
                       r.material_id, m.code AS material_code, m.name AS material_name,
                       m.base_unit, r.quantity, r.status, r.expires_at, r.created_at,
                       runs.status AS run_status
                FROM inventory_reservations r
                JOIN materials m ON m.id = r.material_id
                LEFT JOIN warehouses w ON w.id = r.warehouse_id
                LEFT JOIN runs ON runs.id = r.run_id
                WHERE r.status = 'active'
                ORDER BY r.expires_at, r.run_id, w.id, m.name
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def scenario_rows(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM scenarios ORDER BY id").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["closed_routes"] = json.loads(item["closed_routes"])
                item["default_demand"] = json.loads(item["default_demand"])
                result.append(item)
            return result

    def get_scenario(self, scenario_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
            if not row:
                return None
            item = dict(row)
            item["closed_routes"] = json.loads(item["closed_routes"])
            item["default_demand"] = json.loads(item["default_demand"])
            return item

    def create_evaluation_run(self, run_id: str, kind: str, scenario_id: str = "") -> dict:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO evaluation_runs
                   (id, kind, scenario_id, status, created_at) VALUES (?, ?, ?, 'running', ?)""",
                (run_id, kind, scenario_id, now_iso()),
            )
            row = conn.execute("SELECT * FROM evaluation_runs WHERE id = ?", (run_id,)).fetchone()
            return dict(row)

    def finish_evaluation_run(self, run_id: str, status: str, result: dict) -> dict:
        summary = {
            "status": result.get("status"),
            "duration_ms": result.get("duration_ms", 0),
            "concurrency_levels": len(result.get("concurrency", [])),
            "fault_cases": len(result.get("fault_injection", [])),
            "all_passed": result.get("all_passed", False),
        }
        with self.connect() as conn:
            conn.execute(
                """UPDATE evaluation_runs
                   SET status = ?, summary_json = ?, result_json = ?, completed_at = ?
                   WHERE id = ?""",
                (status, _json(summary), _json(result), now_iso(), run_id),
            )
            row = conn.execute("SELECT * FROM evaluation_runs WHERE id = ?", (run_id,)).fetchone()
            return self._evaluation_row(row)

    def get_evaluation_run(self, run_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM evaluation_runs WHERE id = ?", (run_id,)).fetchone()
            return self._evaluation_row(row) if row else None

    def list_evaluation_runs(self, limit: int = 20) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evaluation_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._evaluation_row(row) for row in rows]

    @staticmethod
    def _evaluation_row(row) -> dict:
        item = dict(row)
        item["summary"] = json.loads(item.pop("summary_json") or "{}")
        item["result"] = json.loads(item.pop("result_json") or "{}")
        return item
    def create_run(
        self,
        run_id: str,
        scenario_id: str,
        request_payload: dict,
        *,
        priority_score: float = 0.0,
        max_attempts: int = 3,
        idempotency_key: str = '',
    ) -> dict:
        now = now_iso()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO runs
                   (id, scenario_id, status, created_at, queued_at, request_json,
                    priority_score, max_attempts, idempotency_key)
                   VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?)""",
                (run_id, scenario_id, now, now, _json(request_payload), priority_score, max_attempts, idempotency_key),
            )
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            return dict(row)

    def get_run_by_idempotency(self, idempotency_key: str) -> dict | None:
        if not idempotency_key:
            return None
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM runs WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        return self.get_run(row["id"]) if row else None

    def update_run(self, run_id: str, status: str, result: dict | None = None) -> None:
        with self.connect() as conn:
            if result is None:
                conn.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
            else:
                conn.execute(
                    """UPDATE runs
                       SET status=?, result_json=?, completed_at=?, lease_owner='', lease_expires_at=NULL
                       WHERE id=?""",
                    (status, _json(result), now_iso(), run_id),
                )

    def record_agent_performance(
        self, agent_id: str, run_id: str, operation: str, success: bool,
        latency_ms: float, error_type: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO agent_performance
                   (agent_id, run_id, operation, success, latency_ms, error_type, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (agent_id, run_id, operation, 1 if success else 0, round(float(latency_ms), 2), error_type[:160], now_iso()),
            )

    def agent_performance_summary(self) -> dict[str, dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT agent_id, COUNT(*) AS samples, SUM(success) AS successes,
                          AVG(latency_ms) AS average_latency_ms
                   FROM agent_performance GROUP BY agent_id"""
            ).fetchall()
            error_rows = conn.execute(
                """SELECT agent_id, error_type, COUNT(*) AS count
                   FROM agent_performance WHERE error_type <> ''
                   GROUP BY agent_id, error_type"""
            ).fetchall()
        errors: dict[str, dict[str, int]] = defaultdict(dict)
        for row in error_rows:
            errors[row["agent_id"]][row["error_type"]] = int(row["count"])
        result = {}
        for row in rows:
            samples = int(row["samples"] or 0)
            successes = int(row["successes"] or 0)
            result[row["agent_id"]] = {
                "historical_samples": samples,
                "historical_success_rate": round(successes / samples, 4) if samples else 0.0,
                "historical_average_latency_ms": round(float(row["average_latency_ms"] or 0), 2),
                "historical_error_types": errors.get(row["agent_id"], {}),
            }
        return result

    def set_run_status(self, run_id: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))

    def add_event(self, run_id: str, event_type: str, agent_id: str, agent_name: str, title: str, detail: str = "", payload: dict | None = None) -> dict:
        for attempt in range(8):
            try:
                with self.connect() as conn:
                    seq = conn.execute(
                        "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM agent_events WHERE run_id = ?",
                        (run_id,),
                    ).fetchone()["seq"]
                    created_at = now_iso()
                    event_id = self._insert_id(
                        conn,
                        """
                        INSERT INTO agent_events
                        (run_id, seq, type, agent_id, agent_name, title, detail, payload_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (run_id, seq, event_type, agent_id, agent_name, title, detail, _json(payload or {}), created_at),
                    )
                    return {
                        "id": event_id, "run_id": run_id, "seq": seq,
                        "type": event_type, "agent_id": agent_id, "agent_name": agent_name,
                        "title": title, "detail": detail, "payload": payload or {},
                        "created_at": created_at,
                    }
            except Exception as exc:
                message = str(exc).lower()
                if "unique" not in message and "duplicate key" not in message:
                    raise
                if attempt >= 7:
                    raise
                time.sleep(0.005 * (attempt + 1))
        raise RuntimeError("failed to allocate event sequence")

    def events_after(self, run_id: str, after_seq: int = 0) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_events WHERE run_id = ? AND seq > ? ORDER BY seq",
                (run_id, after_seq),
            ).fetchall()
            events = []
            for row in rows:
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                events.append(item)
            return events

    def get_run(self, run_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if not row:
                return None
            item = dict(row)
            item["request"] = json.loads(item.pop("request_json"))
            raw_result = item.pop("result_json")
            item["result"] = json.loads(raw_result) if raw_result else None
            return item

    def list_runs(self, limit: int = 20) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT id, scenario_id, status, created_at, queued_at, started_at, completed_at,
                          priority_score, attempts, max_attempts, last_error, idempotency_key,
                          approved, approved_at, approved_by, approved_by_name, approval_note, reserved_at
                   FROM runs ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def approve_run(self, run_id: str, note: str = "", user: dict | None = None) -> dict | None:
        user = user or {}
        with self.connect() as conn:
            conn.execute(
                """UPDATE runs
                   SET approved=1, approved_at=?, approved_by=?, approved_by_name=?, approval_note=?
                   WHERE id=?""",
                (now_iso(), user.get("id"), user.get("display_name") or user.get("username", ""), note, run_id),
            )
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            return dict(row) if row else None

    def dashboard(self) -> dict:
        snapshot = self.latest_snapshot()
        inventory_rows = self.inventory_rows(snapshot["id"] if snapshot else None)
        with self.connect() as conn:
            material_count = conn.execute("SELECT COUNT(*) AS c FROM materials WHERE status='active'").fetchone()["c"]
            warehouse_count = conn.execute("SELECT COUNT(*) AS c FROM warehouses").fetchone()["c"]
            vehicle_count = conn.execute("SELECT COUNT(*) AS c FROM vehicles WHERE status='available'").fetchone()["c"]
            run_count = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
        return {
            "materials": material_count, "warehouses": warehouse_count,
            "vehicles": vehicle_count, "runs": run_count,
            "inventory_records": len(inventory_rows),
            "inventory_units": sum(row["quantity"] for row in inventory_rows),
            "snapshot": snapshot,
        }
