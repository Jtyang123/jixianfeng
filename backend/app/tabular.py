from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


HEADER_ALIASES = {
    "code": ["物资编码", "编码", "material_code", "code", "物料编码"],
    "name": ["物资名称", "名称", "material_name", "name", "物料名称"],
    "aliases": ["别名", "aliases", "alias"],
    "category": ["分类", "物资分类", "category"],
    "specification": ["规格", "规格型号", "specification", "spec"],
    "base_unit": ["基本单位", "单位", "base_unit", "unit"],
    "package_unit": ["包装单位", "package_unit"],
    "conversion_rate": ["包装换算", "换算率", "conversion_rate"],
    "storage_type": ["储存条件", "storage_type"],
    "shelf_life_days": ["保质期天数", "保质期", "shelf_life_days"],
    "is_cold_chain": ["是否冷链", "冷链", "is_cold_chain"],
    "is_hazardous": ["是否危险品", "危险品", "is_hazardous"],
    "unit_weight_kg": ["单件重量kg", "单件重量", "unit_weight_kg"],
    "default_priority": ["默认优先级", "优先级", "default_priority"],
    "warehouse_code": ["仓库编码", "仓库编号", "warehouse_code", "warehouse_id"],
    "warehouse_name": ["仓库名称", "warehouse_name"],
    "quantity": ["数量", "库存数量", "quantity"],
    "batch_no": ["批次号", "批次", "batch_no"],
    "expiry_date": ["有效期", "到期日期", "expiry_date"],
    "status": ["状态", "status"],
    "unit": ["库存单位", "单位", "unit"],
    "id": ["编码", "编号", "ID", "id"],
    "region": ["区域", "地区", "region"],
    "lat": ["纬度", "lat", "latitude"],
    "lng": ["经度", "lng", "lon", "longitude"],
    "capacity": ["容量", "capacity"],
    "vehicle_id": ["车辆编号", "车辆ID", "vehicle_id", "vehicle_code"],
    "plate_no": ["车牌号", "车牌", "plate_no", "plate"],
    "vehicle_type": ["车型", "车辆类型", "vehicle_type"],
    "capacity_kg": ["载重kg", "载重", "capacity_kg"],
    "temp_zone": ["温区", "温度要求", "temp_zone"],
    "destination_id": ["安置点编码", "目的地编码", "destination_id", "destination_code"],
    "road_name": ["路线名称", "道路名称", "road_name"],
    "distance_km": ["距离km", "距离", "distance_km"],
    "eta_hours": ["ETA小时", "时效小时", "eta_hours"],
    "cost": ["成本", "路线成本", "cost"],
    "is_primary": ["主路线", "是否主路线", "is_primary"],
}


def _canonical_header(value: Any) -> str:
    text = str(value or "").strip()
    for key, aliases in HEADER_ALIASES.items():
        if text in aliases or text.lower() in [str(alias).lower() for alias in aliases]:
            return key
    return re.sub(r"\s+", "_", text.lower())


def read_table(filename: str, payload: bytes) -> list[dict]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        return _read_csv(payload)
    if suffix in {".xlsx", ".xlsm"}:
        return _read_xlsx(payload)
    raise ValueError("仅支持 CSV 或 XLSX 文件")


def _read_csv(payload: bytes) -> list[dict]:
    text = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("CSV 编码无法识别，请使用 UTF-8")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV 缺少表头")
    mapping = {header: _canonical_header(header) for header in reader.fieldnames}
    return [
        {mapping.get(k, k): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
        for row in reader
    ]


def _read_xlsx(payload: bytes) -> list[dict]:
    workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    sheet = workbook.active
    values = list(sheet.iter_rows(values_only=True))
    if not values:
        raise ValueError("Excel 文件为空")
    headers = [_canonical_header(value) for value in values[0]]
    rows = []
    for raw in values[1:]:
        if not any(value is not None for value in raw):
            continue
        rows.append({headers[index]: raw[index] for index in range(min(len(headers), len(raw)))})
    return rows


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "是", "yes", "y", "有", "需要"}


def parse_number(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(str(value).strip())
    except ValueError:
        return default


def parse_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None
