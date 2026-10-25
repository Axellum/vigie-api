# Architecture

## Overview

```
 USGS  EMSC  GDACS  NOAA NWS  USGS Volcano  NHC  tsunami.gov    Open-Meteo (forecast, marine, flood, air)   MET Norway   RainViewer   NOAA SWPC   MeteoAlarm
   \     |     |       |         /                       |                                     |             |          |          |
    +----+-----+-------+--------+--------------+---------+-------------------------------------+-------------+----------+----------+
                                       Collectors (app/collectors)
                          circuit breaker + TTL cache + parsers that never invent a value
                                                |
                                  AggregatorService (app/services/aggregator.py)
                       fusion, geofencing, weather fallback chain, snapshot, point, CAP, GeoJSON
                                  |                                     |
                     SQLite (storage_wal.py)                   FastAPI routes (app/main.py)
              watchlist, hazard history, last snapshot         /v1/*, /dashboard, WS /v1/stream
                                                                        |
                                                  embedded_frame.py  ->  text frame  ->  ESP32-P4 display
```

Two repositories cooperate: **this API** (Python, runs on the PC) and the **display firmware**
(C++, ESP32-P4). The only interface between them is the text frame, documented in
[`embedded-frame.md`](embedded-frame.md). See [ADR 0001](decisions/0001-two-repos-no-fabricated-data-frame-format.md).

## Modules

