# Embedded frame contract

`GET /v1/embedded/frame` returns a compact text frame decoded by the ESP32-P4 display
firmware (separate repository, the Vigie display). **This file is the single source of
truth of the contract.** It is pinned by `tests/test_embedded_frame_contract.py`: when a test
there has to change, change this document in the same commit and tell the display repository.

Generator: `app/services/embedded_frame.py`.

## Request

```
GET /v1/embedded/frame?device_type=waveshare-7b&lat=47.75&lon=-3.36&marine=true&max_hazards=12
```

| Parameter | Default | Meaning |
| --- | --- | --- |
| `device_type` | `waveshare-7b` | Display profile: `waveshare-7b`, `tab5`, `marine`, `web`, `generic`. |
| `lat`, `lon` | none | Optional GPS position. When both are given, a first zone `GPS` is added. |
| `marine` | `false` | Add waves, swell and current to the `GPS` zone (also implied by `device_type=marine`). |
| `max_hazards` | `12` (1 to 30) | Maximum number of `HAZ` lines. |

The response is `text/plain; charset=utf-8`.

## General rules

1. UTF-8 text, one record per line, every line ends with `\n` (never `\r\n`).
2. Fields are separated by `|`. A `|` inside a text is replaced by `/`, line breaks by spaces.
3. **An empty field means "unknown".** The API never replaces a missing value by a plausible
   number (no `0`, no `15.0`, no `1013`). `0` means a measured zero. A decoder must keep
   empty fields (do not use `strtok`, which merges consecutive separators).
4. Every line type has a fixed number of fields, **tag included**. Fields are positional
   (index 0 is the tag).
5. Numbers use `.` as decimal separator, no thousands separator, a fixed number of decimals
   per column (given below). Temperatures are in degrees Celsius, speeds in km/h.
6. The last line is `END|<CRC32>`.

## Line order

```
HEAD
ZONE, RAIN5M, DAY x n      <- the GPS zone, only when lat and lon are given
ZONE, RAIN5M, DAY x n      <- one block per watched zone, in zone id order
HAZ x m
END
```

Each `ZONE` is immediately followed by its own `RAIN5M` (always present, possibly empty) and
then its `DAY` lines (none when the zone has no weather).

## `HEAD`

`HEAD` has 13 fields.

| Index | Name | Format / unit | Unknown |
| --- | --- | --- | --- |
| 0 | tag | `HEAD` | |
| 1 | `server_utc` | UTC epoch seconds | never |
| 2 | `solar_kp` | planetary Kp index, `0.0` to `9.0`, 1 decimal | empty when NOAA SWPC could not be read |
| 3 | reserved | constant `0` | |
| 4 | `version` | payload version, currently `1.1` | never |
| 5 | `device_type` | profile served | never |
| 6 | `max_severity` | `GREEN`, `YELLOW`, `ORANGE` or `RED`: the highest severity among all active hazards | empty when no hazard source answered |
| 7 | `red_count` | integer, all active hazards (not only the `HAZ` lines sent) | empty with field 6 |
| 8 | `orange_count` | integer | empty with field 6 |
| 9 | `yellow_count` | integer | empty with field 6 |
| 10 | `gps_degraded` | `1` when Kp >= 6, else `0` | empty with field 2 |
| 11 | `hf_radio_blackout_risk` | `1` when Kp >= 5, else `0` | empty with field 2 |
| 12 | `simulation` | `1` while a simulated scenario is active, else `0` | never |

`GREEN` with zero counts means "at least one hazard source answered and reported nothing".
Empty fields 6 to 9 mean "no hazard source answered": it is **not** an all-clear. When only
some sources answered the frame cannot say so; see `GET /v1/health` and `GET /v1/snapshot`
(`global_status.hazard_sources_ok`).

## `ZONE`

`ZONE` has 24 fields. A zone is either the `GPS` position or a watched zone (`z0`, `z1`, ...).

