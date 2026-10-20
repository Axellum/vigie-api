# Architecture

## Overview

```
 USGS  EMSC  GDACS  NOAA NWS  USGS Volcano     Open-Meteo (forecast, marine, flood, air)   MET Norway   RainViewer   NOAA SWPC
   \     |     |       |         /                       |                                     |             |          |
    +----+-----+-------+--------+--------------+---------+-------------------------------------+-------------+----------+
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
| `app/collectors/base.py` | `BaseCollector`: HTTP client, circuit breaker (`CLOSED`, `OPEN`, `HALF_OPEN`), TTL cache, health. |
| `app/collectors/seismic.py` | USGS and EMSC earthquakes. |
| `app/collectors/disasters.py` | GDACS and NOAA NWS alerts. |
| `app/collectors/flood_air.py` | River discharge (GloFAS), air quality and dust, USGS volcanoes. |
| `app/collectors/weather.py` | Open-Meteo (primary), MET Norway (fallback), RainViewer (radar). |
| `app/collectors/marine.py`, `space_weather.py` | Open-Meteo Marine, NOAA SWPC Kp index. |
| `app/services/aggregator.py` | Orchestration: weather fallback chain, hazard fusion, `build_snapshot`, `build_point_response`, frame, GeoJSON, CAP, simulator. |
| `app/services/geodesy.py` | Haversine distance, bearing, tsunami ETA, de-duplication of hazards across agencies. |
| `app/services/embedded_frame.py` | Serializes the snapshot (and an optional GPS point) to the text frame, with its CRC32. |
| `app/services/storage_wal.py` | SQLite in WAL mode: watchlist, hazard history, last snapshot. |
| `app/services/scheduler.py` | Optional periodic collection and differential push of new or worsening alerts. |
| `app/routers/dashboard.py` | Supervision page. |
| `tools/capture_frame.py` | Captures a real frame to a file. |
| `scripts/run.ps1` | Starts the API on Windows. |

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
- Simulation: simulated alerts exist for testing. They are labelled (`(SIMULATION)` in the
  title, `simulation_active` in the snapshot and the frame, CAP status `Exercise`).

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

## Persistence

SQLite in WAL mode, file `VIGIE_DB_PATH` or `vigie.db` in the repository root (git-ignored).
Tables: `monitored_zones` (the watchlist, seeded with five cities), `hazards_history`,
`snapshot_cache`. Older databases are migrated on start (a hazard without a value is stored as
`NULL`). A legacy `vigie_mondiale.db` is reused when `vigie.db` does not exist.

## Known limitations

- **Time zones.** A watched zone has a fixed configured UTC offset (`utc_offset_s`). It is used
  only when the weather provider gives no offset (MET Norway fallback, no weather). The seed
  values are the cities' standard-time offsets, so in summer Paris and San Francisco would be one
  hour off in that case. With Open-Meteo the real current offset is used.
- **Flood level.** `flood_alert_level` is a heuristic (ratio of the maximum to the mean forecast
  discharge with fixed thresholds), not an official flood warning.
- **Simulation and history.** `/v1/simulate` is not authenticated, and simulated alerts are also
  written to the SQLite hazard history (labelled `(SIMULATION)`). Do not expose the API outside
  the local network.
- **Duplicate title and value.** When USGS and EMSC report the same earthquake the merged alert
  keeps the USGS title and the highest magnitude, so the title can show a slightly lower
  magnitude than the value.
- **Providers.** The terms of several providers could not be verified; see
  [`data-sources.md`](data-sources.md).
