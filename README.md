# Vigie API

*[Lire ce document en français](README.fr.md)*

A personal project, made with the help of AI and shared in case it helps someone. It comes as is, with
no promise of support, availability or accuracy.

It gathers world natural hazards (earthquakes, tsunamis, volcanoes, cyclones, tornadoes, floods, fires,
geomagnetic storms) and weather (current conditions, 15-minute rain, forecast, marine, river discharge,
air quality) from public sources, and serves them to:

- the ESP32-P4 displays of the Vigie project (Waveshare 7B, M5Stack Tab5), through a compact text
  frame: [`docs/embedded-frame.md`](docs/embedded-frame.md);
- any other client, through JSON, GeoJSON (IETF RFC 7946) and OASIS CAP 1.2.

> **This is not an official warning service.** The alerts are relayed from public feeds. They can be
> late, incomplete or wrong, and must not be the only thing a decision rests on. **GDACS alerts are
> automatic and do not replace official warnings.** Follow the warnings of your national authorities.

> **No fabricated data.** When a provider does not supply a value, the API reports it as unknown
> (`null` in JSON, an empty field in the frame). It never shows a plausible default. See
> [ADR 0001](docs/decisions/0001-two-repos-no-fabricated-data-frame-format.md).

## Licence, data and credits

The code is under the [MIT licence](LICENSE). **The data is not covered by it**: it belongs to each
provider and stays under that provider's own terms. [`SOURCES.md`](SOURCES.md) lists, for each source,
the licence, the terms, the credit it asks for, its quota, and what could not be verified.

- Every block of data in the JSON responses carries an `attribution` field with the credit its source
  asks for (the compact frame has no such field). The texts are declared in
  [`app/attribution.py`](app/attribution.py).
