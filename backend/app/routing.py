from __future__ import annotations

import asyncio
import math

import httpx

from .config import OSRM_BASE_URL, OSRM_TIMEOUT


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)
    value = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _fallback_route(warehouse: dict, destination: dict) -> dict:
    distance = round(haversine_km(warehouse["lat"], warehouse["lng"], destination["lat"], destination["lng"]) * 1.26, 2)
    eta_hours = round(distance / 32 + 0.25, 2)
    return {
        "id": f"R-{warehouse['id']}-{destination['id']}-FALLBACK",
        "warehouse_id": warehouse["id"],
        "destination_id": destination["id"],
        "road_name": f"{warehouse['name']} → {destination['name']}（离线路网估算）",
        "distance_km": max(distance, 0.1),
        "eta_hours": max(eta_hours, 0.1),
        "cost": round(180 + distance * 12 + eta_hours * 80, 2),
        "temp_zone": "常温",
        "is_primary": True,
        "status": "open",
        "source": "fallback",
    }


async def _fetch_osrm_route(client: httpx.AsyncClient, warehouse: dict, destination: dict) -> dict:
    coordinates = f"{warehouse['lng']},{warehouse['lat']};{destination['lng']},{destination['lat']}"
    response = await client.get(
        f"{OSRM_BASE_URL}/route/v1/driving/{coordinates}",
        params={"overview": "false", "alternatives": "false", "steps": "true", "annotations": "false"},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RuntimeError(payload.get("message") or "OSRM 未返回可用路线")
    route = payload["routes"][0]
    distance = round(float(route["distance"]) / 1000, 2)
    eta_hours = round(float(route["duration"]) / 3600 + 0.25, 2)
    legs = route.get("legs") or [{}]
    summary = str(legs[0].get("summary") or "").strip()
    road_name = f"{warehouse['name']} → {destination['name']}"
    if summary:
        road_name += f"（{summary}）"
    return {
        "id": f"R-{warehouse['id']}-{destination['id']}-OSRM",
        "warehouse_id": warehouse["id"],
        "destination_id": destination["id"],
        "road_name": road_name,
        "distance_km": max(distance, 0.1),
        "eta_hours": max(eta_hours, 0.1),
        "cost": round(180 + distance * 12 + eta_hours * 80, 2),
        "temp_zone": "常温",
        "is_primary": True,
        "status": "open",
        "source": "osrm",
    }


async def generate_route_candidates(warehouses: list[dict], destinations: list[dict]) -> tuple[list[dict], list[str]]:
    semaphore = asyncio.Semaphore(4)

    async def one(warehouse: dict, destination: dict) -> tuple[dict, str | None]:
        async with semaphore:
            try:
                return await _fetch_osrm_route(client, warehouse, destination), None
            except Exception as exc:
                return _fallback_route(warehouse, destination), f"{warehouse['name']} → {destination['name']}：OSRM 调用失败，已使用离线估算（{exc}）"

    headers = {"User-Agent": "FarmMatch-Emergency-Dispatch/1.0"}
    async with httpx.AsyncClient(timeout=OSRM_TIMEOUT, headers=headers) as client:
        results = await asyncio.gather(*[
            one(warehouse, destination)
            for warehouse in warehouses
            for destination in destinations
        ])
    routes = [item[0] for item in results]
    warnings = [item[1] for item in results if item[1]]
    return routes, warnings
