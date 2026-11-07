from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


Priority = Literal["P0", "P1", "P2"]


class MaterialCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=120)
    aliases: str = ""
    category: str = "其他"
    specification: str = ""
    base_unit: str = Field(default="件", min_length=1, max_length=16)
    package_unit: str = ""
    conversion_rate: float = Field(default=1, gt=0)
    storage_type: str = "常温"
    shelf_life_days: int | None = Field(default=None, ge=0)
    is_cold_chain: bool = False
    is_hazardous: bool = False
    unit_weight_kg: float = Field(default=0.5, ge=0)
    default_priority: Priority = "P1"
    source: str = "manual"
    status: Literal["active", "archived"] = "active"


class DemandItemInput(BaseModel):
    material_id: int
    quantity: float = Field(gt=0)
    priority: Priority = "P1"
    deadline_hours: float = Field(default=12, gt=0, le=168)


class Weights(BaseModel):
    speed: float = Field(default=0.38, ge=0, le=1)
    cost: float = Field(default=0.27, ge=0, le=1)
    risk: float = Field(default=0.35, ge=0, le=1)


class EventConfig(BaseModel):
    type: str = "暴雨洪涝"
    severity: str = "II级"
    location: str = "武汉市·模拟沿江区"
    affected_population: int = Field(default=12000, ge=0)
    closed_route_ids: list[str] = Field(default_factory=list)
    note: str = ""


class RunRequest(BaseModel):
    scenario_id: str = "rainstorm-ii"
    event_id: str | None = None
    event: EventConfig = Field(default_factory=EventConfig)
    demand_items: list[DemandItemInput] = Field(min_length=1)
    weights: Weights = Field(default_factory=Weights)

    @field_validator("demand_items")
    @classmethod
    def validate_unique_materials(cls, items: list[DemandItemInput]) -> list[DemandItemInput]:
        ids = [item.material_id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("同一物资不能重复添加")
        return items


class InventoryImportResult(BaseModel):
    snapshot_id: int
    snapshot_label: str
    imported: int
    errors: list[str]
    warnings: list[str]


class MaterialImportResult(BaseModel):
    imported: int
    updated: int
    errors: list[str]
    warnings: list[str]


class OperationalEventCreate(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    event_type: str = "突发事件"
    severity: str = "III级"
    location: str = Field(min_length=2, max_length=160)
    lat: float | None = None
    lng: float | None = None
    affected_population: int = Field(default=0, ge=0)
    description: str = ""
    source: str = "manual"
    source_ref: str = ""
    closed_routes: list[str] = Field(default_factory=list)
    demand_items: list[dict] = Field(default_factory=list)


class WarehouseCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    region: str = Field(min_length=2, max_length=200)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    capacity: float = Field(default=0, ge=0)
    status: Literal["open", "closed"] = "open"


class DestinationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    address: str = Field(min_length=2, max_length=200)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    population: int = Field(default=0, ge=0)
    contact: str = Field(default="", max_length=120)


class VehicleCreate(BaseModel):
    plate_no: str = Field(min_length=2, max_length=32)
    vehicle_type: str = Field(default="应急运输车辆", min_length=2, max_length=80)
    capacity_kg: float = Field(gt=0)
    temp_zone: str = Field(default="常温", max_length=32)
    warehouse_id: str = Field(min_length=2, max_length=64)
    status: Literal["available", "maintenance", "unavailable"] = "available"
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


class InventoryEntryCreate(BaseModel):
    warehouse_id: str = Field(min_length=2, max_length=64)
    material_id: int = Field(gt=0)
    quantity: float = Field(gt=0)
    unit: str = Field(default="", max_length=16)
    batch_no: str = Field(default="", max_length=80)
    expiry_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    status: Literal["available", "locked", "expired"] = "available"


class InventoryEntryUpdate(BaseModel):
    quantity: float | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, min_length=1, max_length=16)
    batch_no: str | None = Field(default=None, max_length=80)
    expiry_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    status: Literal["available", "locked", "expired"] | None = None


class RouteGenerateRequest(BaseModel):
    warehouse_ids: list[str] = Field(default_factory=list)
    destination_ids: list[str] = Field(default_factory=list)
    replace_generated: bool = True


class VehiclePositionInput(BaseModel):
    vehicle_id: str
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    speed_kmh: float | None = None
    heading: float | None = None
    accuracy_m: float | None = None
    source: str = "gps"
    reported_at: str | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=200)


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    display_name: str = Field(default="", max_length=80)
    password: str = Field(min_length=10, max_length=200)
    role: Literal["viewer", "dispatcher", "verifier", "admin"] = "viewer"


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=8, max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    password: str | None = Field(default=None, min_length=10, max_length=200)
    role: Literal["viewer", "dispatcher", "verifier", "admin"] | None = None
    is_active: bool | None = None


class AgentRegisterRequest(BaseModel):
    id: str = Field(min_length=2, max_length=80)
    name: str = Field(min_length=2, max_length=120)
    role: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=500)
    capabilities: list[str] = Field(default_factory=list)
    version: str = Field(default="v1", min_length=1, max_length=40)
    endpoint: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=120)
    prompt_version: str = Field(default="", max_length=80)
    activate: bool = True


class WorkflowValidateRequest(BaseModel):
    definition: dict

class WorkflowUpsertRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    stage: Literal["after_demand", "after_matching", "after_verification", "post_verification", "standalone"] = "post_verification"
    definition: dict
    enabled: bool = True

class AgentInvokeRequest(BaseModel):
    task: dict = Field(default_factory=dict)
    session_id: str | None = Field(default=None, max_length=100)


class AgentVersionCreate(BaseModel):
    version: str = Field(min_length=1, max_length=40)
    endpoint: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=120)
    prompt_version: str = Field(default="", max_length=80)
    activate: bool = True


class TaskStatusUpdate(BaseModel):
    status: Literal["pending", "assigned", "in_progress", "completed", "failed", "cancelled"]
    assignee: str = ""
