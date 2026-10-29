import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from urja_api.main import create_app
from urja_api.portal.client import PortalClient

ROOT = Path(__file__).parent.parent


@pytest.fixture
def api(portal, settings):
    app = create_app(settings, client=PortalClient(settings, transport=portal.transport()))
    with TestClient(app) as test_client:
        yield test_client


def test_list_and_filter_meters(api, portal):
    body = api.get("/v1/meters", params={"page_size": 5}).json()
    assert body["meta"]["total"] == len(portal.export)
    assert len(body["data"]) == 5 and "network" not in body["data"][0]

    faulty = api.get("/v1/meters", params={"status": "faulty", "transformer": "DT-007"}).json()
    assert faulty["data"] and all(m["status"] == "faulty" and m["transformer_code"] == "DT-007" for m in faulty["data"])

    on_f001 = api.get("/v1/meters", params={"feeder": "F-001"}).json()
    assert {m["transformer_code"] for m in on_f001["data"]} == {"DT-001", "DT-029"}


def test_near_sorts_by_distance(api):
    origin = api.get("/v1/meters/J100000").json()["location"]
    body = api.get("/v1/meters", params={"near": f"{origin['latitude']},{origin['longitude']}", "radius_km": 50}).json()
    assert body["data"][0]["id"] == "J100000"


def test_meter_detail_has_repaired_path(api):
    meter = api.get("/v1/meters/J100400").json()
    assert [n["level"] for n in meter["network"]][-1] == "transformer"
    assert meter["network"][-1]["name"] == "Sanganer DT 7"
    assert any(i["field"] == "transformer.name" for i in meter["data_issues"])


def test_meter_missing_from_export_is_served_live(api, portal):
    portal.export = [m for m in portal.export if m["meterId"] != "J100004"]
    meter = api.get("/v1/meters/J100004").json()
    assert meter["status"] == "faulty" and meter["network"][-1]["code"] == "DT-005"
    assert meter["data_issues"][0]["kind"] == "not_in_export"


def test_unknown_meter_is_404(api):
    response = api.get("/v1/meters/J999999")
    assert response.status_code == 404 and response.json()["error"] == "not_found"


def test_consumption_window_includes_closing_reading(api):
    body = api.get("/v1/meters/J100000/consumption", params={"start": "2026-06-01", "end": "2026-06-01"}).json()
    assert body["summary"]["interval_minutes"] == 30
    assert body["summary"]["readings"] == 49  # 48 half-hours + midnight closing reading
    assert body["readings"][-1]["timestamp"] == "2026-06-02T00:00:00+05:30"
    assert body["readings"][0]["consumed_kwh"] is None


def test_consumption_validation(api):
    assert api.get("/v1/meters/J100000/consumption", params={"start": "2026-06-05", "end": "2026-06-01"}).status_code == 400
    assert api.get("/v1/meters/J100000/consumption", params={"start": "junk"}).status_code == 422
    assert api.get("/v1/meters/J100000/consumption", params={"start": "2026-01-01", "end": "2026-12-31"}).status_code == 400
    assert api.get("/v1/meters/NOPE/consumption").status_code == 404


def test_network_tree_and_nodes(api, portal):
    tree = api.get("/v1/network").json()
    assert sum(z["meter_count"] for z in tree["zones"]) == len(portal.export)
    zone = tree["zones"][0]
    node = api.get(f"/v1/network/nodes/{zone['children'][0]['id']}").json()
    assert node["ancestors"][0]["id"] == zone["id"] and node["children"]
    assert api.get("/v1/network/nodes/nope").status_code == 404


def test_transformers_and_data_quality(api):
    dt = api.get("/v1/transformers/dt-007").json()
    assert dt["capacity_kva"] == 63 and dt["meter_count"] > 0
    report = api.get("/v1/data-quality").json()
    assert report["counts"]["blank"] > 0 and report["counts"]["code_reused"] > 0


def test_upstream_failure_is_502(api, portal):
    api.get("/v1/meters")  # load the catalog first
    portal.failing = 100
    response = api.get("/v1/meters/J100000/consumption")
    assert response.status_code == 502 and response.json()["error"] == "upstream_unavailable"
    assert api.get("/v1/meters").status_code == 200  # catalog keeps serving


def test_health(api):
    assert api.get("/health").json()["portal_reachable"] is True


def test_committed_openapi_matches_app(api):
    committed = json.loads((ROOT / "openapi.json").read_text())
    assert committed == api.app.openapi(), "run `python scripts/export_openapi.py` to refresh openapi.json"
