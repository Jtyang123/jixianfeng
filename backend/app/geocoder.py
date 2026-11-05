from __future__ import annotations

import httpx

from .config import AMAP_WEB_SERVICE_KEY


class GeocoderError(RuntimeError):
    pass


async def geocode_address(address: str) -> dict:
    address = address.strip()
    if not address:
        raise GeocoderError("地址不能为空")
    if not AMAP_WEB_SERVICE_KEY:
        raise GeocoderError("未配置 AMAP_WEB_SERVICE_KEY，无法解析地址；可以手工填写经纬度")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                "https://restapi.amap.com/v3/geocode/geo",
                params={"key": AMAP_WEB_SERVICE_KEY, "address": address, "output": "JSON"},
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        raise GeocoderError(f"地理编码服务调用失败：{exc}") from exc
    if data.get("status") != "1" or not data.get("geocodes"):
        raise GeocoderError(data.get("info") or "未找到该地址")
    item = data["geocodes"][0]
    lng, lat = [float(value) for value in item["location"].split(",")]
    return {
        "address": address,
        "formatted_address": item.get("formatted_address", address),
        "lat": lat,
        "lng": lng,
        "level": item.get("level", ""),
        "source": "amap",
    }