| Module | Role |
| --- | --- |
| `app/main.py` | FastAPI application, routes, WebSocket hub, optional scheduler (lifespan). |
| `app/models/schemas.py` | Pydantic models. A value a provider did not supply is `None`; no model has a default number standing for missing data. |
| `app/collectors/grid.py`, `app/collectors/weights.py`, `app/collectors/budget.py` | Point grids (snapping, settings); weight of an Open-Meteo request; published quotas and the call budget. |
| `app/attribution.py` | The credit each source asks for: one table, read by the models, the collectors, the aggregator and the dashboard (see [`SOURCES.md`](../SOURCES.md)). |
| `app/clock.py` | Time source of the collectors, replaceable by a test (simulated clock). |
| `app/collectors/base.py` | `BaseCollector`: HTTP client, circuit breaker (`CLOSED`, `OPEN`, `HALF_OPEN`), TTL cache, request coalescing, health. |
| `app/collectors/seismic.py` | USGS and EMSC earthquakes. |
| `app/collectors/disasters.py` | GDACS and NOAA NWS alerts (the NWS answer is also read as official warnings). |
| `app/collectors/nhc.py`, `app/shapefile.py`, `app/storm_geo.py` | The National Hurricane Center: the file of the active storms, then the ZIP of shapefiles of each storm's advisory (a strict reader of the ESRI format, and the GeoJSON made of its shapes: rounding, counter-clockwise rings, cut at the date line). |
| `app/collectors/tsunami.py` | The U.S. Tsunami Warning Centers: the list of the latest bulletins, the CAP message of each one that can be an alert, the table of arrival times of a threat message; the events, hazards and vigilances made from them. |
| `app/models/forecast.py`, `app/services/forecasts.py` | The forecast of a hazard as a GeoJSON FeatureCollection (`GET /v1/geojson/hazards/{id}/forecast`). |
| `app/collectors/meteoalarm.py` | MeteoAlarm: the Atom feed of a country and the CAP documents it links to; the level, phenomenon and status tables. |
| `app/collectors/dwd.py`, `eccc.py`, `metservice.py`, `vigicrues.py` | The national warning services: the DWD (a ZIP of CAP documents, read conditionally), ECCC (MSC GeoMet), MetService (an RSS feed and its CAP documents) and Vigicrues (the GeoJSON flow of river sections, 2.2 MB, read only when a publication changed it); each with its level, phenomenon and status tables. |
| `app/collectors/swic_registry.py`, `swic.py`, `swic_cap.py`, `swic_terms.py` | The services of the WMO registry: the registry (a day, with a bundled snapshot), each service's feeds and CAP documents (merged by `sender` and `identifier`, `references` followed, one request per document, a circuit per service), the CAP reader and the level and phenomenon tables, and the terms record of each service (verified or not, the credit, the services never read). See [ADR 0006](decisions/0006-wmo-registry-services.md). |
| `app/collectors/firms.py`, `app/services/fires.py`, `app/models/fires.py` | NASA FIRMS, from its static world files (no key): each file checked by its headers, read for what was added to it, kept when the server stalls (read timeout, deadline, backoff per source, resume with `Range`), parsed in a thread into typed columns sorted by latitude; with an optional key the area API is a fallback for a file that could not be read (the key redacted from every message and log line); the selection of a box, the grid per cell and the summary around a point; the models that describe the answers. Off with `VIGIE_ENABLE_FIRMS=0`. See [ADR 0007](decisions/0007-active-fires-from-nasa-firms.md). |
| `app/collectors/flood_air.py` | River discharge (GloFAS), air quality and dust, USGS volcanoes. |
| `app/collectors/weather.py` | Open-Meteo (primary), MET Norway (fallback), RainViewer (radar). |
| `app/collectors/marine.py`, `space_weather.py` | Open-Meteo Marine, NOAA SWPC Kp index. |
| `app/services/aggregator.py` | Orchestration: weather fallback chain, hazard fusion, `build_snapshot`, `build_point_response`, frame, GeoJSON, CAP, simulator. |
| `app/models/vigilance.py` | The `Vigilance` model (an official warning) and the answers built on it. |
| `app/geo.py`, `app/services/geoindex.py` | Polygons of a CAP message; the country of a point and the shape of a code (shapely), from the files of `app/data/`. |
| `app/services/vigilance.py` | `VigilanceService`: the warnings of a point or a country, and the changes to push. |
| `app/services/uncovered.py` | Why a country has no source on an instance: a stable code and one sentence per country, only for what was read ([`SOURCES.md`](../SOURCES.md), "Countries with no source"). |
| `app/security.py` | The optional admin token (`VIGIE_ADMIN_TOKEN`): a middleware that asks every request that is not a read for `Authorization: Bearer <token>`. |
| `app/data/`, `tools/build_geometries.py`, `tools/build_faults.py` | The bundled reference files and the scripts that rebuild them: the borders and NUTS shapes, and the named active faults (CC BY-SA 4.0, with its notice; licences: [`SOURCES.md`](../SOURCES.md)). |
| `app/models/places.py`, `app/services/places.py` | `near_places` of a hazard: the named active faults (`FaultIndex`, `shapely.STRtree`), and the volcanoes in alert (never the watched points: they are personal and the hazards are shared); the radii are settings (`PlaceSettings`). Calculated by Vigie, named in `derived`, never a severity ([ADR 0008](decisions/0008-sensitive-points.md)). |
| `app/models/earthquakes.py`, `app/services/earthquakes.py` | The earthquakes near a position (`GET /v1/earthquakes`): the earthquakes the feeds list now and those of the history, merged when two sources report one, within a radius and a window; no weather provider is asked. |
| `app/models/watch.py`, `app/services/watch.py` | The status of the watched points (`GET /v1/watched-points/status`): the events within each radius, and `quiet` only when every hazard source answered and has been read without a gap (`SourceReads`). |
| `app/services/geodesy.py` | Haversine distance, bearing, tsunami ETA, de-duplication of hazards across agencies. |
| `app/services/embedded_frame.py` | Serializes the snapshot (and an optional GPS point) to the text frame, with its CRC32. |
| `app/services/storage_wal.py` | SQLite in WAL mode: watchlist, watched points, hazard history, last snapshot, the runs of readings of the hazard sources. |
| `app/services/scheduler.py` | Optional periodic collection and differential push of new or worsening alerts. |
| `app/routers/dashboard.py` | Supervision page. |
| `tools/capture_frame.py` | Captures a real frame to a file. |
| `tools/check_live_feeds.py` | Asks each real provider once and checks the parsers still read the answer (weekly workflow). |
| `tools/measure_forecast_models.py` | A measure, not part of the application: scores `best_match` and several Open-Meteo models and their mean against observations at a fixed lead time (ledger of weighted calls, disk cache, no network to re-score). Report: [`measurements/forecast-models-2026-10.md`](measurements/forecast-models-2026-10.md); decision: [ADR 0010](decisions/0010-forecast-model-blend.md). |
| `scripts/run.ps1` | Starts the API on Windows. |
| `custom_components/vigie/`, `hacs.json`, `tests_ha/` | The Home Assistant integration (a client of `GET /v1/vigilances`, with `WS /v1/stream` as a hint to read again), its tests, and their CI. See [`home-assistant.md`](home-assistant.md). |
| `tools/fake_instance.py` | A fake instance (`/v1/health`, `/v1/vigilances`) with warnings added by hand, labelled `SIMULATION`, to try the integration. |
| `Dockerfile`, `scripts/docker_smoke.sh` | The image (non-root, health check) and the script that proves it answers. |

