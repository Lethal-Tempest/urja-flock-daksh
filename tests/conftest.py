import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest

from urja_api.config import Settings
from urja_api.portal import client as portal_client

FIXTURES = Path(__file__).parent / "fixtures"
SECRET = "test-signing-secret"
COOKIE = portal_client.SESSION_COOKIE


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


class FakePortal:
    """In-process stand-in for the portal, reproducing the behaviours documented in PROTOCOL.md.

    Knobs let a test inject failures: expire the session, return 429s or 5xx.
    """

    def __init__(self) -> None:
        self.export = fixture("export.json")["data"]
        self.transformers = fixture("transformers.json")
        self.pages = {"J100000": fixture("page_legacy_J100000.json"), "J100004": fixture("page_v2_J100004.json")}
        self.energy = {"J100000": fixture("energy_halfhourly_J100000.json"), "J100089": fixture("energy_daily_J100089.json")}
        self.logins = 0
        self.valid_tokens: set[str] = set()
        self.rate_limited = 0  # next N requests get 429
        self.failing = 0  # next N requests get 503
        self.requests: list[httpx.Request] = []

    def expire_sessions(self) -> None:
        self.valid_tokens.clear()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if self.rate_limited:
            self.rate_limited -= 1
            return httpx.Response(429, json={"error": "rate_limited", "message": "Rate limit exceeded; slow down."})
        if self.failing:
            self.failing -= 1
            return httpx.Response(503, text="upstream down")

        if path == "/login" and request.method == "POST":
            form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
            if form.get("password") != "secret":
                return httpx.Response(200, json={"type": "failure", "status": 401, "data": "[]"})
            self.logins += 1
            token = f"token{self.logins}"
            self.valid_tokens.add(token)
            return httpx.Response(200, json={"type": "redirect", "status": 303, "location": "/meters"},
                                  headers={"set-cookie": f"{COOKIE}={token}; Path=/; HttpOnly; Secure"})

        if request.headers.get("cookie", "").split("=", 1)[-1] not in self.valid_tokens:
            if path.endswith("__data.json"):
                return httpx.Response(200, json={"type": "redirect", "location": "/login"})
            return httpx.Response(401, json={"error": "unauthorized", "message": "A valid session is required."})

        if path == "/api/auth/get-session":
            return httpx.Response(200, json={"session": {}, "user": {}})
        if path == "/portal/keys":
            return httpx.Response(200, json={"data": {"signingSecret": SECRET}})
        if path == "/portal/export":
            ts, sig = request.headers.get("x-timestamp", ""), request.headers.get("x-signature", "")
            expected = hmac.new(SECRET.encode(), "\n".join(["GET", path, request.url.query.decode(), ts]).encode(),
                                hashlib.sha256).hexdigest()
            if sig != expected:
                return httpx.Response(401, json={"error": "signature_invalid", "message": "Missing or invalid request signature."})
            return httpx.Response(200, json={"data": self.export, "total": len(self.export)})
        if path == "/portal/dts":
            page = int(request.url.params.get("page", 1))
            rows = self.transformers[(page - 1) * 20 : page * 20]
            return httpx.Response(200, json={"data": rows, "total": len(self.transformers), "page": page, "pageSize": 20})
        if path.startswith("/portal/meters/"):
            _, _, _, meter_id, what = path.split("/")
            if what == "energy" and meter_id in self.energy:
                return httpx.Response(200, json={"data": self._energy_window(meter_id, request)})
            if what == "geo" and meter_id in self.pages:
                return httpx.Response(200, json={"data": {"latitude": "26.9", "longitude": "75.8"}})
            return httpx.Response(404, text="<!doctype html>Not Found")
        if path.startswith("/meters/") and path.endswith("/__data.json"):
            meter_id = path.split("/")[2]
            return httpx.Response(200, json=self.pages.get(meter_id) or fixture("page_missing.json"))
        return httpx.Response(404, text="<!doctype html>Not Found")

    def _energy_window(self, meter_id: str, request: httpx.Request) -> list[dict]:
        # The real portal filters by local day; the fixtures are small enough to filter by string.
        rows = self.energy[meter_id]
        start, end = request.url.params.get("from"), request.url.params.get("to")

        def day(row):
            d, m, y = row["timestamp"][:10].split("/")
            return f"{y}-{m}-{d}"

        return [r for r in rows if (not start or day(r) >= start) and (not end or day(r) <= end)]


@pytest.fixture
def portal() -> FakePortal:
    return FakePortal()


@pytest.fixture
def settings() -> Settings:
    return Settings(portal_base_url="https://portal.test", portal_email="ops@test", portal_password="secret",
                    portal_requests_per_minute=1000)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Retries and backoff must not slow the suite down."""
    async def instant(_seconds):
        return None
    monkeypatch.setattr(portal_client, "_sleep", instant)
