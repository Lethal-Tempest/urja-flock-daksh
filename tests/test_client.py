import pytest

from urja_api.config import Settings
from urja_api.portal.client import PortalAuthError, PortalClient, PortalError


@pytest.fixture
async def client(portal, settings):
    c = PortalClient(settings, transport=portal.transport())
    yield c
    await c.aclose()


async def test_logs_in_lazily_and_once(client, portal):
    await client.list_transformers()
    await client.meter_geo("J100000")
    assert portal.logins == 1


async def test_relogs_in_when_session_expires(client, portal):
    await client.meter_geo("J100000")
    portal.expire_sessions()
    assert await client.meter_geo("J100000") is not None
    assert portal.logins == 2


async def test_detects_expiry_on_data_json_routes(client, portal):
    await client.meter_geo("J100000")
    portal.expire_sessions()  # __data.json answers 200 {"type":"redirect","location":"/login"}
    page = await client.meter_page("J100000")
    assert page["meterId"] == "J100000" and portal.logins == 2


async def test_signs_export_requests(client, portal):
    meters = await client.export_meters()
    assert len(meters) == len(portal.export)
    export_call = next(r for r in portal.requests if r.url.path == "/portal/export")
    assert export_call.headers["x-signature"] and export_call.headers["x-timestamp"]


async def test_retries_through_rate_limit(client, portal):
    await client.meter_geo("J100000")
    portal.rate_limited = 2
    assert await client.meter_geo("J100000") is not None


async def test_gives_up_after_repeated_server_errors(client, portal):
    await client.meter_geo("J100000")
    portal.failing = 10
    with pytest.raises(PortalError):
        await client.meter_geo("J100000")


async def test_unknown_meter_is_none_not_error(client):
    assert await client.meter_energy("NOPE") is None
    assert await client.meter_page("NOPE") is None


async def test_bad_credentials_raise_auth_error(portal):
    settings = Settings(portal_base_url="https://portal.test", portal_email="ops@test", portal_password="wrong")
    c = PortalClient(settings, transport=portal.transport())
    with pytest.raises(PortalAuthError):
        await c.list_transformers()
    await c.aclose()


async def test_paginates_transformers(client, portal):
    rows = await client.list_transformers()
    assert len(rows) == 40
    assert len([r for r in portal.requests if r.url.path == "/portal/dts"]) == 2
