"""Response models. Portal field names don't get past normalize.py."""

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class MeterStatus(StrEnum):
    installed = "installed"
    faulty = "faulty"
    decommissioned = "decommissioned"


class Phase(StrEnum):
    single = "single"
    three = "three"


class InstallationType(StrEnum):
    whole_current = "whole_current"
    ct_operated = "ct_operated"


class NetworkLevel(StrEnum):
    zone = "zone"
    circle = "circle"
    division = "division"
    subdivision = "subdivision"
    substation = "substation"
    feeder = "feeder"
    transformer = "transformer"


LEVELS: list[NetworkLevel] = list(NetworkLevel)


class Location(BaseModel):
    latitude: float
    longitude: float


class NodeRef(BaseModel):
    id: str = Field(description="Path-qualified node id, e.g. `Z-01.C-01.D-01`. Stable across refreshes.")
    level: NetworkLevel
    code: str = Field(description="The utility's code for this node, e.g. `D-01`. Not unique on its own above feeder level.")
    name: str


IssueKind = Literal["blank", "conflict", "code_reused", "unknown_reference", "invalid", "not_in_export"]


class DataIssue(BaseModel):
    """A defect found in the portal's data, and what this API did about it."""

    kind: IssueKind
    meter_id: str | None = None
    field: str
    problem: str
    action: str


class MeterSummary(BaseModel):
    id: str = Field(examples=["J100000"])
    serial_number: str
    make: str
    phase: Phase
    status: MeterStatus
    installation_type: InstallationType
    transformer_code: str = Field(examples=["DT-001"])
    location: Location | None


class Meter(MeterSummary):
    network: list[NodeRef] = Field(description="Path from zone down to the meter's distribution transformer.")
    data_issues: list[DataIssue] = Field(description="Repairs applied to this meter's portal record.")


class Transformer(BaseModel):
    code: str
    name: str
    capacity_kva: float
    feeder_code: str
    node_id: str | None = Field(description="Network node id, or null if no meter places it in the hierarchy.")
    meter_count: int
    meters_by_status: dict[MeterStatus, int]


class NetworkNode(BaseModel):
    id: str
    level: NetworkLevel
    code: str
    name: str
    meter_count: int
    meters_by_status: dict[MeterStatus, int]
    children: list["NetworkNode"] = Field(default_factory=list)


class NetworkNodeDetail(BaseModel):
    id: str
    level: NetworkLevel
    code: str
    name: str
    meter_count: int
    meters_by_status: dict[MeterStatus, int]
    ancestors: list[NodeRef]
    children: list[NodeRef]
    transformer: Transformer | None = Field(None, description="Set when this node is a transformer.")


class Reading(BaseModel):
    timestamp: datetime = Field(description="Reading time, ISO-8601 with the utility's local offset (+05:30).")
    energy_kwh: float = Field(description="Cumulative active-energy register (kWh). Never resets in observed data.")
    apparent_energy_kvah: float = Field(description="Cumulative apparent-energy register (kVAh).")
    voltage_r: float | None = Field(description="R-phase voltage (V) at the reading.")
    consumed_kwh: float | None = Field(description="kWh used since the previous reading in this response; null for the first.")


class ConsumptionSummary(BaseModel):
    readings: int
    interval_minutes: int | None = Field(description="Typical spacing of readings: 30 (half-hourly AMI) or 1440 (daily).")
    first_reading_at: datetime | None
    last_reading_at: datetime | None
    consumed_kwh: float | None = Field(description="Energy used between the first and last reading.")
    consumed_kvah: float | None
    power_factor: float | None = Field(description="consumed_kwh / consumed_kvah over the window.")
    voltage_min: float | None
    voltage_avg: float | None
    voltage_max: float | None
    missing_intervals: int = Field(description="Expected readings absent from the window, given interval_minutes.")


class Consumption(BaseModel):
    meter_id: str
    start: date | None = Field(description="First local day requested (inclusive). Null if the portal default window was used.")
    end: date | None = Field(description="Last local day requested (inclusive).")
    timezone: str = "Asia/Kolkata"
    summary: ConsumptionSummary
    readings: list[Reading]


class PageMeta(BaseModel):
    total: int
    page: int
    page_size: int
    data_as_of: datetime = Field(description="When the meter catalog was last refreshed from the portal.")


class MeterPage(BaseModel):
    meta: PageMeta
    data: list[MeterSummary]


class TransformerList(BaseModel):
    data: list[Transformer]


class NetworkTree(BaseModel):
    data_as_of: datetime
    zones: list[NetworkNode]


class DataQualityReport(BaseModel):
    data_as_of: datetime
    counts: dict[str, int] = Field(description="Number of issues per kind.")
    issues: list[DataIssue]


class Health(BaseModel):
    status: str = Field(examples=["ok", "degraded"])
    portal_reachable: bool
    catalog_loaded: bool
    catalog_age_seconds: float | None
    meters: int | None


class ErrorBody(BaseModel):
    error: str = Field(examples=["not_found"])
    message: str
