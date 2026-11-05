from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook


EVENT_ALIASES = {
    "title": ["事件标题", "标题", "title"],
    "event_type": ["事件类型", "类型", "event_type"],
    "severity": ["等级", "事件等级", "severity"],
    "location": ["影响区域", "区域", "location"],
    "lat": ["纬度", "lat"],
    "lng": ["经度", "lng", "lon"],
    "affected_population": ["影响人口", "人口", "affected_population"],
    "description": ["事件描述", "描述", "description"],
    "source": ["来源", "source"],
    "source_ref": ["来源编号", "来源文件", "source_ref"],
    "closed_routes": ["封闭路线", "封闭路线编号", "closed_routes"],
}
DEMAND_ALIASES = {
    "material_code": ["物资编码", "编码", "material_code"],
    "material_name": ["物资名称", "名称", "material_name"],
    "quantity": ["需求量", "数量", "quantity"],
    "priority": ["优先级", "priority"],
    "deadline_hours": ["时限小时", "截止小时", "deadline_hours"],
}


def _canonical(value, aliases):
    text = str(value or "").strip()
    for key, names in aliases.items():
        if text in names or text.lower() in [name.lower() for name in names]:
            return key
    return text


def _rows_dict(headers, rows):
    canonical = [_canonical(header, {**EVENT_ALIASES, **DEMAND_ALIASES}) for header in headers]
    result = []
    for row in rows:
        if not row or not any(value not in (None, "") for value in row):
            continue
        result.append({canonical[index]: row[index] for index in range(min(len(canonical), len(row)))})
    return result


def parse_event_package(filename: str, payload: bytes) -> tuple[dict, list[dict]]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".json":
        data = json.loads(payload.decode("utf-8-sig"))
        event = data.get("event", data)
        demand = data.get("demand_items", data.get("demands", []))
        return event, demand
    if suffix in {".xlsx", ".xlsm"}:
        workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        event_sheet = next((sheet for sheet in workbook.worksheets if "事件" in sheet.title), workbook.worksheets[0])
        demand_sheet = next((sheet for sheet in workbook.worksheets if "需求" in sheet.title), None)
        event_values = list(event_sheet.iter_rows(values_only=True))
        if len(event_values) < 2:
            raise ValueError("事件信息工作表没有数据")
        event_rows = _rows_dict(event_values[0], event_values[1:2])
        demand = []
        if demand_sheet:
            demand_values = list(demand_sheet.iter_rows(values_only=True))
            if len(demand_values) >= 2:
                demand = _rows_dict(demand_values[0], demand_values[1:])
        return event_rows[0], demand
    if suffix == ".csv":
        text = payload.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = [{_canonical(k, EVENT_ALIASES): v for k, v in row.items()} for row in reader]
        if not rows:
            raise ValueError("CSV 没有事件数据")
        return rows[0], []
    raise ValueError("事件文件仅支持 JSON、CSV 或 XLSX")


def normalize_event(event: dict, demand: list[dict]) -> tuple[dict, list[dict]]:
    normalized = {
        "title": str(event.get("title") or event.get("事件标题") or "").strip(),
        "event_type": str(event.get("event_type") or event.get("类型") or "突发事件"),
        "severity": str(event.get("severity") or event.get("等级") or "III级"),
        "location": str(event.get("location") or event.get("区域") or "").strip(),
        "lat": _num_or_none(event.get("lat") or event.get("纬度")),
        "lng": _num_or_none(event.get("lng") or event.get("经度")),
        "affected_population": int(_num_or_none(event.get("affected_population") or event.get("人口")) or 0),
        "description": str(event.get("description") or event.get("描述") or ""),
        "source": str(event.get("source") or event.get("来源") or "file-import"),
        "source_ref": str(event.get("source_ref") or event.get("来源编号") or ""),
        "closed_routes": _split_list(event.get("closed_routes") or event.get("封闭路线")),
        "status": "confirmed",
        "id": f"EVT-{datetime.now():%Y%m%d%H%M%S}",
        "confirmed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    normalized_demand = []
    for item in demand or []:
        code = str(item.get("material_code") or item.get("物资编码") or "").strip()
        if not code:
            continue
        normalized_demand.append({
            "material_code": code,
            "material_name": str(item.get("material_name") or item.get("物资名称") or ""),
            "quantity": _num_or_none(item.get("quantity") or item.get("需求量")) or 0,
            "priority": str(item.get("priority") or item.get("优先级") or "P1").upper(),
            "deadline_hours": _num_or_none(item.get("deadline_hours") or item.get("时限小时")) or 12,
        })
    return normalized, normalized_demand


def _num_or_none(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _split_list(value):
    if isinstance(value, list):
        return value
    return [item.strip() for item in str(value or "").replace("，", ",").split(",") if item.strip()]
