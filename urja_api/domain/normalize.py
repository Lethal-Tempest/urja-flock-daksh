"""Portal fields into our models."""

import json
import re
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any

from urja_api.domain.models import (
    ConsumptionSummary,
    InstallationType,
    Location,
    MeterStatus,
    Phase,
    Reading,
)

# no timezone on the timestamps. utility is in Jaipur and days end at 23:30, so IST.
PORTAL_TZ = timezone(timedelta(hours=5, minutes=30), "IST")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def status(value: str) -> MeterStatus:
    return MeterStatus(_slug(value))


def phase(value: str) -> Phase:
    return Phase(_slug(value))


def installation_type(value: str) -> InstallationType:
    return InstallationType(_slug(value))


def location(lat: Any, lng: Any) -> Location | None:
    """Export sends floats. /geo sends strings."""
    try:
        latitude, longitude = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180) or (latitude, longitude) == (0, 0):
        return None
    return Location(latitude=latitude, longitude=longitude)


def detail_fields(detail: dict[str, Any]) -> dict[str, str]:
    """Nameplate from the meter page. Two layouts.

    legacy: {"data": [{"parameterName": "Serial No", "parameterValue": "..."}]}
    v2: {"classData": "<json string of installed_meter>"}, PascalCase keys.

    Both come back as serial_no, make, and the rest.
    """
    if "classData" in detail:
        pairs = (json.loads(detail["classData"]).get("installed_meter") or {}).items()
    else:
        pairs = ((p["parameterName"], p["parameterValue"]) for p in detail.get("data") or [])
    out = {}
    for key, value in pairs:
        # SerialNo, "Serial No", InstallationStatus -> serial_no / installation_status
        key = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", str(key))
        out[_slug(key)] = str(value)
    return out


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def readings(rows: list[dict[str, Any]]) -> list[Reading]:
    """Energy rows. Timestamps are 'DD/MM/YYYY HH:mm', numbers arrive as strings."""
    parsed: list[tuple[datetime, float, float, float | None]] = []
    for row in rows:
        kwh, kvah = _number(row.get("kwh")), _number(row.get("kvah"))
        if kwh is None or kvah is None:
            continue  # no register values, skip it
        ts = datetime.strptime(row["timestamp"], "%d/%m/%Y %H:%M").replace(tzinfo=PORTAL_TZ)
        parsed.append((ts, kwh, kvah, _number(row.get("voltR"))))

    parsed.sort(key=lambda r: r[0])
    out: list[Reading] = []
    previous: float | None = None
    seen: set[datetime] = set()
    for ts, kwh, kvah, volt in parsed:
        if ts in seen:
            continue
        seen.add(ts)
        out.append(
            Reading(
                timestamp=ts,
                energy_kwh=kwh,
                apparent_energy_kvah=kvah,
                voltage_r=volt,
                consumed_kwh=None if previous is None else round(kwh - previous, 4),
            )
        )
        previous = kwh
    return out


def summarize(rows: list[Reading]) -> ConsumptionSummary:
    if not rows:
        return ConsumptionSummary(
            readings=0, interval_minutes=None, first_reading_at=None, last_reading_at=None,
            consumed_kwh=None, consumed_kvah=None, power_factor=None,
            voltage_min=None, voltage_avg=None, voltage_max=None, missing_intervals=0,
        )
    first, last = rows[0], rows[-1]
    gaps = [(b.timestamp - a.timestamp).total_seconds() / 60 for a, b in zip(rows, rows[1:])]
    interval = int(median(gaps)) if gaps else None
    expected = int((last.timestamp - first.timestamp).total_seconds() / 60 / interval) + 1 if interval else 1
    kwh = round(last.energy_kwh - first.energy_kwh, 4) if len(rows) > 1 else None
    kvah = round(last.apparent_energy_kvah - first.apparent_energy_kvah, 4) if len(rows) > 1 else None
    volts = [r.voltage_r for r in rows if r.voltage_r is not None]
    return ConsumptionSummary(
        readings=len(rows),
        interval_minutes=interval,
        first_reading_at=first.timestamp,
        last_reading_at=last.timestamp,
        consumed_kwh=kwh,
        consumed_kvah=kvah,
        power_factor=round(kwh / kvah, 3) if kwh is not None and kvah else None,
        voltage_min=min(volts) if volts else None,
        voltage_avg=round(mean(volts), 1) if volts else None,
        voltage_max=max(volts) if volts else None,
        missing_intervals=max(0, expected - len(rows)),
    )
