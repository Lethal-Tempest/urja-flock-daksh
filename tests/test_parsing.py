from datetime import datetime

from tests.conftest import fixture
from urja_api.domain import normalize
from urja_api.portal import devalue


def test_devalue_decodes_legacy_meter_page():
    data = devalue.page_data(fixture("page_legacy_J100000.json"))
    assert data["meterId"] == "J100000"
    assert data["hierarchy"]["DT"] == "Malviya Nagar DT 1 (DT-001)"
    assert {"parameterName": "Make", "parameterValue": "HPL"} in data["detail"]["data"]


def test_devalue_returns_none_for_error_node():
    assert devalue.page_data(fixture("page_missing.json")) is None


def test_both_detail_shapes_normalise_to_the_same_keys():
    legacy = normalize.detail_fields(devalue.page_data(fixture("page_legacy_J100000.json"))["detail"])
    v2 = normalize.detail_fields(devalue.page_data(fixture("page_v2_J100004.json"))["detail"])
    expected = {"meter_id", "serial_no", "make", "phase_type", "installation_status", "installation_type"}
    assert set(legacy) == expected and set(v2) == expected
    assert v2["installation_status"] == "Faulty" and legacy["serial_no"] == "SE33962"


def test_enum_values_are_normalised():
    assert normalize.status("Decommissioned").value == "decommissioned"
    assert normalize.installation_type("CT Operated").value == "ct_operated"
    assert normalize.phase("three").value == "three"


def test_readings_are_local_time_with_deltas():
    rows = normalize.readings(fixture("energy_halfhourly_J100000.json"))
    first, second = rows[0], rows[1]
    assert first.timestamp == datetime(2026, 6, 1, 0, 0, tzinfo=normalize.PORTAL_TZ)
    assert first.timestamp.utcoffset().total_seconds() == 5.5 * 3600
    assert first.consumed_kwh is None
    assert second.consumed_kwh == round(second.energy_kwh - first.energy_kwh, 4)


def test_duplicate_blank_reading_is_dropped():
    raw = fixture("energy_daily_J100089.json")
    assert len(raw) == 31 and len({r["timestamp"] for r in raw}) == 30  # the portal's quirk
    rows = normalize.readings(raw)
    assert len(rows) == 30
    assert all(r.energy_kwh > 0 for r in rows)


def test_summary_detects_interval_and_gaps():
    halfhourly = normalize.summarize(normalize.readings(fixture("energy_halfhourly_J100000.json")))
    assert halfhourly.interval_minutes == 30 and halfhourly.missing_intervals == 0
    rows = normalize.readings(fixture("energy_daily_J100089.json"))
    del rows[10]
    daily = normalize.summarize(rows)
    assert daily.interval_minutes == 1440 and daily.missing_intervals == 1
    assert 0 < daily.power_factor <= 1


def test_location_rejects_junk():
    assert normalize.location("26.9", "75.8").latitude == 26.9
    assert normalize.location(None, None) is None
    assert normalize.location(0, 0) is None
    assert normalize.location("x", "75") is None
