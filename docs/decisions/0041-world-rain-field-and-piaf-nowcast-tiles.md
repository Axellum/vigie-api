# 0041. Global forecast precipitation map overlays (AROME / ARPEGE) and PIAF +1h nowcast tiles

- Status: accepted
- Date: 2026-11-22
- Number: reserved as 0041 (0040 reserved concurrently for point hourly series).

## Context

The map already shows observed precipitation radar mosaics over France and its overseas territories (`GET /v1/radar/{area}`,
ADR 0030 and ADR 0037) and forecast wind fields worldwide (`GET /v1/wind/field`, ADR 0038). Two precipitation views were still
missing:

1. **Global forecast precipitation** (worldwide, with higher resolution over Europe and France) across several forecast lead
   times (`+1h` to `+24h`), under an open licence that allows commercial reuse and requires zero extra disk storage.
2. **Short-term rain nowcast tiles (+5 to +60 min) over metropolitan France**, continuing the observed 5-minute radar animation
   seamlessly using the Météo-France PIAF grids that the `vigie-meteo` gateway already holds in memory.

### Verified Météo-France WCS precipitation measurements (2026-11-22)

Before writing any collector code, `GetCapabilities`, `DescribeCoverage` and `GetCoverage` were queried directly on the three
WCS services of Météo-France's API portal (`public-api.meteofrance.fr/public`) on 2026-11-22 (~12:53–12:56 UTC):

| Level | WCS service | Coverage ID pattern | Grid & area | Max `_PT1H` lead | GRIB2 size & time | Rendered RGBA PNG (`thin=2`, Mercator) |
| --- | --- | --- | --- | --- | --- | --- |
| `world` | `arpege/1.0/wcs/MF-NWP-GLOBAL-ARPEGE-025-GLOBE-WCS` | `TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE___<run>_PT1H` | 1440 x 721, 0.25° (globe, 0..359.75° E) | 48 h | 2.03 MB (~0.32 s) | 23.0 KB (`721 x 360`, 45 ms) |
| `europe` | `arpege/1.0/wcs/MF-NWP-GLOBAL-ARPEGE-01-EUROPE-WCS` | same | 741 x 521, 0.1° (32 W–42 E, 20–72 N) | 102 h | 0.75 MB (~0.15 s) | 9.1 KB (`371 x 260`, 5 ms) |
| `france` | `arome/1.0/wcs/MF-NWP-HIGHRES-AROME-0025-FRANCE-WCS` | same | 1121 x 717, 0.025° (12 W–16 E, 37.5–55.4 N, bitmap corners) | 38 h (09 UTC run) | 1.40 MB (~0.18 s typical) | 14.7 KB (`561 x 358`, 12 ms) |

Key findings from decoding the returned GRIB2 messages with `eccodes`:

- **Direct 1-hour accumulations (no subtraction needed)**: all three services expose `_PT1H` coverages with `shortName="tp"`,
  `units="kg m**-2"` (= mm over 1 hour = mm/h), `stepType="accum"`, `stepUnits=1` (hours), and `startStep == endStep - 1`
  (e.g. `stepRange="7-8"`, `"30-31"`, `"4-5"`, `"27-28"`). No `height(...)` subset is required; only `subset=time(<ISO>)`.
- **Same open licence as wind**: AROME and ARPEGE WCS APIs are published under **Licence Ouverte 2.0 (Etalab)** (commercial
  reuse allowed with source and run date attribution, no alteration).

## Decision

