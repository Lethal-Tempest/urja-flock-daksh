import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from urja_api.api import errors
from urja_api.api.routes import router, system
from urja_api.catalog import Catalog
from urja_api.config import Settings, get_settings
from urja_api.consumption import TTLCache
from urja_api.portal.client import PortalClient

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"

DESCRIPTION = """
A clean, read-only REST API over the **Urja Meter Ops** portal (which has no API of its own).

* **Meters**: nameplate, location, network position, and consumption readings.
* **Network**: the zone → transformer hierarchy, reconstructed and repaired from per-meter records.
* **Data quality**: every defect found in the portal's data and what was done about it.

Meter and network data is served from a catalog refreshed from the portal's bulk export
(see `data_as_of`); consumption is fetched live and cached briefly.
All errors use the shape `{"error": "<code>", "message": "<text>"}`.
"""


def create_app(settings: Settings | None = None, client: PortalClient | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = client or PortalClient(settings)
        app.state.catalog = Catalog(app.state.client, settings.catalog_ttl_seconds)
        app.state.consumption_cache = TTLCache(settings.consumption_ttl_seconds)
        yield
        await app.state.client.aclose()

    app = FastAPI(title="Urja Meter API", version="1.0.0", description=DESCRIPTION, lifespan=lifespan)
    errors.install(app)
    app.include_router(system)
    app.include_router(router)
    if WEB_DIST.is_dir():  # optional built web client
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# Run with: uvicorn urja_api.main:create_app --factory
