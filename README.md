# Urja Meter API

A clean, documented, read-only REST API in front of **Urja Meter Ops**, a legacy web
portal for smart-meter lookups that has no API of its own. Other programs can use it to get
meters, their place in the distribution network, and their consumption, without touching
the portal. A small web client on top shows the data as a map, a hierarchy explorer,
consumption charts and fleet health summaries.

| Document | What's in it |
|---|---|
| **[PROTOCOL.md](PROTOCOL.md)** | How the portal actually works: auth, every endpoint, the signed bulk export, rate limits, data quirks |
| **[openapi.json](openapi.json)** | OpenAPI 3.1 description of *this* API. Browsable at `/docs` (Swagger UI) or `/redoc` when running |
| **[REFLECTION.md](REFLECTION.md)** | Answers to the reflection questions |
| This README | What was built, how to run it, assumptions, decisions, trade-offs, what's next |

---

## Quick start

Requires **Python 3.11+**. The web client also needs **Node 18+**.

```bash
git clone <this repo> && cd urja-api
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

cp .env.example .env                 # portal URL + credentials; edit if yours differ
uvicorn urja_api.main:create_app --factory --port 8000
```

Open **http://127.0.0.1:8000/docs** for interactive API docs. The first data request
takes about 1–2 s while the catalog loads from the portal. Later requests are served from memory.

**Web client** (optional):

```bash
cd web && npm install && npm run build   # FastAPI then serves it at http://127.0.0.1:8000/
# or, for development with hot reload (proxies API calls to :8000):
npm run dev                              # http://localhost:5173
```

**Tests** (offline, against a fake portal, about 2 s):

```bash
pytest
```

### Configuration

All settings are environment variables (or `.env`), prefixed `URJA_`:

| Variable | Default | Meaning |
|---|---|---|
| `URJA_PORTAL_EMAIL` / `URJA_PORTAL_PASSWORD` | *(required)* | Portal login |
| `URJA_PORTAL_BASE_URL` | `https://urja-ops.flockenergy.tech` | Portal address |
| `URJA_PORTAL_REQUESTS_PER_MINUTE` | `100` | Our own cap. The portal starts returning 429 at about 120/min |
| `URJA_CATALOG_TTL_SECONDS` | `900` | How old meter/network data may get before a background refresh |
| `URJA_CONSUMPTION_TTL_SECONDS` | `300` | How long a consumption response is cached |

### Sample requests

```bash
curl 'http://127.0.0.1:8000/v1/meters?status=faulty&feeder=F-001&page_size=2'
```

```json
{
  "meta": { "total": 6, "page": 1, "page_size": 2, "data_as_of": "2026-10-28T12:47:13.862563Z" },
  "data": [
    { "id": "J100068", "serial_number": "GE23103", "make": "L&T", "phase": "single", "status": "faulty",
      "installation_type": "whole_current", "transformer_code": "DT-029",
      "location": { "latitude": 26.85775886211088, "longitude": 75.75026766173328 } },
    { "id": "J100080", "serial_number": "HP78319", "make": "L&T", "phase": "single", "status": "faulty",
      "installation_type": "ct_operated", "transformer_code": "DT-001",
      "location": { "latitude": 26.993871215118656, "longitude": 75.79331641065441 } }
  ]
}
```

Note that `feeder=F-001` spans two transformers (DT-001 and DT-029) in different branches. See *Design decisions*.

```bash
curl 'http://127.0.0.1:8000/v1/meters/J100000/consumption?start=2026-06-01&end=2026-06-07'
```

```json
{
  "meter_id": "J100000", "start": "2026-06-01", "end": "2026-06-07", "timezone": "Asia/Kolkata",
  "summary": { "readings": 337, "interval_minutes": 30,
               "first_reading_at": "2026-06-01T00:00:00+05:30", "last_reading_at": "2026-06-08T00:00:00+05:30",
               "consumed_kwh": 142.05, "consumed_kvah": 153.41, "power_factor": 0.926,
               "voltage_min": 220.0, "voltage_avg": 229.8, "voltage_max": 240.0, "missing_intervals": 0 },
  "readings": [
    { "timestamp": "2026-06-01T00:00:00+05:30", "energy_kwh": 47972.42, "apparent_energy_kvah": 51810.22,
      "voltage_r": 237.0, "consumed_kwh": null },
    { "timestamp": "2026-06-01T00:30:00+05:30", "energy_kwh": 47972.85, "apparent_energy_kvah": 51810.67,
      "voltage_r": 222.0, "consumed_kwh": 0.43 }
  ]
}
```
(`readings` truncated here to 2 of 337.)

(The portal only has readings for **June 2026**. Use dates in that month.)