1. **Global + Europe + France forecast rain field (`app/collectors/meteofrance_rain_field.py`, `app/services/rain_field.py`)**:
   - Modeled on `meteofrance_wind_field.py` (ADR 0038): optional collector enabled whenever `VIGIE_METEOFRANCE_RAIN_API_KEY` or
     `VIGIE_METEOFRANCE_API_KEY` is set, disabled with `VIGIE_RAIN_FIELD=0`.
   - Fetches six forecast lead steps (`+1h, +2h, +3h, +6h, +12h, +24h` from the nearest UTC hour) once an hour across the three
     levels (at most 18 requests/hour, with concurrency capped at 4 and unchanged `(level, valid, run)` frames reused in memory),
     well within the portal's 50 req/min free plan.
   - Verifies `endStep - startStep == 1` on every decoded GRIB2 message so a multi-hour cumulative file can never be misread as
     mm/h.
   - **Web Mercator RGBA PNG rendering**: rows are projected onto linear Web Mercator Y (clamped to ±85° latitude for `world`,
     wrapping longitude seamlessly from -180° to +180°) so a MapLibre `type: "image"` layer renders the overlay without latitude
     distortion (an equirectangular image on Europe 20–72° N would otherwise be shifted by ~6.7° at mid-latitudes). Dry pixels
     (`< 0.1 mm/h`) and missing bitmap corners (AROME) are fully transparent; wet pixels share Vigie's 9-class radar palette.
     All PNGs are encoded with `numpy` and `zlib` (no Pillow) and kept in a bounded in-memory dict (72 images ≈ ~1 MB max, 0 disk).
   - Routes:
     - `GET /v1/rain/field` (`Cache-Control: public, max-age=300`): returns `status`, `base_utc`/`base_at`, `stale`,
       `projection: "mercator"`, `levels` (`world`, `europe`, `france`, each with `model`, `bounds`, `width`, `height`,
       `min_zoom`, `run_utc`/`run_at`, `frames: [{stamp, valid_utc, valid_at, lead_hours, url, size_bytes}]`), `legend`,
       `licence`, `attribution`, and `note` stating it is a forecast and not a radar measure.
     - `GET /v1/rain/field/{level}/{stamp}.png` (`Cache-Control: public, max-age=86400, immutable`): serves the cached RGBA PNG.

2. **PIAF +1h rain nowcast tile relay (`app/collectors/meteofrance_radar.py`, `app/services/radar_france.py`)**:
   - Relays `GET /v1/rain-nowcast/frames` and `GET /v1/rain-nowcast/tiles/{epoch}/{z}/{x}/{y}.png` from the configured
     `vigie-meteo` gateway (0 extra disk, 0 extra Météo-France requests).
   - Uses a dedicated TTL cache and inflight coalescing on `MeteoFranceRadarCollector` so an older gateway lacking the nowcast
     routes returns `status: "unavailable"` without tripping observed radar's circuit breaker.
   - Routes:
     - `GET /v1/radar/france/nowcast` (`Cache-Control: public, max-age=60`): returns `status`, `source_model`, `run_utc`/`run_at`,
       `frames: [{epoch, time, lead_minutes}]`, `tile_url_template`, `bounds`, `min_zoom`, `max_zoom`, `legend`, `note`,
       `licence` (honestly marked `not_verified` as with `meteofrance_piaf`), and `attribution`.
     - `GET /v1/radar/france/nowcast/tiles/{epoch}/{z}/{x}/{y}.png` (`Cache-Control: public, max-age=3600, immutable`): validates
       `epoch` against the gateway's active frame list, serves `TRANSPARENT_PNG` outside `bounds` without calling the gateway,
       and caches relayed tiles in the bounded in-memory LRU cache.

## Checked

- Live Météo-France WCS capabilities, `DescribeCoverage` and `GetCoverage` verified on 2026-11-22 for all three services
  (`world`, `europe`, `france`), confirming `_PT1H` direct 1-hour accumulations (`kg m**-2`), payload sizes, timings and
  rendered RGBA PNG sizes (9–23 KB).
- Small real cropped GRIB2 answers saved in `tests/fixtures/meteofrance_rain_field/` and tested without network access
  (`tests/test_rain_field.py`), along with PIAF nowcast frame parsing, tile relay safeguards, and error handling
  (`tests/test_meteofrance_radar.py`).

## Not verified

- How often a newly published run lacks a far lead hour (`+24h`) at the moment it is requested; individual missing leads are
  skipped cleanly while the remaining steps are served.
- The licence and commercial reuse terms of Météo-France's PIAF API (the catalogue entry states `PAYANT: Non` without naming a
  licence, so `meteofrance_piaf_tiles` remains `not_verified` in `app/commercial.py`).