| Index | Name | Format / unit | Unknown |
| --- | --- | --- | --- |
| 0 | tag | `ZONE` | |
| 1 | `zone_id` | at most 12 characters | never |
| 2 | `name` | at most 32 characters | never |
| 3 | `lat` | degrees, 4 decimals | never |
| 4 | `lon` | degrees, 4 decimals | never |
| 5 | `utc_offset_s` | seconds east of UTC, integer | empty for the `GPS` zone when no weather provider gave it |
| 6 | `temp_c` | 1 decimal | empty |
| 7 | `feels_like_c` | 1 decimal | empty (MET Norway gives none) |
| 8 | `humidity_pct` | integer 0 to 100 | empty |
| 9 | `pressure_hpa` | mean sea-level pressure, 1 decimal | empty |
| 10 | `wind_kmh` | 1 decimal | empty |
| 11 | `gust_kmh` | 1 decimal | empty (MET Norway gives none) |
| 12 | `wind_deg` | direction the wind comes from, integer 0 to 359 | empty |
| 13 | `weather_code` | WMO code (`0` clear, `61` rain, `95` thunderstorm...) | empty |
| 14 | `uv` | UV index, 1 decimal | empty |
| 15 | `aqi` | European air quality index, integer | empty when the air quality provider failed |
| 16 | `rain_eta_min` | minutes before rain in the next hour (`0`, `15`, `30`, `45`); `-1` = dry for the whole hour | empty when the nowcast is unknown |
| 17 | `rain_max_mm_h` | peak intensity of the next hour, 1 decimal | empty with field 16 |
| 18 | `rain_15m_csv` | four values separated by `,`: mm per 15-minute step, 2 decimals | empty with field 16 |
| 19 | `wave_m` | significant wave height, 2 decimals | empty |
| 20 | `wave_period_s` | 1 decimal | empty |
| 21 | `wave_dir_deg` | integer 0 to 359 | empty |
| 22 | `swell_m` | primary swell height, 2 decimals | empty |
| 23 | `current_kt` | surface current in knots, 2 decimals | empty |

Rules:

- **A zone without weather** (no provider answered) keeps fields 0 to 5 and has fields 6 to 23
  **all empty**. Its `RAIN5M` is empty and it has no `DAY` line.
- Fields 19 to 23 are filled only for the `GPS` zone when marine data was requested and
  available. Watched zones always send them empty.
- The nowcast (fields 16 to 18) is all-or-nothing: the four 15-minute values are known or
  the three fields are empty. MET Norway (the fallback) has no 15-minute nowcast.
- The `GPS` zone is named `GPS position`; its forecast and offset are those of the GPS
  position, never those of a watched zone.

## `RAIN5M`

`RAIN5M` has 3 fields: `RAIN5M|zone_id|v0,v1,...,v11`.

Twelve rain rates in mm/h, one per 5 minutes over the next hour, 2 decimals. The provider
only supplies four 15-minute amounts, so each amount is converted to a rate (amount x 4) and
repeated for its three 5-minute steps; nothing is interpolated or extrapolated. The field is
empty when the nowcast is unknown (same condition as `ZONE` field 16).

## `DAY`

`DAY` has 10 fields. One line per forecast day, in chronological order, normally 5 days.

| Index | Name | Format / unit | Unknown |
| --- | --- | --- | --- |
| 0 | tag | `DAY` | |
| 1 | `zone_id` | the zone the day belongs to | never |
| 2 | `date` | local calendar day, ISO 8601 `YYYY-MM-DD` (not a weekday name: the display computes the weekday) | never |
| 3 | `weather_code` | WMO code of the day (the most severe of the day) | empty |
| 4 | `tmin_c` | 1 decimal | empty |
| 5 | `tmax_c` | 1 decimal | empty |
| 6 | `rain_mm` | daily total, 1 decimal | empty |
| 7 | `rain_probability_pct` | maximum probability of precipitation of the day, integer 0 to 100 | empty (MET Norway gives none) |
| 8 | `wind_max_kmh` | 1 decimal | empty |
| 9 | `uv_max` | maximum UV index of that day, 1 decimal | empty (MET Norway gives none) |

## `HAZ`

`HAZ` has 12 fields: one active hazard. Without `lat` and `lon` they are the world hazards
sorted by severity then by value; with a GPS position they are the hazards near that position
(inside the search radius, plus every `RED` one): those inside the radius first, then by severity, then by distance.

| Index | Name | Format / unit | Unknown |
| --- | --- | --- | --- |
| 0 | tag | `HAZ` | |
| 1 | `id` | at most 28 characters | never |
| 2 | `category` | `EARTHQUAKE`, `TSUNAMI`, `VOLCANO`, `HURRICANE`, `TORNADO`, `FLOOD`, `EXTREME_RAIN`, `WILDFIRE`, `SOLAR_STORM` | never |
| 3 | `severity` | `GREEN`, `YELLOW`, `ORANGE`, `RED` | never |
| 4 | `lat` | degrees, 4 decimals | never |
| 5 | `lon` | degrees, 4 decimals | never |
| 6 | `value` | the source's main value, 1 decimal. Its unit depends on the category and the source (magnitude, km/h, VEI, m...) and is not transmitted | empty when the source gives none (NOAA NWS alerts, volcanoes without a VEI) |
| 7 | `depth_km` | earthquake depth, 1 decimal | empty |
| 8 | `tsunami` | `1` when a tsunami warning or potential is reported, else `0` | never |
| 9 | `timestamp_utc` | event time, UTC epoch seconds | never (an event with no time is dropped, not dated "now") |
| 10 | `sources` | agencies joined by `+` (for example `USGS+EMSC`), at most 24 characters | never |
| 11 | `title` | at most 64 characters | never |

