# Vigie API

Backend of the Vigie displays. It aggregates world natural hazards (earthquakes, tsunamis,
volcanoes, cyclones, tornadoes, floods, fires, geomagnetic storms) and weather (current
conditions, 15-minute rain, forecast, marine, river discharge, air quality) and serves them to:

- the ESP32-P4 displays of the Vigie project (Waveshare 7B, M5Stack Tab5), through a compact text
  frame: [`docs/embedded-frame.md`](docs/embedded-frame.md);
- any other client, through JSON, GeoJSON (IETF RFC 7946) and OASIS CAP 1.2.

> **No fabricated data.** When a provider does not supply a value, the API reports it as
> unknown (`null` in JSON, an empty field in the frame). It never shows a plausible default. See
> [ADR 0001](docs/decisions/0001-two-repos-no-fabricated-data-frame-format.md).

This is a personal, **non-commercial** project, built with the help of AI. It is shared as is,
without any promise of support or availability.

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/embedded/frame` | Compact text frame for the displays ([contract](docs/embedded-frame.md)). |
| `GET /v1/snapshot` | Watched zones (weather) and world hazards, JSON under 12 KiB. |
| `GET /v1/point?lat=&lon=` | Weather at one GPS position, marine (`marine=true`), river discharge, air quality, space weather, nearby hazards. |
| `GET /v1/geojson/hazards` | Active hazards as GeoJSON (RFC 7946). |
| `GET /v1/cap/alerts` | Active hazards as OASIS CAP 1.2. |
| `GET/POST /v1/zones`, `DELETE /v1/zones/{id}` | Watched zones (the watchlist). A zone needs its `utc_offset_s`. |
| `GET /v1/history` | Hazard history stored in SQLite. |
| `GET /v1/health`, `GET /v1/providers` | Health, circuit breaker and cache state of each provider. |
| `GET/POST /v1/scheduler/...` | State of the optional background scheduler, run one cycle now. |
| `POST /v1/simulate` | Inject a **simulated** scenario for testing (always labelled as a simulation). |
| `WS /v1/stream` | WebSocket with the initial state and alert pushes. |
| `GET /dashboard` | Web supervision page. |
| `GET /docs` | Interactive OpenAPI documentation. |

## Run

Python 3.12 or newer (the CI covers 3.12 and 3.13).

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt     (Windows)
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

On Windows, `scripts\run.ps1` does all of this (see below). Then open
`http://localhost:8080/dashboard` or `http://localhost:8080/v1/embedded/frame`.

### Configuration

All settings are optional environment variables.

| Variable | Default | Meaning |
| --- | --- | --- |
| `VIGIE_DB_PATH` | `vigie.db` in the repository root (git-ignored) | SQLite file (watchlist, history, last snapshot). |
| `VIGIE_SCHEDULER` | `0` | `1` starts the background scheduler (refresh every 90 s, WebSocket push). |
| `VIGIE_CONTACT` | none | E-mail address or URL added to the `User-Agent` sent to the providers. MET Norway and NOAA require an identifying contact: set it. |
| `VIGIE_CAP_SENDER` | `vigie-api` | `sender` of the CAP alerts. |
| `VIGIE_GRID_FORECAST_DEG` | `0.05` | Grid (degrees, about 5 km) the position is snapped to before the Open-Meteo forecast and MET Norway are asked. |
| `VIGIE_GRID_AIR_QUALITY_DEG` | `0.1` | Same, for Open-Meteo Air Quality (about 11 km). |
| `VIGIE_GRID_FLOOD_DEG` | `0.1` | Same, for Open-Meteo Flood (GloFAS). |
| `VIGIE_GRID_MARINE_DEG` | `0.1` | Same, for Open-Meteo Marine. |

No API key is needed by any provider. A grid value outside 0.0001 to 10 degrees is ignored (the
default is used). Only the request sent to the provider and the cache key use the snapped point: the
response still reports the exact position of the client, and the distances to the hazards use it.

### Run on Windows (the PC that serves the display)

The display fetches `http://<PC address>:8080/v1/embedded/frame` (for example
`http://192.168.0.155:8080/v1/embedded/frame`). Give the PC a fixed address in the router so
this URL does not change.

1. **Start the API**, from any folder, in a normal (not administrator) PowerShell:

   ```
   powershell -NoProfile -ExecutionPolicy Bypass -File H:\AuxFilsDesIdees\vigie_api\scripts\run.ps1
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
   Invoke-WebRequest http://192.168.0.155:8080/v1/health -UseBasicParsing
   ```

4. **Start it at logon** with Task Scheduler (nothing is created by the repository). Task
   Scheduler, *Create Task...* (not *Basic Task*):

   - *General*: name `Vigie API`; "Run only when user is logged on".
   - *Triggers*: New, "At log on", specific user: you.
   - *Actions*: New, "Start a program". Program: `powershell.exe`. Arguments:
     `-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "H:\AuxFilsDesIdees\vigie_api\scripts\run.ps1" -LogFile "H:\AuxFilsDesIdees\vigie_api\logs\vigie-api.log"`.
     Start in: `H:\AuxFilsDesIdees\vigie_api`.
   - *Settings*: "If the task fails, restart every" 1 minute, 3 attempts; untick "Stop the task if
     it runs longer than". The `H:` drive must be available at logon. If the repository folder is
     renamed, update the paths above. The server output goes to `logs\vigie-api.log`
     (git-ignored); stop the server by ending the task in Task Scheduler.

## Develop

```
python -m pytest              # no test touches the network
python -m ruff check .        # lint
python tools/capture_frame.py frame.txt     # write a REAL frame (uses the live providers)
```

`tools/capture_frame.py` starts the application in-process, fetches
`/v1/embedded/frame?device_type=waveshare-7b` from the real providers and writes it to the given
file. It is used to produce the test fixture of the display repository.

CI (`.github/workflows/ci.yml`) runs ruff and pytest on Python 3.12 and 3.13 for every push to
`main` and every pull request.

## Data sources

| Domain | Source | Terms |
| --- | --- | --- |
| Earthquakes | USGS, EMSC / SeismicPortal, GDACS | see [`docs/data-sources.md`](docs/data-sources.md) |
| Disasters | GDACS, NOAA NWS, USGS Volcano Hazards Program | |
| Space weather | NOAA SWPC | |
| Weather | **Open-Meteo** (primary), MET Norway (fallback), RainViewer (radar) | |
| Marine, river discharge, air quality | Open-Meteo Marine, Flood (Copernicus GloFAS), Air Quality (Copernicus CAMS) | |

**Open-Meteo's free API may only be used for non-commercial purposes**
([terms](https://open-meteo.com/en/terms), checked on 2026-10-20), with an attribution link
wherever its data is displayed, and within 10,000 calls per day, 5,000 per hour and 600 per
minute. This project is personal and non-commercial; **do not use it commercially without a paid
Open-Meteo plan** (and check the other providers' terms). The licence, attribution and limits of
every source, and what could not be verified, are in [`docs/data-sources.md`](docs/data-sources.md).

## Documentation

- [`docs/architecture.md`](docs/architecture.md): components, data flow, limits.
- [`docs/embedded-frame.md`](docs/embedded-frame.md): the frame contract (single source of truth).
- [`docs/data-sources.md`](docs/data-sources.md): providers, terms, cache and call volume.
- [`docs/decisions/`](docs/decisions): architecture decision records.
- [`CHANGELOG.md`](CHANGELOG.md), [`AGENTS.md`](AGENTS.md) (guide for AI coding agents).
