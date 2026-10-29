"""Enough of SvelteKit's devalue format to read __data.json.

`data` is a flat array. Index 0 is the root. Object fields and list items
are indexes into that array, not the values. Negative indexes are sentinels
(undefined, NaN, ...). A list whose first item is a string is a tagged
value, like ["Date", "..."]. Tags we don't know are returned as-is.
"""

from datetime import datetime
from typing import Any

_SENTINELS: dict[int, Any] = {
    -1: None,  # undefined
    -2: None,  # array hole
    -3: float("nan"),
    -4: float("inf"),
    -5: float("-inf"),
    -6: -0.0,
}


def unflatten(values: list[Any]) -> Any:
    cache: dict[int, Any] = {}

    def hydrate(index: int) -> Any:
        if index in _SENTINELS:
            return _SENTINELS[index]
        if index in cache:
            return cache[index]

        value = values[index]
        if isinstance(value, dict):
            obj: dict[str, Any] = {}
            cache[index] = obj
            for key, ref in value.items():
                obj[key] = hydrate(ref)
            return obj

        if isinstance(value, list):
            if value and isinstance(value[0], str):
                result = _hydrate_tagged(value, hydrate)
            else:
                result = [hydrate(ref) for ref in value]
            cache[index] = result
            return result

        cache[index] = value
        return value

    return hydrate(0)


def _hydrate_tagged(value: list[Any], hydrate) -> Any:
    tag, *rest = value
    if tag == "Date":
        return datetime.fromisoformat(rest[0].replace("Z", "+00:00"))
    if tag == "Set":
        return [hydrate(ref) for ref in rest]
    if tag == "Map":
        return {hydrate(k): hydrate(v) for k, v in zip(rest[::2], rest[1::2])}
    if tag == "null":  # ["null", key, ref, key, ref, ...]
        return {k: hydrate(ref) for k, ref in zip(rest[::2], rest[1::2])}
    if tag == "BigInt":
        return int(rest[0])
    if tag == "Object":
        return hydrate(rest[0])
    return value  # unseen tag, leave it alone


def page_data(payload: dict[str, Any], node: int = -1) -> Any:
    """Decoded load() data for one node. Defaults to the leaf page."""
    nodes = payload.get("nodes") or []
    if not nodes:
        return None
    entry = nodes[node]
    if not entry or entry.get("type") != "data":
        return None
    return unflatten(entry["data"])
