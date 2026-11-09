from __future__ import annotations

import json
import os
from .database import Database, now_iso


class OperationsRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert_warehouse(self, data: dict) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO warehouses (id, name, region, x, y, lat, lng, capacity, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name, region=excluded.region, x=excluded.x, y=excluded.y,
                  lat=excluded.lat, lng=excluded.lng, capacity=excluded.capacity,
                  status=excluded.status, updated_at=excluded.updated_at
                """,
                (data["id"], data["name"], data.get("region", ""), data.get("x", 50), data.get("y", 50),
                 data.get("lat"), data.get("lng"), data.get("capacity", 0), data.get("status", "open"), now_iso()),
            )

    def upsert_destination(self, data: dict) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO destinations (id, name, address, x, y, lat, lng, population, contact)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name, address=excluded.address, x=excluded.x, y=excluded.y,
                  lat=excluded.lat, lng=excluded.lng, population=excluded.population, contact=excluded.contact
                """,
                (data["id"], data["name"], data.get("address", ""), data.get("x", 50), data.get("y", 50),
                 data.get("lat"), data.get("lng"), data.get("population", 0), data.get("contact", "")),
            )

    def upsert_vehicle(self, data: dict) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO vehicles
                (id, plate_no, vehicle_type, capacity_kg, temp_zone, warehouse_id, status,
                 lat, lng, last_position_at, gps_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  plate_no=excluded.plate_no, vehicle_type=excluded.vehicle_type,
                  capacity_kg=excluded.capacity_kg, temp_zone=excluded.temp_zone,
                  warehouse_id=excluded.warehouse_id, status=excluded.status,
                  lat=COALESCE(excluded.lat, vehicles.lat), lng=COALESCE(excluded.lng, vehicles.lng),
                  last_position_at=COALESCE(excluded.last_position_at, vehicles.last_position_at),
                  gps_source=CASE WHEN excluded.gps_source <> '' THEN excluded.gps_source ELSE vehicles.gps_source END
                """,
                (data["id"], data["plate_no"], data["vehicle_type"], data["capacity_kg"],
                 data.get("temp_zone", "常温"), data["warehouse_id"], data.get("status", "available"),
                 data.get("lat"), data.get("lng"), data.get("last_position_at"),
                 data.get("gps_source", "manual")),
            )

    def upsert_route(self, data: dict) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO routes
                (id, warehouse_id, destination_id, road_name, distance_km, eta_hours,
                 cost, temp_zone, is_primary, status, source, geometry_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  warehouse_id=excluded.warehouse_id, destination_id=excluded.destination_id,
                  road_name=excluded.road_name, distance_km=excluded.distance_km,
                  eta_hours=excluded.eta_hours, cost=excluded.cost, temp_zone=excluded.temp_zone,
                  is_primary=excluded.is_primary, status=excluded.status, source=excluded.source,
                  geometry_json=excluded.geometry_json
                """,
                (data["id"], data["warehouse_id"], data["destination_id"], data["road_name"],
                 data["distance_km"], data["eta_hours"], data["cost"], data.get("temp_zone", "常温"),
                 int(bool(data.get("is_primary", True))), data.get("status", "open"), data.get("source", "manual"),
                 json.dumps(data.get("geometry", []), ensure_ascii=False)),
            )

    def update_route_geometry(self, route_id: str, geometry: list[list[float]]) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE routes SET geometry_json = ? WHERE id = ?",
                (json.dumps(geometry, ensure_ascii=False), route_id),
            )
    def delete_route(self, route_id: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM routes WHERE id = ?", (route_id,)).fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM routes WHERE id = ?", (route_id,))
            return {"deleted": True, "route": dict(row)}

    def get_destination(self, destination_id: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM destinations WHERE id = ?", (destination_id,)).fetchone()
            return dict(row) if row else None

    def delete_destination(self, destination_id: str, force: bool = False) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM destinations WHERE id = ?", (destination_id,)).fetchone()
            if not row:
                return None
            route_count = conn.execute(
                "SELECT COUNT(*) AS c FROM routes WHERE destination_id = ?", (destination_id,)
            ).fetchone()["c"]
            if route_count and not force:
                return {"deleted": False, "destination": dict(row), "references": {"routes": route_count}}
            conn.execute("DELETE FROM routes WHERE destination_id = ?", (destination_id,))
            conn.execute("DELETE FROM destinations WHERE id = ?", (destination_id,))
            return {"deleted": True, "destination": dict(row), "references": {"routes": route_count}}

    def get_vehicle(self, vehicle_id: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
            return dict(row) if row else None

    def delete_vehicle(self, vehicle_id: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
            if not row:
                return None
            position_count = conn.execute(
                "SELECT COUNT(*) AS c FROM vehicle_positions WHERE vehicle_id = ?", (vehicle_id,)
            ).fetchone()["c"]
            conn.execute("DELETE FROM vehicle_positions WHERE vehicle_id = ?", (vehicle_id,))
            conn.execute("DELETE FROM vehicles WHERE id = ?", (vehicle_id,))
            return {"deleted": True, "vehicle": dict(row), "references": {"positions": position_count}}

    def get_warehouse(self, warehouse_id: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM warehouses WHERE id = ?", (warehouse_id,)).fetchone()
            return dict(row) if row else None

    def delete_warehouse(self, warehouse_id: str, force: bool = False) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM warehouses WHERE id = ?", (warehouse_id,)).fetchone()
            if not row:
                return None
            counts = {
                "routes": conn.execute(
                    "SELECT COUNT(*) AS c FROM routes WHERE warehouse_id = ?", (warehouse_id,)
                ).fetchone()["c"],
                "vehicles": conn.execute(
                    "SELECT COUNT(*) AS c FROM vehicles WHERE warehouse_id = ?", (warehouse_id,)
                ).fetchone()["c"],
                "inventory": conn.execute(
                    "SELECT COUNT(*) AS c FROM inventory WHERE warehouse_id = ?", (warehouse_id,)
                ).fetchone()["c"],
            }
            if any(counts.values()) and not force:
                return {"deleted": False, "warehouse": dict(row), "references": counts}
            conn.execute("DELETE FROM inventory WHERE warehouse_id = ?", (warehouse_id,))
            conn.execute("DELETE FROM routes WHERE warehouse_id = ?", (warehouse_id,))
            conn.execute(
                "DELETE FROM vehicle_positions WHERE vehicle_id IN (SELECT id FROM vehicles WHERE warehouse_id = ?)",
                (warehouse_id,),
            )
            conn.execute("DELETE FROM vehicles WHERE warehouse_id = ?", (warehouse_id,))
            conn.execute("DELETE FROM warehouses WHERE id = ?", (warehouse_id,))
            return {"deleted": True, "warehouse": dict(row), "references": counts}

    def delete_event(self, event_id: str, force: bool = False) -> dict | None:
        scenario_id = f"event-{event_id}"
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM operational_events WHERE id = ?", (event_id,)).fetchone()
            if not row:
                return None
            run_rows = conn.execute("SELECT id FROM runs WHERE scenario_id = ?", (scenario_id,)).fetchall()
            run_ids = [run["id"] for run in run_rows]
            task_count = 0
            agent_event_count = 0
            for run_id in run_ids:
                task_count += conn.execute(
                    "SELECT COUNT(*) AS c FROM dispatch_tasks WHERE run_id = ?", (run_id,)
                ).fetchone()["c"]
                agent_event_count += conn.execute(
                    "SELECT COUNT(*) AS c FROM agent_events WHERE run_id = ?", (run_id,)
                ).fetchone()["c"]
            counts = {"runs": len(run_ids), "tasks": task_count, "agent_events": agent_event_count}
            if run_ids and not force:
                return {"deleted": False, "event": dict(row), "references": counts}
            for run_id in run_ids:
                conn.execute("DELETE FROM dispatch_tasks WHERE run_id = ?", (run_id,))
                conn.execute("DELETE FROM agent_events WHERE run_id = ?", (run_id,))
                conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            conn.execute("DELETE FROM scenarios WHERE id = ?", (scenario_id,))
            conn.execute("DELETE FROM operational_events WHERE id = ?", (event_id,))
            return {"deleted": True, "event": dict(row), "references": counts}

    def add_event(self, data: dict) -> dict:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO operational_events
                (id, title, event_type, severity, location, lat, lng, affected_population,
                 status, source, source_ref, description, closed_routes, demand_json, confirmed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (data["id"], data["title"], data["event_type"], data["severity"], data["location"],
                 data.get("lat"), data.get("lng"), data.get("affected_population", 0),
                 data.get("status", "confirmed"), data.get("source", "manual"),
                 data.get("source_ref", ""), data.get("description", ""),
                 json.dumps(data.get("closed_routes", []), ensure_ascii=False),
                 json.dumps(data.get("demand_items", []), ensure_ascii=False),
                 data.get("confirmed_at", now_iso()), now_iso()),
            )
            row = conn.execute("SELECT * FROM operational_events WHERE id = ?", (data["id"],)).fetchone()
            item = dict(row)
            item["closed_routes"] = json.loads(item["closed_routes"])
            item["demand_items"] = json.loads(item.pop("demand_json", "[]") or "[]")
            return item

    def list_events(self, limit: int = 50) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM operational_events ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["closed_routes"] = json.loads(item["closed_routes"])
                item["demand_items"] = json.loads(item.pop("demand_json", "[]") or "[]")
                result.append(item)
            return result

    def get_event(self, event_id: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM operational_events WHERE id = ?", (event_id,)).fetchone()
            if not row:
                return None
            item = dict(row)
            item["closed_routes"] = json.loads(item["closed_routes"])
            item["demand_items"] = json.loads(item.pop("demand_json", "[]") or "[]")
            return item

    def ingest_vehicle_position(self, data: dict) -> dict:
        received_at = now_iso()
        reported_at = data.get("reported_at") or received_at
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO vehicle_positions
                (vehicle_id, lat, lng, speed_kmh, heading, accuracy_m, source, reported_at, received_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (data["vehicle_id"], data["lat"], data["lng"], data.get("speed_kmh"), data.get("heading"),
                 data.get("accuracy_m"), data.get("source", "gps"), reported_at, received_at),
            )
            conn.execute(
                "UPDATE vehicles SET lat=?, lng=?, last_position_at=?, gps_source=? WHERE id=?",
                (data["lat"], data["lng"], reported_at, data.get("source", "gps"), data["vehicle_id"]),
            )
            row = conn.execute("SELECT * FROM vehicles WHERE id = ?", (data["vehicle_id"],)).fetchone()
            return dict(row)

    def vehicle_history(self, vehicle_id: str, limit: int = 100) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM vehicle_positions WHERE vehicle_id = ? ORDER BY received_at DESC LIMIT ?",
                (vehicle_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def replace_tasks(self, run_id: str, plan: dict) -> list[dict]:
        created_at = now_iso()
        with self.db.connect() as conn:
            conn.execute("DELETE FROM dispatch_tasks WHERE run_id = ?", (run_id,))
            for group in plan.get("groups", []):
                payload = json.dumps({
                    "route_id": group.get("route_id"), "route_name": group.get("route_name"),
                    "vehicle_trips": group.get("vehicle_trips"), "weight_kg": group.get("weight_kg"),
                }, ensure_ascii=False)
                conn.execute(
                    """
                    INSERT INTO dispatch_tasks
                    (run_id, task_type, title, status, assignee, due_at, payload_json, created_at, updated_at)
                    VALUES (?, 'transport', ?, 'pending', '', NULL, ?, ?, ?)
                    """,
                    (run_id, f"{group['warehouse_name']} → {group['destination_name']} 运输任务",
                     payload, created_at, created_at),
                )
            rows = conn.execute("SELECT * FROM dispatch_tasks WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json") or "{}")
                result.append(item)
            return result

    def list_tasks(self, run_id: str | None = None) -> list[dict]:
        with self.db.connect() as conn:
            if run_id:
                rows = conn.execute("SELECT * FROM dispatch_tasks WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM dispatch_tasks ORDER BY created_at DESC LIMIT 100").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                result.append(item)
            return result

    def update_task(self, task_id: int, status: str, assignee: str = "") -> dict | None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE dispatch_tasks SET status=?, assignee=?, updated_at=? WHERE id=?",
                (status, assignee, now_iso(), task_id),
            )
            row = conn.execute("SELECT * FROM dispatch_tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                return None
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            return item

    def readiness(self) -> dict:
        with self.db.connect() as conn:
            counts = {}
            for table in ("materials", "warehouses", "destinations", "vehicles", "routes", "inventory",
                          "operational_events", "vehicle_positions"):
                counts[table] = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            gps_count = conn.execute(
                "SELECT COUNT(*) AS c FROM vehicles WHERE last_position_at IS NOT NULL AND gps_source NOT IN ('warehouse','manual')"
            ).fetchone()["c"]
        return {
            "mode": "operational" if os.getenv("FARMMATCH_MODE", "demo") == "operational" else "demo",
            "counts": counts,
            "gps_vehicles": gps_count,
            "event_source": "manual / webhook-ready",
            "vehicle_source": "import / GPS-position-ready",
            "map_provider": "Leaflet + AMap-compatible tiles (no key; OSM configurable)",
            "route_provider": "internal / OSRM-ready",
        }

    def ensure_operational_scenario(self, event: dict) -> dict:
        scenario_id = f"event-{event['id']}"
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO scenarios
                (id, name, description, event_type, severity, location, affected_population,
                 closed_routes, default_demand, risk_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name, description=excluded.description,
                  event_type=excluded.event_type, severity=excluded.severity,
                  location=excluded.location, affected_population=excluded.affected_population,
                  closed_routes=excluded.closed_routes, risk_notes=excluded.risk_notes
                """,
                (scenario_id, event["title"], event.get("description", ""), event["event_type"],
                 event["severity"], event["location"], event.get("affected_population", 0),
                 json.dumps(event.get("closed_routes", []), ensure_ascii=False),
                 f"真实事件来源：{event.get('source', 'manual')}"),
            )
        return self.db.get_scenario(scenario_id)
