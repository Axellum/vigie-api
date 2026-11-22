# 0043. Global observed satellite precipitation map layer (NASA GIBS GPM IMERG)

- Status: accepted
- Date: 2026-11-22
- Number: reserved as 0043 (`python scripts/prochain_adr.py` confirmed 0043 free after 0040–0042).

## Context

Vigie already exposes:

- High-resolution ground weather radar mosaics over France and its overseas territories (`GET /v1/radar/{area}`, ADR 0030 and ADR 0037).
- Global, European and French **forecast** precipitation overlays (`GET /v1/rain/field`, ADR 0041) and France PIAF +1h nowcast tiles (`GET /v1/radar/france/nowcast`, ADR 0041).

Outside France and its overseas territories, there was no open, keyless, commercial-safe layer of **observed** precipitation. RainViewer is restricted to personal and educational use only (`forbidden` in `app/commercial.py`), and global ground-radar composites are generally commercial or restricted.

### Verified live NASA GIBS GPM IMERG probe (2026-11-22)

Before writing any collector code, NASA's Global Imagery Browse Services (GIBS) WMTS and WMS endpoints and official documentation/policy pages were probed directly from Python on **2026-11-22 (~18:40–18:44 UTC)** without any API key or authentication:

| Check | Exact URL probed | Measured result (2026-11-22) |
| --- | --- | --- |
| WMTS Capabilities | `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/1.0.0/WMTSCapabilities.xml` | HTTP `200`, `5,851,536` bytes. Lists `IMERG_Precipitation_Rate_30min` (`Precipitation Rate, 30-minute avg. (IMERG)`), `TileMatrixSet = GoogleMapsCompatible_Level6` (zooms 0–6, 256×256 PNG, `MatrixWidth = MatrixHeight = 2^z`), `WGS84BoundingBox = [-180, -85.051129, 180, 85.051129]`, `Default = 2026-11-22T12:30:00Z`. |
| WMTS DescribeDomains (24 h window) | `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/1.0.0/IMERG_Precipitation_Rate_30min/default/GoogleMapsCompatible_Level6/all/2026-11-21T18:00:00Z--2026-11-22T18:59:59Z.xml` | HTTP `200`, **`380` bytes** XML (`<Domain>2026-11-21T18:00:00Z/2026-11-22T12:30:00Z/PT30M</Domain>`). Same latest timestamp on `/nrt/` (`370` bytes for today). |
| Observed latency | Measured at `2026-11-22T18:41:06Z` vs latest frame `2026-11-22T12:30:00Z` | **6 h 11 min (`371` min)** behind real time at probe time. GPM IMERG documentation (`https://gpm.nasa.gov/data/imerg`) states Early Run nominal latency ~4 hours after observation time (plus GIBS ingestion delay -> typically 4 to 6 hours). |
| WMTS tile `z=0, y=0, x=0` | `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/IMERG_Precipitation_Rate_30min/default/2026-11-22T12:30:00Z/GoogleMapsCompatible_Level6/0/0/0.png` | HTTP `200`, **`6,682` bytes**, `image/png`, `256×256`, 8-bit indexed (`color_type=3`), 256-entry `PLTE` with `tRNS=[0, 0]` (entries 0=`No Data` and 1=`<0.1 mm/h` transparent). Decoded pixels: `2,924 / 65,536` (`4.5%`) opaque wet pixels, `95.5%` transparent; sample wet RGB values `(123,209,0)`, `(69,192,0)`, `(247,217,0)` matching `GPM_Precipitation_Rate.xml`. |
| WMTS tiles `z=1, 2, 6` | `.../1/0/1.png`, `.../2/1/2.png`, `.../6/22/32.png` for `2026-11-22T12:30:00Z` | HTTP `200`, `4,642` / `5,822` / `3,583` bytes (`256×256` indexed PNG with `tRNS`). |
| Missing future timestamp | `.../default/2026-11-22T18:30:00Z/GoogleMapsCompatible_Level6/0/0/0.png` | HTTP `404` (`196` bytes). |
| WMS GetMap comparison | `https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi?...&WIDTH=1024&HEIGHT=1024&FORMAT=image/png&TRANSPARENT=true&TIME=2026-11-22T12:30:00Z` | HTTP `200`, `110,504` bytes (`1024×1024` RGBA PNG). Larger and dynamically rendered vs pre-generated 3.5–6.7 KB WMTS tiles. |
| Official colour scale | `https://gibs.earthdata.nasa.gov/colormaps/v1.3/GPM_Precipitation_Rate.xml` | HTTP `200`, `44,832` bytes. Defines transparent `No Data` (ref 0) and `[-INF, 0.1)` (ref 1), 110-step continuous `Rain Rate` (`0.1` to `≥ 53.0` mm/h, green → yellow → orange → red → dark red) and 110-step continuous `Snow Rate` (`0.1` to `≥ 53.0` mm/h, cyan → blue → purple). |
| Licence & data use policy | `https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance` | HTTP `200`. States: *"Unless the content is marked with a use restriction or license, data provided from a NASA-led mission are licensed as Creative Commons Zero (CC0)."* Conditions: acknowledge NASA as source; do not suggest or imply NASA endorsement of any commercial product, service, or activity. |
| GIBS acknowledgment | `https://nasa-gibs.github.io/gibs-api-docs/` | HTTP `200`. Requests exact wording: *"We acknowledge the use of imagery provided by services from NASA's Global Imagery Browse Services (GIBS), part of NASA's Earth Science Data and Information System (ESDIS)."* No numeric rate limit is published on the GIBS docs read on 2026-11-22. |

