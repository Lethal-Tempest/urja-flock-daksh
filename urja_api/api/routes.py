from collections import Counter
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from urja_api.api.errors import ApiError, bad_request, not_found
from urja_api.catalog import Catalog, Snapshot, filter_meters, meter_from_page
from urja_api.consumption import MAX_WINDOW_DAYS, TTLCache, get_consumption
from urja_api.domain.models import (
    Consumption,
    DataQualityReport,
    ErrorBody,
    Health,
    InstallationType,
    Meter,
    MeterPage,
    MeterSummary,
    MeterStatus,
    NetworkLevel,
    NetworkNode,
    NetworkNodeDetail,
    NetworkTree,
    PageMeta,
    Phase,
    Transformer,
    TransformerList,
)
from urja_api.portal.client import PortalClient, PortalError

router = APIRouter(prefix="/v1")
system = APIRouter(tags=["system"])

ERRORS = {404: {"model": ErrorBody}, 502: {"model": ErrorBody, "description": "The portal is unreachable or failing"}}
METER_ID = Path(description="Meter number, e.g. `J100000`", pattern=r"^[A-Za-z0-9_-]{1,32}$")


def get_client(request: Request) -> PortalClient:
    return request.app.state.client


def get_catalog(request: Request) -> Catalog:
    return request.app.state.catalog


async def get_snapshot(catalog: Annotated[Catalog, Depends(get_catalog)]) -> Snapshot:
    return await catalog.snapshot()


SnapshotDep = Annotated[Snapshot, Depends(get_snapshot)]


def _floats(raw: str, count: int, name: str) -> list[float]:
    try:
        values = [float(x) for x in raw.split(",")]
    except ValueError:
        values = []
    if len(values) != count:
        raise bad_request(f"`{name}` must be {count} comma-separated numbers")
    return values


@router.get("/meters", response_model=MeterPage, tags=["meters"], responses=ERRORS)
async def list_meters(
    snapshot: SnapshotDep,
    q: Annotated[str | None, Query(description="Substring of meter id or serial number (case-insensitive)")] = None,
    status: Annotated[list[MeterStatus] | None, Query(description="Repeat to match any of several")] = None,
    make: Annotated[list[str] | None, Query(description="Manufacturer, e.g. `HPL`; repeatable")] = None,
    phase: Phase | None = None,
    installation_type: InstallationType | None = None,
    zone: str | None = None,
    circle: str | None = None,
    division: str | None = None,
    subdivision: str | None = None,
    substation: str | None = None,
    feeder: str | None = None,
    transformer: Annotated[str | None, Query(description="Transformer code, e.g. `DT-007`")] = None,
    node: Annotated[str | None, Query(description="Network node id: every meter below that node")] = None,
    bbox: Annotated[str | None, Query(description="`south,west,north,east` in degrees")] = None,
    near: Annotated[str | None, Query(description="`lat,lng`; results sorted nearest first")] = None,
    radius_km: Annotated[float, Query(gt=0, le=100)] = 1.0,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
):
    """Search and filter meters. All filters combine with AND; code filters match a code at that
    level anywhere in the network (e.g. `feeder=F-001` spans both branches that carry F-001)."""
    levels = {
        NetworkLevel.zone: zone, NetworkLevel.circle: circle, NetworkLevel.division: division,
        NetworkLevel.subdivision: subdivision, NetworkLevel.substation: substation,
        NetworkLevel.feeder: feeder, NetworkLevel.transformer: transformer,
    }
    meters = filter_meters(
        snapshot,
        q=q, status=status, make=make, phase=phase, installation_type=installation_type, node=node,
        codes={level: code for level, code in levels.items() if code},
        bbox=tuple(_floats(bbox, 4, "bbox")) if bbox else None,
        near=(*_floats(near, 2, "near"), radius_km) if near else None,
    )
    window = meters[(page - 1) * page_size : page * page_size]
    return MeterPage(
        meta=PageMeta(total=len(meters), page=page, page_size=page_size, data_as_of=snapshot.fetched_at),
        data=[MeterSummary.model_validate(m.model_dump(include=set(MeterSummary.model_fields))) for m in window],
    )


@router.get("/meters/{meter_id}", response_model=Meter, tags=["meters"], responses=ERRORS)
async def get_meter(
    snapshot: SnapshotDep,
    client: Annotated[PortalClient, Depends(get_client)],
    meter_id: Annotated[str, METER_ID],
):
    """One meter with its full network path and any repairs applied to its record.
    Meters added since the last catalog refresh are looked up live."""
    meter = snapshot.meters.get(meter_id.upper())
    if meter:
        return meter
    page = await client.meter_page(meter_id)
    if page is None:
        raise not_found(f"Meter {meter_id}")
    return meter_from_page(page, await client.meter_geo(meter_id))