## Official warnings (vigilances)

`GET /v1/vigilances` gives the warnings that the national weather services publish (MeteoAlarm for 39 European
countries, the DWD for Germany, Environment and Climate Change Canada, MetService for New Zealand, the NWS for the
United States, and Vigicrues for the river floods of France, and, for more countries, the national services of the WMO
registry), for a position (`lat`, `lon`) or a country (`country`), as
JSON or GeoJSON. The embedded frame is **not changed**: the warnings have their own routes and the frame contract stays
[`embedded-frame.md`](embedded-frame.md).

```
 position ──> country (offline, bundled borders) ──> the source of the country: DWD (DE) / ECCC (CA) / MetService (NZ)
                                                     or the MeteoAlarm feed of the country (180 s cache) ───────┐
                                                 ├─> France only: Vigicrues, the river sections (a line each)   │
                                                 └─> NWS answer of the hazards (shared)                         ├─> warnings in force
 country= ──────────────────────────────────────────────────────────────────────────────────────────────────────┘        │
                                                                                                                         v
        keep those whose polygon, whose code's shape, or whose river line (within river_radius_km) holds the position
                              ──> drop the MeteoAlarm "flooding" a Vigicrues section says ──> CAP documents (once each)
```

The decisions and their reasons: [ADR 0002](decisions/0002-official-warnings-kept-intact-and-localized-offline.md),
[ADR 0003](decisions/0003-national-source-preferred-rivers-are-lines.md) (one source per country, a river is a line),
[ADR 0006](decisions/0006-wmo-registry-services.md) (the WMO registry: more countries, only the terms that were read),
[ADR 0010](decisions/0010-forecast-model-blend.md) (the forecast models: measured, the mean of eight for the minimum and the far maximum).
Rules that hold the whole model:

- **The official words stay intact.** The identifier, the times, the original level and phenomenon, the
  headline, the description and the instruction in every language the issuer wrote, the codes of the area and
  the link to the original are returned as given. The three fields the API derives (`status`, `level`,
  `phenomenon`) are named in `derived` on every warning and come from tables
  ([`data-sources.md`](data-sources.md)); what no table covers is `null`.
- **Every warning carries its credit**, and the answer carries the notice that MeteoAlarm obliges
  redistributors to publish (`disclaimer`).
- **What cannot be matched is said, not hidden.** A warning is matched to a position by its own polygon (exact),
  by the shape of one of its codes (simplified: a position within about 2 km of the edge of a shape counts as
  inside it, except when it lies inside another shape of the same code system), or, for a river section, by the
  distance to its line (`river_radius_km`, 5 km by default; the distance comes back as `distance_km`). A code with no shape on this
  instance cannot be matched: `area.localization` is `none`, and `coverage` counts those warnings
  (`warnings_not_matchable`) instead of letting an empty list read as "nothing to fear". Which codes have a
  shape, and why the others do not, is in [`SOURCES.md`](../SOURCES.md).
- **A provider that does not answer is reported** (`coverage[].status`: `stale` with the age of the last answer,
  or `unavailable`), never an all-clear, and it is not credited.
