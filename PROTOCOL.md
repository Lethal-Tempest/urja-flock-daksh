# How the Urja Meter Ops portal works

Everything below was found by using the portal as a normal logged-in user: reading the
HTML it serves, the JavaScript bundles the browser downloads, and the JSON those bundles
fetch. No endpoint was guessed blindly or attacked. The only non-GET request ever sent is
the login form. Observations are dated **28 Oct 2026**.

## 1. What the portal is

* **SvelteKit** app (response header `X-Sveltekit-Page: true`, assets under `/_app/immutable/`).
  Pages render a shell on the server, and most data is fetched by the browser afterwards.
* **Better Auth** for sessions (cookie `__Secure-better-auth.session_token`, and the
  `/api/auth/*` routes).
* The client route manifest (`/_app/immutable/entry/app.*.js`) lists every page:
  `/login`, `/meters`, `/meters/[id]`, `/transformers` (plus `/`, which redirects).
  Each page's bundle (`/_app/immutable/nodes/N.*.js`) names the JSON endpoints it calls.
  That is how the full endpoint list below was found.
* `robots.txt` allows everything.

## 2. Authentication

| Step | Request | Response |
|---|---|---|
| Log in | `POST /login`, form-encoded `email`, `password`. **Must send `Origin: https://urja-ops.flockenergy.tech`.** | `200` JSON `{"type":"redirect","status":303,"location":"/meters"}` + `Set-Cookie: __Secure-better-auth.session_token=…; Max-Age=3600; HttpOnly; Secure; SameSite=Lax` |
| Wrong password | same | `200` (not 401!) JSON `{"type":"failure","status":401,"data":"[…\"Invalid email or password.\"]"}`, and no cookie |
| No `Origin` header | same | `403 Cross-site POST form submissions are forbidden` (SvelteKit's CSRF check) |
| Who am I | `GET /api/auth/get-session` | session + user JSON. `expiresAt` = login + 1h |
| Log out | `POST /api/auth/sign-out` | (not used) |

* Sessions are a **fixed 1 hour**. `updatedAt` never moves, so activity does not extend it.
* **A missing or expired session shows up in three different ways**, depending on the route:
  * `/portal/*` JSON endpoints: `401 {"error":"unauthorized","message":"A valid session is required."}`
  * HTML pages: `302 Location: /login`
  * `…/__data.json` (SvelteKit page data): `200 {"type":"redirect","location":"/login"}`, a
    *success* status code that actually means "logged out".

## 3. Endpoints

All responses are JSON unless noted. Numbers inside records are often strings.

| Endpoint | Used by page | Returns |
|---|---|---|
| `GET /portal/meters/search?q=&page=` | Meters list | `{data:[{meterId, serialNo, make, phaseType, installStatus, dtCode}], total, page, pageSize:20}` |
| `GET /portal/dts?page=` | Transformers | `{data:[{code, name, feederCode, capacityKva}], total:40, page, pageSize:20}` |
| `GET /portal/meters/{id}/geo` | Meter detail | `{data:{latitude:"26.93…", longitude:"75.83…"}}` (strings) |
| `GET /portal/meters/{id}/energy?from=&to=` | Meter detail | `{data:[{timestamp:"DD/MM/YYYY HH:mm", kwh:"…", kvah:"…", voltR:"…"}]}` |
| `GET /meters/{id}/__data.json` | Meter detail (server load) | nameplate + hierarchy labels, in SvelteKit *devalue* encoding (see §5) |
| `GET /portal/keys` | Transformers ("Export all meters") | `{data:{signingSecret:"…"}}` |
| `GET /portal/export?page=1` + signature headers | Transformers ("Export all meters") | **every meter**, with full hierarchy and coordinates (see §4) |

Details and quirks:

* **Search**: `q` is a case-insensitive substring match on `meterId` and `serialNo` only.
  `SE33` matches, `HPL` (make) and `DT-001` don't. The page size is fixed at 20 and
  `pageSize`/`limit` are ignored. A page past the end returns `data: []` with the real `total`.
* **Unknown meter**: `/geo` and `/energy` return a **404 HTML page**, not JSON. `__data.json`
  returns **200** whose last node is `{"type":"error","status":404,"error":{"message":"Meter not found"}}`.
* **Unknown route**: 404 HTML.

## 4. The bulk path: signed export

The Transformers page has an "Export all meters" button. Its code:

1. `GET /portal/keys` gives `signingSecret`.
2. Signs `"GET\n/portal/export\n" + query + "\n" + unixSeconds` with **HMAC-SHA256**, as hex.
3. `GET /portal/export?page=1` with headers `x-timestamp: <unixSeconds>` and `x-signature: <hex>`.

The response is `{data:[…403 meters…], total:403}`. Each record has more than search returns:
`installType`, `build` (`legacy` | `v2`), a full `hierarchy`
`{zone, circle, division, subdivision, substation, feeder, dt}` (each `{name, code}`), and
`geo {lat, lng}` as floats. **One call (about 230 KB) replaces about 1,200 per-meter calls**
(search pages + detail + geo for every meter).

* The `page` parameter is ignored. Every value, or none at all, returns all 403 meters.
  It still has to be inside the signed string, because the signature covers the exact query.
* The timestamp must be within roughly **±5 minutes** of server time. -301s and +120s were
  accepted; -600s and +400s were rejected.
* The secret is the **same across calls and across sessions**. It is static.
* Bad, missing or mismatched signature: `401 {"error":"signature_invalid", …}`.
* **Observation:** the secret is handed to any logged-in client, so the signature proves
  only that the caller read `/portal/keys` first. It is a speed bump, not a security
  boundary. It also lets the portal tell "used the UI's export button" apart from plain scraping.
* Energy readings are **not** in the export. They are only available per meter.

## 5. Meter page data (`__data.json`) and its two record formats

SvelteKit serialises each page's `load()` result with *devalue*: a flat array where index 0
is the root and every object value or array item is an **index into the same array**
(negative numbers are sentinels such as `undefined`). Decoded, the meter page holds:

```jsonc
{ "meterId": "J100000",
  "detail": …,                       // two different shapes, below
  "hierarchy": { "Zone": "Jaipur Zone 1 (Z-01)", "Circle": "Circle 1 (C-01)", …, "Sub Station": "Substation 1 (SS-01)", "DT": "Malviya Nagar DT 1 (DT-001)" } }
```

`detail` depends on the meter's `build` (the export's field):

