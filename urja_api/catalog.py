"""Meters, transformers, and the network, kept in memory.

About 400 meters and one export call. When the TTL is up the next request
kicks a background refresh and still gets the current snapshot. A failed
refresh leaves the last good one in place.
"""

import asyncio
import logging
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from urja_api.domain import normalize
from urja_api.domain.models import (
    LEVELS,
    DataIssue,
    Location,
    Meter,
    MeterStatus,
    NetworkLevel,
    NodeRef,
    Transformer,
)
from urja_api.domain.network import Network, build_network
from urja_api.portal.client import PortalClient

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Snapshot:
    fetched_at: datetime
    meters: dict[str, Meter]
    transformers: dict[str, Transformer]
    network: Network
    issues: list[DataIssue]

    def status_counts(self, meter_ids: list[str]) -> dict[MeterStatus, int]:
        counts = Counter(self.meters[m].status for m in meter_ids if m in self.meters)
        return {s: counts.get(s, 0) for s in MeterStatus}


def build_snapshot(raw_meters: list[dict[str, Any]], raw_transformers: list[dict[str, Any]]) -> Snapshot:
    network = build_network(raw_meters, raw_transformers)
    issues = list(network.issues)
    meters: dict[str, Meter] = {}
    for raw in raw_meters:
        mid = raw["meterId"]
        geo = raw.get("geo") or {}
        loc = normalize.location(geo.get("lat"), geo.get("lng"))
        if loc is None:
            issues.append(DataIssue(kind="invalid", meter_id=mid, field="geo", problem=f"missing or invalid ({geo})", action="location set to null"))
        try:
            meters[mid] = Meter(
                id=mid,
                serial_number=raw["serialNo"],
                make=raw["make"],
                phase=normalize.phase(raw["phaseType"]),
                status=normalize.status(raw["installStatus"]),
                installation_type=normalize.installation_type(raw["installType"]),
                transformer_code=network.nodes[network.meter_paths[mid][-1]].code,
                location=loc,
                network=network.path_refs(mid),
                data_issues=[i for i in network.issues if i.meter_id == mid],
            )
        except (KeyError, ValueError) as exc:
            issues.append(DataIssue(kind="invalid", meter_id=mid, field="record", problem=f"unparseable ({exc})", action="meter omitted"))

    transformers = {}
    for raw in raw_transformers:
        node_id = network.transformer_nodes.get(raw["code"])
        meter_ids = network.nodes[node_id].meter_ids if node_id else []
        transformers[raw["code"]] = Transformer(
            code=raw["code"],
            name=raw["name"],
            capacity_kva=float(raw["capacityKva"]),
            feeder_code=raw["feederCode"],
            node_id=node_id,
            meter_count=len(meter_ids),
            meters_by_status={},
        )
    snapshot = Snapshot(datetime.now(UTC), meters, transformers, network, issues)
    for t in transformers.values():
        node = network.nodes.get(t.node_id) if t.node_id else None
        t.meters_by_status = snapshot.status_counts(node.meter_ids if node else [])
    return snapshot


class Catalog:
    def __init__(self, client: PortalClient, ttl_seconds: int) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._snapshot: Snapshot | None = None
        self._loaded_at: float | None = None
        self._lock = asyncio.Lock()
        self._background: asyncio.Task | None = None

    @property
    def loaded(self) -> bool:
        return self._snapshot is not None

    @property
    def current(self) -> Snapshot | None:
        """What's being served. Doesn't trigger a load."""
        return self._snapshot

    def age_seconds(self) -> float | None:
        return None if self._loaded_at is None else time.monotonic() - self._loaded_at

    async def snapshot(self) -> Snapshot:
        if self._snapshot is None:
            await self.refresh()
        elif self.age_seconds() > self._ttl and not (self._background and not self._background.done()):
            self._background = asyncio.create_task(self._refresh_quietly())
        return self._snapshot

    async def refresh(self) -> Snapshot:
        async with self._lock:
            if self._snapshot is not None and self.age_seconds() <= self._ttl:
                return self._snapshot  # another caller already refreshed
            raw_meters = await self._client.export_meters()
            raw_transformers = await self._client.list_transformers()
            self._snapshot = build_snapshot(raw_meters, raw_transformers)
            self._loaded_at = time.monotonic()
            log.info("Catalog refreshed: %d meters, %d issues", len(self._snapshot.meters), len(self._snapshot.issues))
            return self._snapshot

    async def _refresh_quietly(self) -> None:
        try:
            await self.refresh()
        except Exception:
            log.exception("Catalog refresh failed; serving stale data")