- **One source per country.** The national source answers for its country and MeteoAlarm's copy of it is not asked
  for (Germany); MeteoAlarm stands in only if the national service gives no answer. France has two sources, which
  say two things (the départements, the rivers); a MeteoAlarm "flooding" warning that a river section of the answer
  says at the same level or higher is left out ([`data-sources.md`](data-sources.md)). A service of the WMO registry
  answers only for a country that has no collector, except Spain (AEMET gives polygons where MeteoAlarm gives codes);
  each such service is its own line of `coverage`, and one that never answered is silent for the push, it ends nothing.
  `GET /v1/vigilances/countries` lists what an instance serves with its settings (`VIGIE_WMO_SOURCES`).
- **A country with no source says why.** The borders hold every country of Natural Earth (244, about 990 KiB), so a position in Japan, Australia
  or China finds its country; the answer then carries it in `uncovered` (`country`, `name`, `covered: false`, a stable `reason_code` and a
  one-sentence `reason`) and, when nothing at all is read for the position, in `note`. A position in no country (at sea, further than about 25 km
  from a coast) says that. The reasons are written only when they were read: Australia (the Bureau does not allow redistribution), Japan
  (a law reserves the giving of warnings to the JMA), the unverified or restrictive terms of the registry's services, Hong Kong and China (never
  read); any other country is `no_source`, which claims nothing more. `GET /v1/vigilances/countries?uncovered=true` lists every country, the
  served ones unchanged. A territory has its own code and is answered on its own.
- **Sober on the providers.** One request per needed source, conditional where the source supports it (DWD,
  MetService), a CAP document once and only for a warning that is returned, no request of its own for the NWS
  ([`data-sources.md`](data-sources.md) has the measured figures).

### Push on `/v1/stream`

A client sends `{"action": "follow", "lat": .., "lon": ..}` on the WebSocket and gets a `vigilance_state` at
once (what `GET /v1/vigilances` would answer); `{"action": "unfollow", ..}` stops it. With the scheduler on, the
changes are pushed as `vigilance_push` messages with `change` = `appeared` (a warning for the position that was
not there), `upgraded` (its level went up: `previous_level` says from what) or `ended` (it expired, was
cancelled or is no longer listed). The watched zones are followed by the scheduler too and their changes go to
every client (`point.zone_id`, `point.zone_name`). The first look at a position only sets the baseline: nothing
is pushed for what was already there; a source that does not answer never ends a warning, and does not silence the
other sources of the position (what it had said stands; if it answers for the first time after the baseline, that
first answer is its own baseline). A position is
rounded to 4 decimals (about 11 m), a connection follows 10 at most, and the positions live in memory with the
connection.

## Unknown values

Every parser keeps the provider's `None` / missing value as `None`, skips an item it cannot
classify (for example a hazard without an event time), or raises so that the caller falls back.
Nothing is padded, defaulted or copied from another field. At each layer:

- Weather: Open-Meteo, then MET Norway, then the last cached Open-Meteo answer (labelled
  `(stale cache)`), then **no weather**: a zone then has `current = null`, no nowcast and no
  forecast, and the frame sends its `ZONE` weather columns empty.
- Air quality, river discharge, marine, radar, space weather: each is optional; a failure is
  `null` in JSON and an empty field in the frame.
- Hazards: if no hazard source answers, the global status is unknown (`max_severity = null`)
  instead of an all-clear `GREEN`.
- Simulation: simulated alerts exist for testing (the simulator is off unless `VIGIE_ENABLE_SIMULATION=1`).
  They are labelled (`(SIMULATION)` in the title, `simulation_active` in the snapshot and the frame,
  CAP status `Exercise`).

## Credits