* **legacy** (238 meters): `{"data":[{"parameterName":"Serial No","parameterValue":"SE33962"}, …]}`
* **v2** (165 meters): `{"classData":"{\"installed_meter\":{\"SerialNo\":\"SE65293\",\"Make\":\"Genus\",…}}"}`,
  a JSON object **encoded as a string**, with PascalCase keys.

The portal's own page has a function that handles both shapes. Hierarchy here is display
text (`"Name (CODE)"`) that has to be parsed back apart. The export gives the same
information already structured, so this path is only a fallback.

## 6. Consumption (`/energy`)

* `from`/`to` take **`YYYY-MM-DD`**, interpreted as whole local days, both inclusive
  (`from=2026-06-01&to=2026-06-02` returns 00:00 on the 1st through 23:30 on the 2nd).
  * Neither: the **last 7 days of data** ending at the newest reading
    (`23/06/2026 23:30 … 30/06/2026 23:30`, 337 rows). That is a window starting mid-day.
  * Only `from`: `from 00:00` to `from + 7d 00:00`.
  * Only `to`: 7 days ending `to 23:30`.
  * `to` before `from`, or a range with no data: `[]`.
  * **Garbage is silently accepted.** `from=bad&to=2026-06-01` returns just June 1. Other
    formats (ISO datetimes, `DD/MM/YYYY`) are ignored and you get the default window.
  * No maximum window (30 days works fine).
* **Data exists only for 1–30 June 2026**, for every meter. Requests outside that are empty.
* Rows: `timestamp` `"DD/MM/YYYY HH:mm"` with **no timezone**. `kwh` and `kvah` are
  **cumulative registers** (monotonic, never reset in June). `voltR` is R-phase voltage.
  All three are strings.
* **Two reading intervals.** 40 meters (`J100000`–`J100039`, one per transformer) report
  **every 30 minutes**. The other 363 report **once a day at 00:00**. Nothing in a meter's
  record says which; you only find out from the readings.
* **Duplicate blank row:** five meters (`J100089`, `J100134`, `J100195`, `J100211`,
  `J100330`) end with a second `30/06/2026 00:00` row whose `kwh` and `voltR` are `""`
  while `kvah` is filled in.
* Otherwise the data is clean: no gaps, no negative deltas, voltage 220–240 V, and the
  power factor (Δkwh/Δkvah) is exactly 0.926 for every meter, which suggests the data is synthetic.

## 7. Rate limiting

* About **120 requests per fixed 60-second window**, login included. A burst got 119 OK
  responses after logging in, then **`429 {"error":"rate_limited","message":"Rate limit exceeded; slow down."}`**.
* **No `Retry-After`** and no rate-limit headers. The window reset 49s after the first 429
  in one test, consistent with a fixed 60s window.
* The session check runs before the limiter: an unauthenticated request still gets `401`
  while you are limited.
* Not established: whether the limit is per session, per account or per IP. I only had one
  account and one IP, and didn't try to get around the limit.
* This makes the bulk export important. Fetching every meter's detail and geo one by one
  takes at least 7 minutes. Fetching every meter's energy takes at least 4 minutes.

## 8. Data quality: the network hierarchy

The portal has no hierarchy endpoint. The hierarchy only exists as a copy inside each
meter's export record (or as display labels on the meter page). Profiling all 403 copies found:

| Finding | Count |
|---|---|
| Blank hierarchy **codes** (circle 6, feeder 5, substation 3) | 14 |
| Blank hierarchy **names** (circle 5, feeder 3) | 8 |
| Stale transformer name: `J100400`–`J100402` say DT-007 is *"Old Malviya Nagar Xfmr"*; the transformer list and the 10 other DT-007 meters say *"Sanganer DT 7"* | 3 |
| Codes that appear under more than one parent (e.g. `SD-01` under `D-01`, `D-05` and `D-09`; feeder `F-001` under substations `SS-01` and `SS-11`) | 54 codes |

What *is* consistent:

* Once blanks are ignored, **every transformer (DT) maps to exactly one path**.
* Each DT's feeder always matches the transformer list (`/portal/dts`), which is the only
  "master" data the portal has.
* `dtCode` on a meter always equals its `hierarchy.dt.code`.

So the DT is the reliable anchor, and codes above feeder level are not globally unique.
Either they are only unique within their parent, or the source data is wrong. The tree
cannot be a strict tree if codes are treated as global ids. Feeder `F-001` really does feed
DT-001 and DT-029, which sit under different substations. How the API handles this is
described in the README (§ Design decisions).

Other observations:

* **The hierarchy isn't geographic.** A circle's meters are spread across the whole city
  rather than clustered. Don't use it as a proxy for location.
* **Status doesn't match the readings.** Decommissioned meters keep recording consumption
  (an average of 447 kWh in June, against 462 kWh for installed meters). Faulty meters report
  complete, plausible data. Either the status field is stale or the readings are not
  real telemetry.
* Every meter has valid coordinates (around Jaipur), and IDs and serials are unique.
