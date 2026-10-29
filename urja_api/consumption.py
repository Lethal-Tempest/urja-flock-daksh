"""Energy readings, fetched per request. They aren't in the bulk export, so they get their own cache."""

import time
from collections import OrderedDict
from datetime import date, datetime, time as dtime, timedelta

from urja_api.domain import normalize
from urja_api.domain.models import Consumption
from urja_api.portal.client import PortalClient

DEFAULT_WINDOW_DAYS = 7
MAX_WINDOW_DAYS = 92


class TTLCache:
    def __init__(self, ttl_seconds: float, max_entries: int = 512) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._items: OrderedDict[tuple, tuple[float, object]] = OrderedDict()

    def get(self, key: tuple):
        hit = self._items.get(key)
        if hit is None or time.monotonic() - hit[0] > self._ttl:
            return None
        self._items.move_to_end(key)
        return hit[1]

    def put(self, key: tuple, value: object) -> None:
        self._items[key] = (time.monotonic(), value)
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)


def resolve_window(start: date | None, end: date | None) -> tuple[date | None, date | None]:
    """A request with only one bound means a 7-day window."""
    if start and not end:
        end = start + timedelta(days=DEFAULT_WINDOW_DAYS - 1)
    elif end and not start:
        start = end - timedelta(days=DEFAULT_WINDOW_DAYS - 1)
    return start, end


async def get_consumption(
    client: PortalClient, cache: TTLCache, meter_id: str, start: date | None, end: date | None
) -> Consumption | None:
    start, end = resolve_window(start, end)
    key = (meter_id, start, end)
    cached = cache.get(key)
    if cached is not None:
        return cached

    if start and end:
        # extra day so we get the midnight reading after `end`.
        # Without it a daily meter's last day has no usage.
        rows = await client.meter_energy(meter_id, start, end + timedelta(days=1))
    else:
        rows = await client.meter_energy(meter_id)  # no dates: portal returns its latest 7 days
    if rows is None:
        return None

    readings = normalize.readings(rows)
    if start and end:
        lo = datetime.combine(start, dtime.min, normalize.PORTAL_TZ)
        hi = datetime.combine(end + timedelta(days=1), dtime.min, normalize.PORTAL_TZ)
        readings = [r for r in readings if lo <= r.timestamp <= hi]
        if readings:  # nothing inside the window to subtract from the first row
            readings[0] = readings[0].model_copy(update={"consumed_kwh": None})

    result = Consumption(
        meter_id=meter_id, start=start, end=end,
        summary=normalize.summarize(readings), readings=readings,
    )
    cache.put(key, result)
    return result