Each source asks for a credit line. `app/attribution.py` declares them once; the parsers of the single-source
blocks (marine, river discharge, air quality, space weather, and the weather bundles) set `attribution` where
the provider data is read, the aggregator sets it on the zones, the rain block and the point response, and a
hazard computes its own from its `sources` (a merged USGS + EMSC alert carries both credits). A block with
no data has `attribution: null`; a simulated alert or block is credited to no provider. GeoJSON and CAP
carry the credit too. The compact frame has none: its contract is fixed. `SOURCES.md` quotes the texts and
says whose words they are (the provider's, suggested by it, or ours) and what could not be verified.

## Data flow of `GET /v1/embedded/frame`

1. `build_snapshot`: in parallel, the hazards (5 feeds), the space weather, and for each watched
   zone the weather (with fallback) and the air quality.
2. If `lat` and `lon` are given, `build_point_response` adds the GPS position: weather, forecast,
   marine, river discharge, air quality, nearby hazards.
3. `serialize_embedded_frame` writes `HEAD`, the zones (`ZONE`, `RAIN5M`, `DAY`), the hazards
   (`HAZ`) and `END|<CRC32>`.

Every call goes through the collectors' caches, so a display polling every minute costs a few
provider calls per TTL, not per request.

## Provider calls

The number of calls to the providers must not grow with the number of clients. What keeps it flat:

- **Point grids.** The position sent upstream, and the cache key, are snapped to a grid coarser than
  the weather models: 0.05 degree (about 5 km) for the forecast and MET Norway, 0.1 degree for air
  quality, river discharge and marine conditions (`app/collectors/grid.py`, settings in the README).
  Clients in the same cell share one call and one cache entry. The client keeps its exact position:
  the response reports it and the distances to the hazards use it. Open-Meteo applies its own
  elevation correction to the requested point, so the temperature near a hill is the one of the cell
  centre, not of the exact spot: this is the price of the shared cell.
- **One cache entry per position, whatever the number of days.** The forecast is always asked for
  the longest length any route returns (10 days) and cached once per cell. The compact profiles
  (5 days: the frame, `compact=true`) slice the answer; the number of days is not part of any cache
  key. A watched zone read by a frame and by a web page used to cost two calls.
- **The forecast in two requests.** Open-Meteo weighs a request by its variables: the single forecast
  request (18 variables) weighed 1.8 calls. The current conditions and the 15-minute rain (11
  variables, refreshed every 15 minutes) and the daily forecast (7 variables, every hour) are now
  separate requests with their own cache entries; the weight of each request is computed from the
  published rule in `app/collectors/weights.py` (see `data-sources.md`).
- **A call budget per source.** Every request goes through `BaseCollector._get`, which counts it
  (and its weight) over a sliding window of 24 hours. A provider with a published quota (Open-Meteo,
  MET Norway, RainViewer; `app/collectors/budget.py`) never gets a request that would exceed it:
  the collector serves its expired cache instead and the responses say `stale`. The four Open-Meteo
  APIs share one budget. `GET /v1/providers` reports `calls_24h`, `weight_24h` and the quota.
- **TTLs from the sources' own rhythm.** Each source is cached for the time it takes to change, not
  for a round 5 minutes: the table with the evidence, the source URL and what could not be verified
  is [`data-sources.md`](data-sources.md). MET Norway is asked as its terms require: a conditional
  request (`If-Modified-Since` with the previous `Last-Modified`), and its answer is kept until the
  `Expires` header; a `304 Not Modified` keeps the cached value.
- **One upstream request for simultaneous callers.** `BaseCollector.fetch` runs the request of a
  cache key in one shared task: N clients asking for the same key at the same time send one request
  and all receive its answer or its error (each caller then applies its own stale policy). The
  circuit breaker counts that single failure once. A client that hangs up does not cancel the
  request for the others, and the answer is cached even when nobody waits for it any more.

## Persistence

SQLite in WAL mode, file `VIGIE_DB_PATH` or `vigie.db` in the repository root (git-ignored).
Tables: `monitored_zones` (the watchlist, seeded with five cities), `watched_points`, `hazards_history`,
`snapshot_cache`, `source_runs` (for each hazard source, since when it has been read without a gap). Older databases are migrated on start (a hazard without a value is stored as
`NULL`). A legacy `vigie_mondiale.db` is reused when `vigie.db` does not exist.

## Known limitations

- **Time zones.** A watched zone has a fixed configured UTC offset (`utc_offset_s`). It is used
  only when the weather provider gives no offset (MET Norway fallback, no weather). The seed
  values are the cities' standard-time offsets, so in summer Paris and San Francisco would be one
  hour off in that case. With Open-Meteo the real current offset is used.
- **No flood level from river discharge.** `flood_risk` gives the GloFAS discharges as they are;
  `flood_alert_level` is always null and does not raise `local_alert_level`. Until 2026-10-20 it was
  drawn from `anomaly_ratio` (highest ensemble member over the ensemble mean), which measures how far
  the forecast scenarios spread, not how high the river is: it said "yellow" for the Seine in Paris while
  the day's discharge was below the mean forecast. Open-Meteo gives no flood threshold or return
  period. Official flood warnings are in `/v1/vigilances` (Vigicrues, MeteoAlarm, NWS...).
- **Simulation and history.** `/v1/simulate` is not authenticated, and simulated alerts are also
  written to the SQLite hazard history (labelled `(SIMULATION)`). That is why the route is **off by
  default**: it answers 403 unless the instance sets `VIGIE_ENABLE_SIMULATION=1`
  (`GET /v1/health` reports `simulation_enabled`). Enable it only where trusted clients alone can reach
  the API. The rest of the API is not authenticated either (`POST /v1/zones`, `POST /v1/scheduler/trigger`):
  do not expose it to the Internet without a reverse proxy that does.
- **The admin token (optional).** With `VIGIE_ADMIN_TOKEN` set, **every request that is not a read** (anything but `GET`, `HEAD` and `OPTIONS`:
  `POST`, `PUT`, `PATCH`, `DELETE`) must carry `Authorization: Bearer <token>`, else the answer is 401 and the body is not read: today
  `POST /v1/zones`, `DELETE /v1/zones/{id}`, `POST /v1/scheduler/trigger`, `POST` and `DELETE /v1/watched-points` and `POST /v1/simulate`. Without the
  setting nothing changes: the network is trusted, as before. It is a middleware on the method (`app/security.py`), not a list of routes, so a route
  added later is covered without anyone remembering it, and `tests/test_admin_token.py` walks the routes of the application and fails if one with a write
  method answers without the token. The token is checked before the switches above (`VIGIE_ENABLE_SIMULATION`, `VIGIE_ENABLE_WATCHED_POINTS_WRITE`): it
  does not turn a route on, a disabled route stays 403. It is compared in constant time and appears nowhere: not in a log, not in an answer, not in
  `GET /v1/health` (which says `admin_token_set`). The reads never need it, nor does the WebSocket `/v1/stream` (a client following positions changes nothing
  on the instance). It is a shared secret in a header: over plain HTTP it can be read on the network, so on anything but a trusted network put TLS in front
  (a reverse proxy). The dashboard asks for it once, keeps it for the tab, and sends it with its two writes. The Home Assistant integration only reads
  (`GET` and the stream) and needs none.
- **Watched points.** A watched point is personal (a home, for example): it is listed by `GET /v1/watched-points` and its status only, and
  never in the hazards, which every client shares. `POST` and `DELETE /v1/watched-points` have no authentication either, so like `/v1/zones` and
  `/v1/simulate` **they must not be reachable from the Internet on a public instance: put the instance behind a reverse proxy that blocks
  them.** They are also **off by default**: 403 unless the instance sets `VIGIE_ENABLE_WATCHED_POINTS_WRITE=1` (`GET /v1/health` reports
  `watched_points_write_enabled`); the reads always work.
- **Official warnings, what is not matched.** Warnings that name their area with MeteoAlarm `EMMA_ID` codes only
  (Italy, Spain, Austria, Poland... Germany since the DWD answers for it) are listed by `country=` but cannot be matched to a position: their
  shapes have no stated licence and are not bundled ([`SOURCES.md`](../SOURCES.md)). NWS alerts that name UGC zones
  instead of a polygon are in the same case. A change of a warning that is not matched to any followed position is
  not pushed.
- **Official warnings, the issuer's name.** The terms of MeteoAlarm want the name of the national service shown when
  one country is concerned. The name comes with the CAP document (`senderName`); a warning returned before its
  document is read, or whose document the server does not have, carries the credit of EUMETNET – MeteoAlarm only.
- **Duplicate title and value.** When USGS and EMSC report the same earthquake the merged alert
  keeps the USGS title and the highest magnitude, so the title can show a slightly lower
  magnitude than the value.
- **Providers.** The terms of several providers could not be verified; see
  [`data-sources.md`](data-sources.md).
