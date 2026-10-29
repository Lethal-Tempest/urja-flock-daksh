"""Portal HTTP client.

Login, session expiry, export signing, and the rate limit live here.
Callers get decoded JSON. PROTOCOL.md has the raw notes on the portal.
"""

import asyncio
import hashlib
import hmac
import logging
import time
from collections import deque
from datetime import date
from typing import Any
from urllib.parse import urlencode

import httpx

from urja_api.config import Settings
from urja_api.portal import devalue

log = logging.getLogger(__name__)

SESSION_COOKIE = "__Secure-better-auth.session_token"
# Fixed 1h session (Max-Age=3600), not sliding. Refresh a few minutes early.
SESSION_LIFETIME_SECONDS = 3600 - 300
RATE_WINDOW_SECONDS = 60.0


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)  # tests patch this


class PortalError(Exception):
    pass


class PortalAuthError(PortalError):
    pass


class RateLimiter:
    """At most `limit` calls in any rolling minute.

    Their limiter is a fixed 60s bucket. A rolling window under the same
    number can't spill one of those buckets.
    """

    def __init__(self, limit: int, window: float = RATE_WINDOW_SECONDS) -> None:
        self.limit = limit
        self.window = window
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self.window:
                    self._calls.popleft()
                if len(self._calls) < self.limit:
                    self._calls.append(now)
                    return
                await _sleep(self.window - (now - self._calls[0]) + 0.01)


class PortalClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        base = settings.portal_base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=base,
            timeout=settings.portal_timeout_seconds,
            # login 403s without a matching Origin (sveltekit csrf)
            headers={"Origin": base, "User-Agent": "urja-api/0.1"},
            follow_redirects=False,
            transport=transport,
        )
        self._limiter = RateLimiter(settings.portal_requests_per_minute)
        self._login_lock = asyncio.Lock()
        self._session_generation = 0
        self._session_started: float | None = None
        self._signing_secret: str | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _login(self) -> None:
        await self._limiter.acquire()
        self._http.cookies.clear()
        response = await self._http.post(
            "/login",
            data={
                "email": self._settings.portal_email,
                "password": self._settings.portal_password.get_secret_value(),
            },
        )
        if SESSION_COOKIE not in self._http.cookies:
            raise PortalAuthError(f"Portal login failed (HTTP {response.status_code})")
        self._session_generation += 1
        self._session_started = time.monotonic()
        log.info("Logged in to portal (session #%d)", self._session_generation)

    async def _ensure_session(self, stale_generation: int | None = None) -> int:
        """Current session generation, logging in first if we need to.

        `stale_generation` is the one a caller just saw rejected, so several
        requests hitting the same expiry share a single re-login.
        """
        async with self._login_lock:
            expired = (
                self._session_started is None
                or time.monotonic() - self._session_started > SESSION_LIFETIME_SECONDS
                or stale_generation == self._session_generation
            )
            if expired:
                await self._login()
            return self._session_generation

    async def check_session(self) -> bool:
        """True when the portal still accepts the session. Used by /health."""
        response = await self._request("/api/auth/get-session")
        return response.status_code == 200 and bool(response.json())

    async def _request(
        self, path: str, params: dict[str, Any] | None = None, signed: bool = False
    ) -> httpx.Response:
        """GET with login, the rate limit, and retries. 2xx and 404 come back; the rest raise."""
        query = urlencode(params or {})
        url = f"{path}?{query}" if query else path
        attempts = self._settings.portal_max_attempts
        last_problem = "no attempt made"
        stale: int | None = None

        for attempt in range(1, attempts + 1):
            generation = await self._ensure_session(stale)
            stale = None
            headers = await self._signature_headers(path, query) if signed else None
            await self._limiter.acquire()
            try:
                response = await self._http.get(url, headers=headers)
            except httpx.TransportError as exc:  # includes timeouts
                last_problem = f"{type(exc).__name__}: {exc}"
                await self._backoff(attempt)
                continue

            if self._is_logged_out(response):
                last_problem = "session expired"
                stale = generation
                continue
            if response.status_code == 401 and _error_code(response) == "signature_invalid":
                last_problem = "export signature rejected"
                self._signing_secret = None  # fetch it again next time
                continue
            if response.status_code == 429:
                last_problem = "rate limited by portal"
                # no Retry-After. their window is 60s.
                await _sleep(min(RATE_WINDOW_SECONDS, 5.0 * 2 ** (attempt - 1)))
                continue
            if response.status_code >= 500:
                last_problem = f"portal returned HTTP {response.status_code}"
                await self._backoff(attempt)
                continue
            return response

        raise PortalError(f"GET {path} failed after {attempts} attempts ({last_problem})")

    @staticmethod
    def _is_logged_out(response: httpx.Response) -> bool:
        # logged-out looks different depending on the route
        if response.status_code == 401 and _error_code(response) == "unauthorized":
            return True  # /portal/*
        if response.status_code in (302, 303) and response.headers.get("location", "").endswith("/login"):
            return True  # html pages bounce to /login
        if response.headers.get("content-type", "").startswith("application/json"):
            try:
                body = response.json()
            except ValueError:
                return False
            # __data.json answers 200 and puts the redirect in the body
            return isinstance(body, dict) and body.get("type") == "redirect" and body.get("location") == "/login"
        return False

    @staticmethod
    async def _backoff(attempt: int) -> None:
        await _sleep(min(10.0, 0.5 * 2 ** (attempt - 1)))

    async def _signature_headers(self, path: str, query: str) -> dict[str, str]:
        if self._signing_secret is None:
            response = await self._request("/portal/keys")
            response.raise_for_status()
            self._signing_secret = response.json()["data"]["signingSecret"]
        timestamp = str(int(time.time()))
        message = "\n".join(["GET", path, query, timestamp])
        signature = hmac.new(self._signing_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        return {"x-timestamp": timestamp, "x-signature": signature}

    async def _get_json(self, path: str, params: dict[str, Any] | None = None, signed: bool = False) -> Any:
        """Decoded JSON, or None on 404."""
        response = await self._request(path, params, signed)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise PortalError(f"GET {path} returned HTTP {response.status_code}: {response.text[:200]}")
        return response.json()

    async def export_meters(self) -> list[dict[str, Any]]:
        """Every meter, one signed call.

        `page` is ignored (all rows come back either way) but it's part of the
        signed string, and the UI always sends page=1.
        """
        body = await self._get_json("/portal/export", {"page": 1}, signed=True)
        return body["data"]

    async def list_transformers(self) -> list[dict[str, Any]]:
        """All transformers. The portal pages these 20 at a time."""
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            body = await self._get_json("/portal/dts", {"page": page})
            rows.extend(body["data"])
            if not body["data"] or len(rows) >= body["total"]:
                return rows
            page += 1

    async def meter_page(self, meter_id: str) -> dict[str, Any] | None:
        """Nameplate and hierarchy labels off the meter page."""
        body = await self._get_json(f"/meters/{meter_id}/__data.json")
        if body is None or body.get("type") != "data":
            return None
        return devalue.page_data(body)

    async def meter_geo(self, meter_id: str) -> dict[str, Any] | None:
        body = await self._get_json(f"/portal/meters/{meter_id}/geo")
        return None if body is None else body["data"]

    async def meter_energy(
        self, meter_id: str, start: date | None = None, end: date | None = None
    ) -> list[dict[str, Any]] | None:
        params = {}
        if start:
            params["from"] = start.isoformat()
        if end:
            params["to"] = end.isoformat()
        body = await self._get_json(f"/portal/meters/{meter_id}/energy", params)
        return None if body is None else body["data"]


def _error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("error") if isinstance(body, dict) else None