---

## What the API offers

| Endpoint | Purpose |
|---|---|
| `GET /v1/meters` | Search and filter: `q` (id/serial), `status`, `make`, `phase`, `installation_type`, any network level by code (`zone` … `feeder`, `transformer`), `node` (subtree), `bbox`, `near=lat,lng&radius_km=` (sorted nearest first), pagination |
| `GET /v1/meters/{id}` | Nameplate, location, full network path, and any repairs applied to the record |
| `GET /v1/meters/{id}/consumption?start=&end=` | Register readings with per-interval usage, plus a summary (interval, total kWh/kVAh, power factor, voltage range, missing readings) |
| `GET /v1/network?depth=` | The reconstructed zone → circle → division → subdivision → substation → feeder → transformer tree, with meter counts and status breakdowns |
| `GET /v1/network/nodes/{id}` | One node: ancestors, children, counts |
| `GET /v1/transformers`, `/v1/transformers/{code}` | Transformers with capacity, feeder and connected-meter counts |
| `GET /v1/data-quality` | Every defect found in the portal's data, and what was done about it |
| `GET /health` | Portal reachability and catalog age. Reports `degraded` rather than failing |

Conventions: snake_case fields, lower-case enum values (`installed`, `ct_operated`),
ISO-8601 timestamps with the utility's `+05:30` offset, units in field names (`_kwh`,
`_kva`), and one error shape everywhere: `{"error": "not_found", "message": "Meter J9 not found"}`.
Errors use 400/422 for bad input, 404 for unknown ids, and **502** when the portal itself is failing.

## How it's structured

```
urja_api/
  portal/client.py     everything that knows the portal's wire protocol: login, session-expiry
                       detection, request signing, rate limiting, retries
  portal/devalue.py    decoder for SvelteKit's __data.json format
  domain/models.py     the public data model (the API contract)
  domain/normalize.py  field-level translation: enums, timestamps, numbers, the two detail shapes
  domain/network.py    rebuilds and repairs the hierarchy from per-meter records
  catalog.py           in-memory snapshot of meters/network + filtering; TTL refresh
  consumption.py       on-demand consumption fetch + TTL cache
  api/routes.py        HTTP endpoints;  api/errors.py  the uniform error shape
  main.py              app factory
scripts/export_openapi.py  regenerates openapi.json
tests/                 fake portal (conftest.py) + parser, network, client and API tests
web/                   React + Vite client (map, hierarchy explorer, charts, fleet overview)
```

Data flow: on the first request the **catalog** makes one signed bulk-export call (all
meters with hierarchy and coordinates) and pages through the transformer list (2 calls).
It then repairs the hierarchy and keeps the result in memory. Meter, network,
transformer and data-quality endpoints read that snapshot. Consumption is not in the
export, so it is fetched per request and cached briefly.

---

## Assumptions

* **Timestamps are IST (UTC+05:30).** The portal sends `DD/MM/YYYY HH:mm` with no zone.
  The utility is in Jaipur, and days start at 00:00 and end at 23:30, so local time is the
  only reading that makes sense. Responses keep the `+05:30` offset rather than converting
  to UTC, so "a day" means the utility's day.
* **`kwh`/`kvah` are cumulative registers.** They only ever increase, so usage is the
  difference between readings, not the value itself.
* **The transformer list (`/portal/dts`) is the master record for transformers.** Where a
  meter's copy disagrees (a stale name, a blank feeder), the master wins.
* **The meter, network and transformer data changes slowly** (a 15-minute TTL is fine),
  while consumption is the part people want fresh.
* **One shared service account is acceptable.** Every API consumer goes through the same portal login.
* The portal is read-only for us. The API never writes, and it stays under the portal's rate limit.

## Design decisions and trade-offs

**Use the bulk export, not page-by-page scraping.** The portal UI hides a signed
"export all meters" call (PROTOCOL §4). It is one request instead of about 1,200, and it
also contains fields search doesn't return (installation type, hierarchy, coordinates).
Under a 120 req/min limit, that is the difference between a 1-second load and a
7-minute crawl. The trade-off is that it relies on an undocumented signing scheme. The
client re-fetches the secret and retries once if a signature is rejected, and the
per-meter page is kept as a fallback for meters the export doesn't have.

**Network nodes are identified by their path.** Codes like `SD-01` and `F-001` appear
under several parents (PROTOCOL §8). If codes were global ids, the network would be a
many-to-many graph that no consumer could walk as a tree. So a node's id is its full path
(`Z-01.C-01.D-01`), and the original code is still a field. Code-based filters
(`/v1/meters?feeder=F-001`) still match across branches, so "everything on feeder F-001"
works too. Each reused code is listed in `/v1/data-quality` instead of being silently merged.

