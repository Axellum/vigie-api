# 0028. The weather of France from the Météo-France models, read behind Vigie

- Status: accepted
- Date: 2026-11-18

## Context

[ADR 0026](0026-self-hosted-weather-gateway-behind-vigie.md) kept Open-Meteo's public API for the current conditions and the
forecast, because the Open-Meteo server of the gateway (synchronised with Météo-France models only) gave `null` for the pressure, the
weather code and the UV index, and measured no more accurate than the public API. It said the move would be reconsidered once the
missing fields were filled, mainly for the licence (Météo-France's models are open data; Open-Meteo's free API is non-commercial).
The project's owner decided on 2026-11-18 to do it for France, without losing a field.

What was found (2026-11-18, the Open-Meteo server and the gateway of the project, a Météo-France key):

- The server's AROME France HD files hold only temperature, humidity, wind components, gusts, precipitation, CAPE and the three cloud
  layers: no pressure, no weather code, no total cloud cover. The **public** API has the same gap for that model: its weather code in
  France does not come from AROME either.
- `models=meteofrance_seamless` is Open-Meteo's blend of the Météo-France models, variable by variable: AROME HD (about +51 hours),
  then ARPEGE Europe (about +4 days). Asked through the gateway's pass-through (`GET /v1/forecast`), it gives every field Vigie shows
  except the UV index and the rain probability, and the days after the fourth are `null`. The pressure and the weather code are
  ARPEGE's (the weather code is Open-Meteo's calculation from ARPEGE's fields, not a Météo-France product).
- Météo-France's `DPPaquetENVIRONNEMENT` (model PHEALTH, package `UVQ`) gives the **daily maximum UV index** of today and tomorrow on
  a 0.025-degree grid, real sky and clear sky, one 1.9 MB GRIB2 file per run, a run every 3 hours. No hourly curve.
- No Météo-France model in the subscribed APIs gives a precipitation probability (the ensembles, PE-AROME, do: a later lot).

## Decision

1. **For a point in a box around metropolitan France and Corsica**, when `VIGIE_METEO_GATEWAY_URL` is set (and
   `VIGIE_METEO_GATEWAY_WEATHER` is not `0`), the current conditions, the 15-minute rain and the first four days (weather code, minimum
   and maximum temperature, rain total, maximum wind) come from `models=meteofrance_seamless` through the gateway
   (`app/collectors/meteofrance_weather.py`). A neighbour inside the box (Barcelona, Geneva) is served from the same models, which cover it.
2. **The three missing fields**: the pressure and the weather code from ARPEGE (through the blend); the **UV index** from PHEALTH
   (`app/collectors/meteofrance_uv.py`, read by Vigie itself, one reader per Météo-France dataset): its maximum for today and tomorrow
   as `uv_max`, and the index of the current hour **calculated** as `maximum × (sin(Sun height) / sin(noon Sun height)) ** 1.2`.
3. **Open-Meteo's public API stays** for what the models do not give: the rain probability (every day), the days after the fourth, the
   `uv_max` of the days after tomorrow, the hourly cloud cover (the aurora estimate), every point outside the box, and as the fallback
   when the gateway fails. Its daily request is the same one the fallback asks for, so it is made with the gateway's, not after it.
4. **The PIAF rain is unchanged** (ADR 0026): it replaces the 15-minute rain when it answers.
5. **The gateway tells whether it answers from an expired cache** (`X-Cache-Status`, vigie-meteo#6): Vigie refuses a `stale`
   answer, as it refuses any gateway failure, and asks the public API.
6. **Credits**: `meteofrance_weather` and, when its grid answered, `meteofrance_uv`; the Open-Meteo credit only when its daily part
   contributed. Both new sources are `not_verified` for commercial use (see `app/commercial.py`).

## Measured (2026-11-18)

Current conditions, 24 French towns, against the nearest Météo-France station of `DPPaquetObs` (15 km at most), one pass at about 08:00
local time (06:45 UTC) with Open-Meteo's public API (`best_match`, the "before") and the blend read through the gateway (the "after"):

| | Public API | Blend (gateway) | n |
| --- | --- | --- | --- |
| Temperature, mean absolute error | 0.95 °C | 1.16 °C | 24 |
| Wind speed, mean absolute error | 5.7 km/h | 4.3 km/h | 16 |
| Sea-level pressure, mean absolute error | 0.44 hPa | 0.59 hPa | 12 |
| Relative humidity, mean absolute error | 6.6 points | 8.3 points | 16 |
| Fields `null` over the 24 towns | none | UV index (24), then filled by PHEALTH | |

The two are within what one pass can tell apart: the blend is slightly worse on the temperature, the pressure and the humidity and
better on the wind. **Not a gain in accuracy**: the reason to move is the licence and having one reader per dataset.

- Days one to three, 24 towns, blend minus public API (no observation to judge them yet): maximum temperature mean absolute difference 0.57 °C
  (bias -0.28), minimum 0.73 °C (-0.25), rain total 3.3 mm (+1.7), maximum wind 4.4 km/h (+4.1). A rain code (51 and above) on
  both in 49 cases of 72, on the public API only in 3, on the blend only in 8, on neither in 12.
- The weather code of the current hour differs from the public API's in 19 of 24 towns at this hour; it is ARPEGE's (11 km), and it
  can name drizzle where AROME's own precipitation is zero (Strasbourg at 07:00 UTC: code 55, current precipitation 0.0 mm and 0.0 mm in each of the next four quarters). Not corrected: any rule that
  overrides it would be ours, not Météo-France's or Open-Meteo's.
- UV of the current hour against Open-Meteo's hourly clear-sky curve (6 towns, 2 days, 08:00 to 16:00 UTC, 102 points): mean absolute
  error **0.23** UV index, at most 0.6, with the maximum taken from that same curve. The calculated index is too high in the morning and
  too low in the afternoon (the curve is not symmetrical about solar noon). It was **not** compared with a UV sensor.
- Time: a point already read costs what it did (the answer of `/v1/point` from the cache, 2 to 4 ms on a developer's PC with the
  gateway on a distant network, before and after); a point read for the first time asks the gateway, Open-Meteo's daily part and
  the UV grid together.

## Consequences

- The project's weather of France depends on the Oracle machine (as the PIAF rain already did); when it stops, Vigie asks the public API,
  with no error shown, after the circuit breaker of the collector opens (3 failures, then 2 minutes).
- The current conditions can be up to twice the cache time old (15 minutes in the gateway, 15 minutes in Vigie): 30 minutes at most, the
  models being updated every hour.
- The gateway's SQLite cache keeps one row per query: two per 0.05-degree cell asked. It has no purge; the disk of the machine has room
  for the cells of France (a few hundred MB at the very most), to watch.
- The weather code and the pressure of the current hour are ARPEGE's: coarser than AROME's 1.5 km.
- The index UV of the current hour is an approximation (a power law of the Sun height from Météo-France's maximum of the day): it
  follows the Sun and not the clouds of the hour. For the days after tomorrow `uv_max` is still Open-Meteo's.
- Rain total and maximum wind of the first four days are the models'; the rain probability beside them is the public API's, from
  another model: the two can disagree.

## Not verified

- The licence chain of the files an Open-Meteo server synchronises from Open-Meteo's bucket, and the terms of `DPPaquetENVIRONNEMENT`
  (the API names the source, no licence): both `not_verified` in `app/commercial.py`.
- Accuracy over several days and times of day (one pass in the morning); the daily values against observations.
- The UV index against a UV sensor; the exponent 1.2 is chosen, not fitted.
- A reboot of the VPS with the tunnel (no reboot since the tunnel was set up).