## `END` and the CRC

`END` has 2 fields: `END|<CRC32>`.

The CRC32 (IEEE 802.3, the one of zlib / PNG, polynomial `0xEDB88320` reflected, initial value
and final XOR `0xFFFFFFFF`) is computed over **every byte before the `END|` line**, that is
from the first byte of `HEAD` up to and including the `\n` that ends the last line before
`END`. It is written as 8 **uppercase** hexadecimal digits followed by `\n`. A frame whose
`END` line is missing, or whose CRC does not match, must be rejected.

## Size budget and decoder limits

- The whole frame stays under **8 KiB** (8192 bytes) for the default zone set (GPS zone, 5
  watched zones, 5 days each, 12 hazards). A test enforces it.
- No line exceeds **512 bytes**.
- Limits currently set in the display decoder (display repository, `firmware/core/vigie_model.cpp`,
  read on 2026-10-20): 32 fields per line, 10 zones, 30 hazards, 10 forecast days per zone. The
  API must stay within them.

## Example

Three zones: `z0` has all its weather, `z1` has none (every provider failed), `z2` has partial
weather from the fallback provider (no feels-like, gust, UV, air quality, nowcast, rain
probability). Two hazards: an earthquake, and a NOAA tornado warning that carries no magnitude and
no depth. This block is generated by the code: a test fails if it drifts.

<!-- example:start -->
```text
HEAD|1792334600|2.3|0|1.1|waveshare-7b|RED|1|1|0|0|0|0
ZONE|z0|Paris, FR|48.8566|2.3522|7200|18.2|17.6|68|1016.4|19.5|31.0|245|61|1.7|31|15|2.0|0.00,0.20,0.50,0.10|||||
RAIN5M|z0|0.00,0.00,0.00,0.80,0.80,0.80,2.00,2.00,2.00,0.40,0.40,0.40
DAY|z0|2026-10-18|61|12.0|19.0|2.4|80|32.0|3.5
DAY|z0|2026-10-19|2|11.5|20.5|0.0|10|20.0|4.0
DAY|z0|2026-10-20|1|10.0|21.0|0.0|5|18.0|4.2
ZONE|z1|Tokyo, JP|35.6762|139.6503|32400||||||||||||||||||
RAIN5M|z1|
ZONE|z2|Reykjavik, IS|64.1466|-21.9426|0|3.5||88|998.2|30.2||200|63||||||||||
RAIN5M|z2|
DAY|z2|2026-10-20|63|1.0|4.5|3.2||38.0|
HAZ|usgs_us7000test|EARTHQUAKE|ORANGE|35.1500|25.2000|6.5|18.0|1|1792334500|USGS+EMSC|M 6.5 - Crete, Greece
HAZ|noaa_1234|TORNADO|RED|35.5000|-97.5000|||0|1792334300|NOAA_NWS|Tornado Warning - Oklahoma County, OK
END|77EE62A0
```
<!-- example:end -->

## What changed in this version of the contract

Column order and counts are unchanged. Relative to the previous frames:

- `DAY` field 2 is an ISO date (`2026-10-20`), not a French weekday abbreviation (`Mar`).
- `DAY` field 7 is the real maximum probability of precipitation (it used to be derived from
  the rain amount) and field 9 is that day's maximum UV (it used to repeat the current UV).
- Unknown values are **empty fields** instead of invented numbers (temperature `15.0`,
  humidity `70`, pressure `1013.2`, wind `10`, AQI `20`/`22`, Kp `2.0`, depth `0.0`, zero
  waves, a zero rain forecast...). `ZONE` fields 6 to 23 are all empty when a zone has no
  weather. `HEAD` fields 2, 6 to 11 can be empty.
- `RAIN5M` is a step function of the 15-minute amounts (it used to be smoothed, with an
  invented 20 % decay on the last step).
- The `GPS` zone has its own forecast and local UTC offset (it used to repeat the first
  watched zone's forecast and a longitude-based offset), and is named `GPS position`.