**Repair, but show the repairs.** Blank codes and names are filled from the other meters
on the same transformer (a transformer always has exactly one consistent path). Names
disagreeing with the master take the master's name. Every change is recorded as a
`DataIssue`, on the meter (`data_issues`) and in `/v1/data-quality`, so a consumer can
always see what the portal actually said.

**Hold the data in memory, not in a database.** The whole dataset is 403 meters (about
230 KB), so a dict and list filters answer every query in microseconds, with no schema,
migrations or a second process to run. `near` is a linear scan with haversine distance.
*Where this breaks down:* a scan over everything per request is fine into the tens of
thousands of meters. Around **10⁵–10⁶ meters**, or with many concurrent API workers, it
would stop working. Each worker would hold its own copy and run its own export (sharing
one portal rate budget), the export itself becomes a multi-megabyte single request, and
linear geo scans get slow. At that point I'd move to Postgres + PostGIS (or SQLite +
R*Tree for a single node), refreshed by one background sync job doing incremental
updates, with API workers reading from the database only.

**Freshness: TTL with stale-while-revalidate.** After `URJA_CATALOG_TTL_SECONDS`, the next
request triggers a background refresh and is answered from the current snapshot, so no
request ever waits on a refresh after the first load. A failed refresh keeps the last
good snapshot (`data_as_of` on responses and `catalog_age_seconds` on `/health` show its
age). The portal has no ETag or last-modified on its data, so time is the only staleness
signal available. A meter missing from the snapshot is looked up live before answering 404, so
newly added meters show up straight away.

**Consumption windows are whole local days, including the closing reading.**
`start=2026-06-01&end=2026-06-07` returns readings from 06-01 00:00 up to **06-08 00:00**
inclusive. Without that midnight reading, a daily meter's last day would show no usage.
A request with only one bound means 7 days. A request with neither returns the portal's
"latest 7 days". Invalid dates get a 422, where the portal silently accepts them. Windows
are capped at 92 days.

**Robustness lives in the client.** Logins happen lazily and once (concurrent requests
share one re-login). The client re-logs in shortly before the fixed 1h session ends, and
again whenever it sees any of the portal's three ways of saying "logged out". Our own
sliding-window limiter (100/min) keeps us under the portal's 429 threshold. If a 429
still arrives, the client waits and retries. Timeouts and 5xx responses get exponential
backoff. After a few attempts the API answers 502 with a clear message, and the catalog
keeps serving its last snapshot. Session re-login was verified against the live portal by
corrupting the session cookie mid-run.

**OpenAPI comes from the code.** FastAPI generates the spec from the Pydantic models and
route signatures. `scripts/export_openapi.py` writes `openapi.json`, and a test fails if the
committed file differs from the running app, so the spec can't drift.

**Tests use a fake portal, not recordings of the live one.** `tests/conftest.py`
reproduces the portal behaviours from PROTOCOL.md (login responses, all three logged-out
signals, the HMAC check, pagination, 404 shapes) over trimmed real fixtures. The tests
run offline in about 2 s and can inject session expiry, 429s and 5xx.

## What I intentionally left out

* **Write operations.** The portal is to be treated as read-only.
* **Authentication on this API.** It is meant to run inside a trusted network for internal
  teams. Before exposing it further I'd add API keys or SSO at the gateway.
* **Fleet-wide consumption analytics** (e.g. "top consumers across all meters"). That
  needs every meter's readings, which is 403 calls (at least 4 minutes under the rate
  limit) per refresh. It's a job for a scheduled background sync into a store, not for a
  request handler. The meter-level consumption endpoint and its summary are there instead.
* **Scraping the HTML pages.** The JSON endpoints and the export cover everything the pages show.
* **Persistence, metrics, Docker.** Not needed to run or evaluate this at the current size.
* **Web client tests.** It's a thin view over the API, and its data shaping is small.

## What I'd improve with more time

1. A background sync that also pulls consumption for all meters into SQLite or Postgres on
   a schedule. That enables fleet analytics: anomaly ranking, transformer loading against
   kVA capacity, and "decommissioned but consuming" across the whole fleet instead of per meter.
2. Conditional refresh: compare a cheap signal (e.g. search `total`) before re-running the export.
3. Structured logging and metrics for portal latency, 429s and re-logins.
4. A contract test that runs against the live portal on a schedule, to catch protocol drift
   (a new signing scheme, renamed fields) before users do.
5. Confirm the timezone and the meaning of `build`/`legacy`/`v2` with the utility, rather
   than inferring them.

## Reflection

See **[REFLECTION.md](REFLECTION.md)**.
