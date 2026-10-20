# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- **Call sobriety, point grids.** The position sent to Open-Meteo (forecast, air quality, flood,
  marine) and to MET Norway, and the cache key, are snapped to a grid: 0.05 degree for the forecast
  and MET Norway, 0.1 degree for air quality, flood and marine (`VIGIE_GRID_FORECAST_DEG`,
  `VIGIE_GRID_AIR_QUALITY_DEG`, `VIGIE_GRID_FLOOD_DEG`, `VIGIE_GRID_MARINE_DEG`). It used to be
  0.01 degree, finer than the weather models. The response still reports the exact position and the
  distances to the hazards still use it.
- **Call sobriety, cache key without `days`.** The forecast is always requested for 10 days and
  cached once per position; the compact profiles slice it. A frame (5 days) and a full page
  (10 days) used to cost one Open-Meteo call each per position. `fetch_weather_with_fallback` no
  longer takes `days`.
- **Embedded frame contract** ([`docs/embedded-frame.md`](docs/embedded-frame.md)), same column
  order and counts:
  - `DAY` field 2 is the ISO date (`2026-10-20`) instead of a French weekday abbreviation.
  - `DAY` field 7 is the real daily maximum precipitation probability (Open-Meteo
    `precipitation_probability_max`); field 9 is that day's maximum UV, no longer the current UV
    repeated.
  - Unknown values are empty fields. A zone without weather has `ZONE` fields 6 to 23 empty;
    `HEAD` fields 2 and 6 to 11 can be empty (Kp unknown, no hazard source answered).
  - `RAIN5M` repeats each 15-minute rate three times instead of smoothing it; the GPS zone has
    its own forecast and local UTC offset and is named `GPS position`.
- The documentation, comments, log and error messages and the dashboard are in English; the
  dashboard drops the "DEFCON" wording and shows unknown values as `n/a`.
- `DailyForecast.d` (weekday) is replaced by `date` (ISO 8601); new `rain_probability_pct` and
  `uv_max`. All measurement fields of the weather, marine, air quality and space weather models
  are optional (`null` = unknown). `GET /v1/point` has a `daily` forecast and `utc_offset_s`.
- `POST /v1/zones` requires `utc_offset_s` (there is no silent default of zero).
- Open-Meteo is asked for the sea-level pressure (`pressure_msl`) and the current UV index.
- The RainViewer radar frame is the latest **observed** one (past frames only); the free
  RainViewer API has had no nowcast since 2026-01-01. The short-term rain of the frame comes only
  from Open-Meteo `minutely_15` (interpolated from hourly data outside Central Europe and North
  America, as Open-Meteo documents).
- The CAP `area.circle` radius is `0` (the affected radius is not known), the CAP `language` is
  `en-US`, `sender` is `vigie-api` or `VIGIE_CAP_SENDER`, and `web` is no longer set.
- Default database file is `vigie.db` (an existing `vigie_mondiale.db` is still used).
- The background scheduler starts only with `VIGIE_SCHEDULER=1` (it was never started before).
- Hazard sources that return no magnitude (NOAA NWS alerts, volcanoes without a VEI) keep the
  value unknown; the hazard history column is nullable (older databases are migrated).

### Fixed

- No fabricated data. Removed the invented offline weather ("MET Norway / Offline Safety Cache"
  computed from the latitude), the air quality index fixed at `22` / `20`, the Kp index defaulting
  to `2.0` (the live NOAA file was never read correctly), the default temperature, humidity,
  pressure, wind, minimum and maximum temperatures, the gusts and UV estimated for MET Norway,
  the zero rain forecast, the invented marine conditions, the invented radar timestamp, the
  invented NOAA wind speeds and magnitudes, the volcano VEI derived from a colour code, and the
  default depth of 10 km.
- MET Norway: the daily weather code comes from each day's forecast symbols and days are
  grouped in the zone's local days.
- The GPS zone of the frame no longer repeats the first watched zone's forecast.
- The USGS volcano feed was never parsed (its real fields are `vName`, `long`, `colorCode`,
  `sentUtc`).
- Hazards are no longer reported as "all clear" (`GREEN`) when no hazard source answered.
- The dashboard escapes the text coming from the providers.
- Started with the latest FastAPI, the application crashed on the removed `openapi_version`
  argument.

### Added

- `docs/` : architecture, frame contract, data sources and terms, decision records.
- `tests/test_embedded_frame_contract.py` pins the frame contract; no test reaches the network.
- Air quality (EAQI) for every watched zone.
- `scripts/run.ps1` (Windows start script), `tools/capture_frame.py` (capture a real frame),
  a GitHub Actions workflow (ruff and pytest on Python 3.12 and 3.13), ruff configuration.
- Environment variables `VIGIE_CONTACT`, `VIGIE_CAP_SENDER`, `VIGIE_SCHEDULER`.

### Removed

- The French work kit `docs/travail/` and the audit `docs/audits/`.