def haversine_km(a: Location, lat: float, lng: float) -> float:
    p1, p2 = math.radians(a.latitude), math.radians(lat)
    dp, dl = p2 - p1, math.radians(lng - a.longitude)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def filter_meters(
    snapshot: Snapshot,
    *,
    q: str | None = None,
    status: list[MeterStatus] | None = None,
    make: list[str] | None = None,
    phase: str | None = None,
    installation_type: str | None = None,
    node: str | None = None,
    codes: dict[NetworkLevel, str] | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    near: tuple[float, float, float] | None = None,
) -> list[Meter]:
    """Every filter is ANDed. `near` is (lat, lng, radius_km) and sorts by distance."""
    meters = snapshot.meters.values()
    if node:
        target = snapshot.network.nodes.get(node)
        allowed = set(target.meter_ids) if target else set()
        meters = [m for m in meters if m.id in allowed]
    if q:
        needle = q.strip().lower()
        meters = [m for m in meters if needle in m.id.lower() or needle in m.serial_number.lower()]
    if status:
        meters = [m for m in meters if m.status in status]
    if make:
        wanted = {x.lower() for x in make}
        meters = [m for m in meters if m.make.lower() in wanted]
    if phase:
        meters = [m for m in meters if m.phase == phase]
    if installation_type:
        meters = [m for m in meters if m.installation_type == installation_type]
    for level, code in (codes or {}).items():
        depth = LEVELS.index(level)
        meters = [m for m in meters if m.network[depth].code.lower() == code.lower()]
    if bbox:
        south, west, north, east = bbox
        meters = [m for m in meters if m.location and south <= m.location.latitude <= north and west <= m.location.longitude <= east]
    if near:
        lat, lng, radius = near
        scored = [(haversine_km(m.location, lat, lng), m) for m in meters if m.location]
        return [m for d, m in sorted(scored, key=lambda x: x[0]) if d <= radius]
    return sorted(meters, key=lambda m: m.id)


_LABEL = re.compile(r"^(?P<name>.*?)\s*\((?P<code>[^()]+)\)\s*$")


def meter_from_page(page: dict[str, Any], geo: dict[str, Any] | None) -> Meter:
    """Meter from the detail page, for ones the last export didn't have.

    Hierarchy there is a label like "Circle 1 (C-01)". One record isn't
    enough to repair blanks, so they stay blank.
    """
    fields = normalize.detail_fields(page["detail"])
    labels = page.get("hierarchy") or {}
    keys = ["Zone", "Circle", "Division", "Subdivision", "Sub Station", "Feeder", "DT"]
    network: list[NodeRef] = []
    for level, key in zip(LEVELS, keys):
        match = _LABEL.match(labels.get(key) or "")
        code, name = (match["code"], match["name"]) if match else ("", "")
        prefix = network[-1].id + "." if network else ""
        network.append(NodeRef(id=prefix + (code or "UNKNOWN"), level=level, code=code, name=name or code))
    return Meter(
        id=page["meterId"],
        serial_number=fields.get("serial_no", ""),
        make=fields.get("make", ""),
        phase=normalize.phase(fields["phase_type"]),
        status=normalize.status(fields["installation_status"]),
        installation_type=normalize.installation_type(fields["installation_type"]),
        transformer_code=network[-1].code,
        location=normalize.location(geo.get("latitude"), geo.get("longitude")) if geo else None,
        network=network,
        data_issues=[DataIssue(kind="not_in_export", meter_id=page["meterId"], field="record",
                               problem="not in the latest bulk export", action="served live from the meter page")],
    )
