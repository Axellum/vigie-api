# 0040. Hourly weather, 24-hour pressure history and hourly air quality on `/v1/point`

- Status: accepted
- Date: 2026-11-22
- Number: given by `scripts/prochain_adr.py` on 2026-11-22.

## Context

`GET /v1/point` returned current conditions, a 15-minute rain nowcast and daily forecasts, which was enough for embedded displays
and a daily overview, but web clients displaying meteograms, barometer trends, UV/solar curves and air-quality evolution need
hourly series up to 72 hours ahead (plus the past 24 hours of sea-level pressure for the barometer trend).

Three constraints shaped how these series could be added without degrading existing clients or exceeding the Open-Meteo free quota:

1. **Compact budget (16 KiB).** `GET /v1/point` defaults to `compact=true` so embedded terminals stay under 16 KiB
   (`tests/test_snapshot_budget.py`), and `GET /v1/embedded/frame` stays under 8 KiB (`docs/embedded-frame.md`). Seventy-two hours
   of multi-variable weather and air quality add roughly 20 KiB of JSON and cannot be sent in the compact profile.
2. **Open-Meteo free quota (10,000 weighted calls/day, 9,000 budget).** In `tests/test_call_sobriety.py` (1,000 clients on 50
   points plus 5 watched zones), the baseline Open-Meteo weight was `8,450.1` out of `9,000.0`, leaving only `549.9` weighted
   calls of headroom. Adding 17 hourly variables to the hourly `daily` request (called 24 times/day for all 55 cells) would have
   added roughly `+1,850` weighted calls and blown past the quota.
3. **Honest nulls across providers (ADR 0001).** Open-Meteo provides all 17 hourly weather variables; Météo-France's
   `meteofrance_seamless` (AROME HD + ARPEGE Europe behind the `vigie-meteo` gateway, ADR 0028) provides 15 of them but has no UV
   index and no precipitation probability; MET Norway Locationforecast 2.0 compact provides a smaller subset and no past hours.

## Decision

1. **Optional top-level fields on `PointResponse`, populated only when `compact=false`:**
   - `hourly: Optional[list[HourlyWeatherPoint]]` (up to 72 hours, starting at the current UTC hour): `time_utc` (UTC epoch
     seconds, matching `SurfHour`, `ActivityHour` and `ShortTermStep`), `temp_c`, `feels_like_c`, `dew_point_c`,
     `precipitation_mm`, `precipitation_probability_pct`, `wind_kmh`, `wind_gust_kmh`, `wind_deg`, `cloud_cover_pct`,
     `cloud_low_pct`, `cloud_mid_pct`, `cloud_high_pct`, `pressure_hpa`, `uv_index`, `shortwave_wm2`, `weather_code`, `is_day`.
   - `pressure_history: Optional[list[PressureHistoryPoint]]` (up to 24 hours before the current UTC hour, non-null
     `pressure_hpa` entries only): `time_utc`, `pressure_hpa`.
   - `air_hourly: Optional[list[AirHourlyPoint]]` (up to 72 hours, starting at the current UTC hour): `time_utc`, `pm2_5`,
     `pm10`, `ozone`, `nitrogen_dioxide`, `dust`, `european_aqi`.
   - When `compact=true` (the default), all three fields are `None`, and `GET /v1/snapshot` and `GET /v1/embedded/frame` are
     untouched.

2. **Dedicated 6-hour hourly cache on `OpenMeteoCollector`:**
   - The existing `now` (15 min, weight `1.1`) and `daily` (1 h, weight `1.0`) requests stay unchanged.
   - A separate cached part (`om-hourly:<cell>`, TTL `21,600 s` = 6 hours, matching the 6-hour run cadence of global NWP models
     such as ECMWF IFS and GFS) asks for the 17 hourly variables with `forecast_hours=72` and `past_hours=24` (weight `1.7`; even
     counting `past_hours=24` as 1 extra day, `7 + 1 = 8 <= 14`, so the Open-Meteo days factor in `app/collectors/weights.py`
     stays `1.0`).
   - It is fetched only when `compact=false` and only when neither the France gateway nor the overseas gateway answered. When a
     self-hosted Open-Meteo URL is configured (ADR 0039), `get_open_meteo` routes it there first, outside the public quota.
   - Failure of the optional hourly fetch leaves `hourly` and `pressure_history` as `None` without failing `/v1/point`.