- **Open-Meteo's free API may only be used for non-commercial purposes**
  ([terms](https://open-meteo.com/en/terms)), within 10,000 calls a day, with a link to Open-Meteo wherever
  its data is shown. This project is personal and non-commercial. **Do not use it commercially without
  a paid Open-Meteo plan**, and check the terms of the other providers.
- RainViewer's free API is for personal and educational use only. Turn it off on an instance that relays
  its data to others (`VIGIE_ENABLE_RAINVIEWER=0`; the Docker image does).

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/embedded/frame` | Compact text frame for the displays ([contract](docs/embedded-frame.md)). |
| `GET /v1/snapshot` | Watched zones (weather) and world hazards, compact JSON. |
| `GET /v1/point?lat=&lon=` | Weather at one GPS position, marine (`marine=true`), river discharge, air quality, space weather, nearby hazards. |
| `GET /v1/geojson/hazards` | Active hazards as GeoJSON (RFC 7946). |
| `GET /v1/cap/alerts` | Active hazards as OASIS CAP 1.2. |
| `GET/POST /v1/zones`, `DELETE /v1/zones/{id}` | Watched zones (the watchlist). A zone needs its `utc_offset_s`. |
| `GET /v1/history` | Hazard history stored in SQLite. |
| `GET /v1/health`, `GET /v1/providers` | Health, circuit breaker and cache state of each provider, and its calls, weight and quota over 24 hours. |
| `GET/POST /v1/scheduler/...` | State of the optional background scheduler, run one cycle now. |
| `POST /v1/simulate` | Inject a **simulated** scenario for testing (always labelled as a simulation). **Off by default**: it answers 403 unless `VIGIE_ENABLE_SIMULATION=1`. |
| `WS /v1/stream` | WebSocket with the initial state and alert pushes. |
| `GET /dashboard` | Web supervision page. |
| `GET /docs` | Interactive OpenAPI documentation. |

**The API has no authentication.** Run it on a trusted network. Do not expose it to the Internet as is:
anyone who can reach it can add or delete watched zones and trigger collection cycles. Put a reverse proxy
that authenticates in front of it if you must.

## Run

Python 3.12 or newer (the CI covers 3.12 and 3.13).

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt     (Windows)
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

On Windows, `scripts\run.ps1` does all of this (see below). Then open
`http://localhost:8080/dashboard` or `http://localhost:8080/v1/embedded/frame`.

### With Docker

```
docker build -t vigie-api .
docker run --rm -p 8080:8080 -v vigie-data:/data -e VIGIE_CONTACT=you@example.org vigie-api
```

The image (`python:3.13-slim`, built for linux/amd64 by the CI) runs as a non-root user, keeps its
database in the `/data` volume and has a health check on `/v1/health`. RainViewer is off in the image; add
`-e VIGIE_ENABLE_RAINVIEWER=1` for a personal instance. The publication workflow pushes
`ghcr.io/axellum/vigie-api` when a version tag (`v*`) is created; until a tag exists, build the image
yourself.

### Configuration

All settings are optional environment variables.

| Variable | Default | Meaning |
| --- | --- | --- |
| `VIGIE_DB_PATH` | `vigie.db` in the repository root (git-ignored); `/data/vigie.db` in the Docker image | SQLite file (watchlist, history, last snapshot). |
| `VIGIE_SCHEDULER` | `0` | `1` starts the background scheduler (refresh every 90 s, WebSocket push). |
| `VIGIE_ENABLE_SIMULATION` | `0` | `1` enables `POST /v1/simulate` (and the dashboard buttons). The route has no authentication and writes the simulated alerts into the hazard history: enable it only on an instance that trusted clients alone can reach. |
| `VIGIE_ENABLE_RAINVIEWER` | `1`; `0` in the Docker image | `0` stops asking RainViewer (the radar fields of `/v1/point` stay unknown). Its free API is for personal and educational use: set `0` on an instance that serves other people. |
| `VIGIE_CONTACT` | none | E-mail address or URL added to the `User-Agent` sent to the providers. MET Norway and NOAA require an identifying contact: set it. |
| `VIGIE_CAP_SENDER` | `vigie-api` | `sender` of the CAP alerts. |
| `VIGIE_GRID_FORECAST_DEG` | `0.05` | Grid (degrees, about 5 km) the position is snapped to before the Open-Meteo forecast and MET Norway are asked. |
| `VIGIE_GRID_AIR_QUALITY_DEG` | `0.1` | Same, for Open-Meteo Air Quality (about 11 km). |
| `VIGIE_GRID_FLOOD_DEG` | `0.1` | Same, for Open-Meteo Flood (GloFAS). |
| `VIGIE_GRID_MARINE_DEG` | `0.1` | Same, for Open-Meteo Marine. |
| `VIGIE_BUDGET_FRACTION` | `0.9` | Part of each published provider quota (Open-Meteo 10,000 calls a day, MET Norway, RainViewer) the API lets through. Beyond it the cache is served, flagged stale. |

No API key is needed by any provider. A grid value outside 0.0001 to 10 degrees is ignored (the
default is used). Only the request sent to the provider and the cache key use the snapped point: the
response still reports the exact position of the client, and the distances to the hazards use it.

### Calls to the providers

The calls to the providers should not grow with the number of clients: they depend on the number of
distinct 5 km cells asked for and on how fast each source really changes (grids, one cache entry per
cell, one request for simultaneous callers, TTLs from each source's rhythm, a call budget; see
[`docs/architecture.md`](docs/architecture.md) and [`docs/data-sources.md`](docs/data-sources.md)).
`tests/test_call_sobriety.py` plays a simulated day with the scheduler on and its 5 default zones,
and 1,000 clients on 50 points (20 each, polling every 30 minutes, 10 points are harbours that also ask
for the marine conditions; no two points share a cell, so the grids do not help), against a simulated
upstream, and counts the requests each source receives:

| Source | Calls a day, before | after |
| --- | ---: | ---: |
| Open-Meteo forecast | 17,212 | 6,167 |
| Open-Meteo air quality | 7,182 | 1,320 |
| Open-Meteo river discharge | 6,122 | 200 |
| Open-Meteo marine | 2,216 | 240 |
| **Open-Meteo, weighted** (the free quota is 10,000 a day) | **46,502** | **8,412** |
| USGS earthquakes, EMSC | 1,980 each | 1,393 each |
| NOAA NWS alerts | 1,069 | 705 |
| GDACS | 676 | 474 |
| RainViewer | 662 | 144 |
| NOAA SWPC | 403 | 286 |
| USGS volcanoes | 212 | 144 |

"Before" is `main` at commit 1864e25 (same simulation, measured once); "after" is this code. The
heaviest Open-Meteo hour weighs 2,305 before and 412 after (quota 5,000), the heaviest minute 497
and 231 (quota 600). With clients polling every 15 minutes instead, before becomes 54,766 and after
8,667: the load of the clients hardly changes the "after". The 55 distinct cells of this scenario use
84 % of the free quota; beyond that the budget stops the requests and serves the cache, flagged stale.
Play it yourself: `python -m tests.simulation` (about 20 seconds).

### Run on Windows (a PC that serves the display)

The display fetches `http://<PC address>:8080/v1/embedded/frame`. Give the PC a fixed address in the
router so this URL does not change. Below, `<repository folder>` is the folder where you cloned this
repository.

1. **Start the API**, from any folder, in a normal (not administrator) PowerShell:

   ```
   powershell -NoProfile -ExecutionPolicy Bypass -File <repository folder>\scripts\run.ps1
   ```

   The script creates `.venv` in the repository on the first run, installs `requirements.txt`
   (again only when the file changes) and starts `uvicorn app.main:app --host 0.0.0.0 --port 8080`.
   Options: `-Port`, `-BindAddress`, `-LogFile <file>`, `-NoInstall`. Stop it with Ctrl+C.

2. **Allow the display to reach it.** Windows Firewall blocks inbound connections by default. Run
   this one line **once, in PowerShell opened as administrator** (it opens TCP 8080 on the
   Private network profile, for the local subnet only):

   ```
   New-NetFirewallRule -DisplayName "Vigie API 8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Private -RemoteAddress LocalSubnet
   ```

   The home network must be set as *Private* in Windows (Settings, Network and Internet). To
   remove the rule later: `Remove-NetFirewallRule -DisplayName "Vigie API 8080"`.

3. **Check from another device** on the same network:

   ```
   Invoke-WebRequest http://<PC address>:8080/v1/health -UseBasicParsing
   ```

4. **Start it at logon** with Task Scheduler (nothing is created by the repository). Task
   Scheduler, *Create Task...* (not *Basic Task*):

   - *General*: name `Vigie API`; "Run only when user is logged on".
   - *Triggers*: New, "At log on", specific user: you.
   - *Actions*: New, "Start a program". Program: `powershell.exe`. Arguments:
     `-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "<repository folder>\scripts\run.ps1" -LogFile "<repository folder>\logs\vigie-api.log"`.
     Start in: `<repository folder>`.
   - *Settings*: "If the task fails, restart every" 1 minute, 3 attempts; untick "Stop the task if
     it runs longer than". If the repository folder is on another drive, that drive must be available
     at logon. If the repository folder is renamed or moved, update the paths above. The server output
     goes to `logs\vigie-api.log` (git-ignored); stop the server by ending the task in Task Scheduler.

## Develop

```
python -m pytest              # no test touches the network
python -m ruff check .        # lint
python tools/capture_frame.py frame.txt     # write a REAL frame (uses the live providers)
python tools/check_live_feeds.py            # ask each real provider once, check the parsers still read it
```

`tools/capture_frame.py` starts the application in-process, fetches
`/v1/embedded/frame?device_type=waveshare-7b` from the real providers and writes it to the given
file. It is used to produce the test fixture of the display repository.

CI (`.github/workflows/`):

- `ci.yml`, on every push to `main` and every pull request: ruff and pytest on Python 3.12 and 3.13, and a
  job that builds the Docker image, runs it and asks `/v1/health` and `/v1/embedded/frame`.
- `secret-scan.yml`: gitleaks over the whole git history, on every pull request and push to `main`.
- `live-feeds.yml`: every Monday and on demand, never in a pull request, `tools/check_live_feeds.py` asks
  each real provider once. A red run means a provider changed its format or removed an endpoint.
- `publish-image.yml`: pushes the Docker image to `ghcr.io/axellum/vigie-api` when a `v*` tag is pushed.

## Data sources

| Domain | Source |
| --- | --- |
| Earthquakes | USGS, EMSC / SeismicPortal, GDACS |
| Disasters | GDACS, NOAA NWS, USGS Volcano Hazards Program |
| Space weather | NOAA SWPC |
| Weather | Open-Meteo (primary), MET Norway (fallback), RainViewer (radar) |
| Marine, river discharge, air quality | Open-Meteo Marine, Flood (GloFAS), Air Quality (CAMS) |

Licence, terms, credit, quota and clauses of each source, and what is not verified: [`SOURCES.md`](SOURCES.md).
How each one is cached and what it costs in calls: [`docs/data-sources.md`](docs/data-sources.md).

## Documentation

- [`SOURCES.md`](SOURCES.md): the sources, their licences, credits and quotas.
- [`docs/architecture.md`](docs/architecture.md): components, data flow, limits.
- [`docs/embedded-frame.md`](docs/embedded-frame.md): the frame contract (single source of truth).
- [`docs/data-sources.md`](docs/data-sources.md): how each source is used, cache and call volume.
- [`docs/decisions/`](docs/decisions): architecture decision records.
- [`CHANGELOG.md`](CHANGELOG.md), [`AGENTS.md`](AGENTS.md) (guide for AI coding agents).