## Decision

1. **Use NASA GIBS WMTS `IMERG_Precipitation_Rate_30min` (`GoogleMapsCompatible_Level6`) proxied through Vigie API** (`app/collectors/nasa_gibs_imerg.py`, `app/services/rain_observed.py`, `app/models/rain_observed.py`):
   - **Why WMTS tiles over WMS GetMap**: pre-generated tiles are 3.5–6.7 KB (vs ~110 KB dynamically rendered global WMS), align natively with MapLibre `raster` tile sources (`min_zoom: 0`, `max_zoom: 6`, clean overzoom above z=6 where z=6 is already ~2.4 km/px vs IMERG's ~10 km native grid), and require zero image processing or disk storage on the server.
   - **Frame discovery**: query the ~380-byte OGC WMTS `DescribeDomains` XML endpoint over the last 30 hours, cached for 10 minutes (`TTL_SECONDS = 600`, at most 6 tiny XML requests/hour), keeping the latest 12 thirty-minute frames (6 hours of observed precipitation, oldest first).
   - **Privacy & proxy safeguards**: clients never contact `gibs.earthdata.nasa.gov` directly. `GET /v1/rain/observed/tiles/{stamp}/{z}/{x}/{y}.png` validates `stamp` (`^\d{12}$`) against the strict allowlist of frames currently listed by `GET /v1/rain/observed`, validates `0 <= z <= 6` and `0 <= x, y < 2^z`, maps standard XYZ `{z}/{x}/{y}` to GIBS WMTS `{TileMatrix}/{TileRow}/{TileCol}` (`{z}/{y}/{x}`), coalesces concurrent identical tile requests in flight, validates PNG magic bytes and max size (`200 KB`), and stores tiles in a bounded in-memory LRU cache (`600` entries ≈ 3–4 MB max RAM, zero extra disk).
   - **Caching**: `GET /v1/rain/observed` sends `Cache-Control: public, max-age=300`; tile responses send `Cache-Control: public, max-age=86400, immutable` (historical 30-minute tiles do not change).
   - **Sobriety & circuit breaker**: all upstream requests flow through `BaseCollector._get`, counted in `calls_24h` and capped by a self-imposed ceiling (`600` req/min held to 90% = `540`/min in `app/collectors/budget.py`). Because NASA GIBS retains historical IMERG tiles, a temporary `DescribeDomains` outage serves the last cached frame list marked `stale: true` rather than failing the map.
   - **Configuration**: enabled by default with no API key; disabled cleanly with `VIGIE_OBSERVED_RAIN=0` (`status: "disabled"`, listed in `disabled_sources` on `GET /v1/health`, tile route returns 404).
   - **Honest communication**: `resolution_km: 10`, `typical_latency_minutes: 300`, exact NASA GIBS rain (`legend`) and snow (`snow_legend`) scales, exact NASA GIBS acknowledgment (`attribution`), CC0 / U.S. public domain `licence`, and a clear `note` stating it is a satellite precipitation estimate with ~10 km resolution and 4–6 h typical delay, not a ground radar and not for safety.

## Checked

- Live NASA GIBS WMTS capabilities, `DescribeDomains`, 4 sample tiles across zooms 0–6, missing-timestamp 404 behavior, WMS GetMap comparison, `GPM_Precipitation_Rate.xml` v1.3 colormap, NASA ESDIS Data Use Guidance, GIBS API Docs, and GPM IMERG page probed on 2026-11-22.
- Offline unit and API route tests (`tests/test_rain_observed.py`) using recorded small fixtures (`tests/fixtures/nasa_gibs_imerg/`) verify XML parsing (bare-date start, multi-period, single instant, empty/malformed), stamp allowlist, XYZ→WMTS `{z}/{y}/{x}` mapping, LRU eviction, in-flight coalescing, circuit breaker, budget enforcement, attribution, commercial status (`allowed`), and disabled/unavailable/ok route responses without network access.

## Not verified

- Whether NASA GIBS enforces an undocumented per-IP rate limit beyond standard fair-use thresholds (no numeric limit was found on the GIBS documentation pages read on 2026-11-22; mitigated with 10-minute metadata caching, 600-tile LRU cache, in-flight coalescing, and a self-imposed 600 req/min ceiling).
- Typical variance of GIBS ingestion latency across the day (one live measurement of 6 h 11 min was taken on 2026-11-22 at 18:41 UTC; GPM Early Run nominal latency is stated as ~4 hours on `gpm.nasa.gov/data/imerg`).