3. **Zero extra requests for France (`MeteoFranceWeatherCollector`), overseas (`MeteoFranceOverseasCollector`) and MET Norway:**
   - `MeteoFranceWeatherCollector` adds the 15 supported hourly variables (excluding `uv_index` and `precipitation_probability`,
     which AROME/ARPEGE do not provide) plus `forecast_hours=72` and `past_hours=24` to its existing 1-hour `daily` gateway
     request (still 2 gateway calls per cell, and zero public Open-Meteo hourly calls in France).
   - `MeteoFranceOverseasCollector` converts the hourly AROME-OM steps it already fetches into `HourlyWeatherPoint`, leaving
     unsupported fields `None`.
   - `MetNorwayCollector` extracts the fields its compact `timeseries` actually supplies (`temp_c`, `next_1_hours`
     `precipitation_mm`, `wind_kmh`, `wind_deg`, `cloud_cover_pct`, `pressure_hpa`, `next_1_hours` `weather_code` and explicit
     `_day`/`_night` `is_day`), leaving all other fields and `pressure_history` as `None`.
   - `split_hourly_series` anchors the split between `pressure_history` (`[now - 24h, now)`) and `hourly` (`[now, now + 72h)`)
     at the current UTC hour so cached series stay aligned with "now" without duplicating the boundary hour.

4. **Single-request hourly air quality on `OpenMeteoAirQualityCollector` without variable duplication:**
   - Because CAMS Air Quality has 1-hour resolution, `current` now requests only `us_aqi,uv_index` (2 variables), while `hourly`
     requests `pm2_5,pm10,ozone,nitrogen_dioxide,dust,european_aqi` (6 variables) over `forecast_days=4` (plus the 6 pollen
     variables inside CAMS Europe).
   - `parse_response` reads `european_aqi`, `pm2_5`, `pm10` and `dust` from `current` when present (preserving backward
     compatibility with recorded payloads) and falls back to the `current.time` hour in `hourly`.
   - Call weight stays `1.0` outside Europe (8 variables, unchanged) and rises from `1.2` to `1.4` inside CAMS Europe (14
     variables), with zero extra HTTP requests.
   - The parsed 72-hour air series is carried in a private attribute on `AirQualityStatus` and exposed as `PointResponse.air_hourly`
     only when `compact=false`, keeping `AirQualityStatus` and compact payloads unchanged.

## Measured (2026-11-22)

`tests/test_call_sobriety.py` (1,000 clients on 50 points + 5 watched zones, including 80 web clients polling `compact=false`
across 40 points):

| Metric | Before | After | Limit / budget |
| --- | ---: | ---: | ---: |
| Open-Meteo total HTTP calls | 7,927 | 8,087 (+160) | — |
| Open-Meteo daily weighted calls | 8,450.1 | 8,760.5 (+310.4) | 9,000.0 budget (10,000 quota) |
| Budget refusals | 0 | 0 | 0 |

## Consequences

- Web clients wanting `hourly`, `pressure_history` and `air_hourly` must pass `compact=false` to `GET /v1/point`.
- For non-French Open-Meteo points, `hourly` can be up to 6 hours old from cache; slicing at the current UTC hour keeps the
  first returned entry aligned with "now" (between 66 and 72 upcoming hours remain in the cached window).
- In metropolitan France, `hourly[].uv_index` and `hourly[].precipitation_probability_pct` are `null` because AROME/ARPEGE do not
  provide them; solar radiation (`shortwave_wm2`) is populated.
- In `tests/test_open_meteo_weight.py` and `tests/test_pollens.py`, the expected CAMS Europe air quality call weight updates from
  `1.2` to `1.4`.