@router.get("/meters/{meter_id}/consumption", response_model=Consumption, tags=["meters"], responses=ERRORS)
async def get_meter_consumption(
    request: Request,
    client: Annotated[PortalClient, Depends(get_client)],
    meter_id: Annotated[str, METER_ID],
    start: Annotated[date | None, Query(description="First local day, inclusive (YYYY-MM-DD)")] = None,
    end: Annotated[date | None, Query(description="Last local day, inclusive (YYYY-MM-DD)")] = None,
):
    """Register readings and usage for a meter. Readings are half-hourly for some meters and daily
    for others; `summary.interval_minutes` says which. With only one bound the window is 7 days;
    with neither, the portal's latest 7 days are returned."""
    if start and end and end < start:
        raise bad_request("`end` must not be before `start`")
    if start and end and (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise bad_request(f"Window may span at most {MAX_WINDOW_DAYS} days")
    cache: TTLCache = request.app.state.consumption_cache
    result = await get_consumption(client, cache, meter_id.upper(), start, end)
    if result is None:
        raise not_found(f"Meter {meter_id}")
    return result


def _tree(snapshot: Snapshot, node_id: str, depth: int) -> NetworkNode:
    node = snapshot.network.nodes[node_id]
    return NetworkNode(
        id=node.id, level=node.level, code=node.code, name=node.name,
        meter_count=len(node.meter_ids), meters_by_status=snapshot.status_counts(node.meter_ids),
        children=[_tree(snapshot, c, depth - 1) for c in node.children] if depth > 0 else [],
    )


@router.get("/network", response_model=NetworkTree, tags=["network"], responses=ERRORS)
async def get_network(
    snapshot: SnapshotDep,
    depth: Annotated[int, Query(ge=0, le=6, description="Levels below zone to include (6 = down to transformers)")] = 6,
):
    """The reconstructed zone > circle > division > subdivision > substation > feeder > transformer tree."""
    return NetworkTree(
        data_as_of=snapshot.fetched_at,
        zones=[_tree(snapshot, root.id, depth) for root in snapshot.network.roots()],
    )


@router.get("/network/nodes/{node_id}", response_model=NetworkNodeDetail, tags=["network"], responses=ERRORS)
async def get_network_node(snapshot: SnapshotDep, node_id: str):
    """One node with its ancestors and direct children. List its meters with `GET /v1/meters?node={id}`."""
    nodes = snapshot.network.nodes
    node = nodes.get(node_id)
    if node is None:
        raise not_found(f"Network node {node_id}")
    ancestors = []
    parent = node.parent_id
    while parent:
        ancestors.insert(0, nodes[parent].ref())
        parent = nodes[parent].parent_id
    return NetworkNodeDetail(
        id=node.id, level=node.level, code=node.code, name=node.name,
        meter_count=len(node.meter_ids), meters_by_status=snapshot.status_counts(node.meter_ids),
        ancestors=ancestors, children=[nodes[c].ref() for c in node.children],
        transformer=snapshot.transformers.get(node.code) if node.level is NetworkLevel.transformer else None,
    )


@router.get("/transformers", response_model=TransformerList, tags=["network"], responses=ERRORS)
async def list_transformers(snapshot: SnapshotDep):
    """All distribution transformers with capacity and connected-meter counts."""
    return TransformerList(data=sorted(snapshot.transformers.values(), key=lambda t: t.code))


@router.get("/transformers/{code}", response_model=Transformer, tags=["network"], responses=ERRORS)
async def get_transformer(snapshot: SnapshotDep, code: str):
    transformer = snapshot.transformers.get(code.upper())
    if transformer is None:
        raise not_found(f"Transformer {code}")
    return transformer


@router.get("/data-quality", response_model=DataQualityReport, tags=["network"], responses=ERRORS)
async def get_data_quality(snapshot: SnapshotDep):
    """Every defect found in the portal's records and how it was handled."""
    counts = Counter(i.kind for i in snapshot.issues)
    return DataQualityReport(data_as_of=snapshot.fetched_at, counts=dict(counts), issues=snapshot.issues)


@system.get("/health", response_model=Health)
async def health(request: Request):
    """Liveness plus upstream status. Never fails: reports `degraded` instead."""
    catalog: Catalog = request.app.state.catalog
    try:
        reachable = await request.app.state.client.check_session()
    except (PortalError, ApiError):
        reachable = False
    age = catalog.age_seconds()
    snapshot = catalog.current
    return Health(
        status="ok" if reachable and catalog.loaded else "degraded",
        portal_reachable=reachable,
        catalog_loaded=catalog.loaded,
        catalog_age_seconds=round(age, 1) if age is not None else None,
        meters=len(snapshot.meters) if snapshot else None,
    )
