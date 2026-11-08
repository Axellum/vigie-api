# Embedded frame contract

`GET /v1/embedded/frame` returns a compact text frame decoded by the ESP32-P4 display
firmware (separate repository, the Vigie display). **This file is the single source of
truth of the contract.** It is pinned by `tests/test_embedded_frame_contract.py`: when a test
there has to change, change this document in the same commit and tell the display repository.

Generator: `app/services/embedded_frame.py`.

## Request

```
GET /v1/embedded/frame?device_type=waveshare-7b&lat=47.75&lon=-3.36&marine=true&max_hazards=12
GET /v1/embedded/frame?device_type=tab5&spot=soorts-hossegor-plage-sud-point-d-or&wind_min_kn=12&wind_max_kn=25
```

| Parameter | Default | Meaning |
| --- | --- | --- |
| `device_type` | `waveshare-7b` | Display profile: `waveshare-7b`, `tab5`, `marine`, `web`, `generic`. |
| `lat`, `lon` | none | Optional GPS position. When both are given, a first zone `GPS` is added. |
| `marine` | `false` | Add waves, swell and current to the `GPS` zone (also implied by `device_type=marine`). |
| `max_hazards` | `12` (1 to 30) | Maximum number of `HAZ` lines. |
| `spot` | none | Optional surf spot id (`GET /v1/surf/spots`). Adds the `SEA`, `ACT` and `CRIT` lines of that spot's activities. An unknown id answers `404`. |
| `wind_min_kn`, `wind_max_kn` | `12`, `25` (knots, `0 < min < max <= 60`) | The wind range of the kite, wing or windsurf (only read with `spot`). Anything else answers `422`. |

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
   per column (given below). Temperatures are in degrees Celsius, speeds in km/h (except the
   `CRIT` lines, which give the unit of each value: knots, Beaufort, metres...).
6. The last line is `END|<CRC32>`.
7. **A decoder skips a tag it does not know** (the CRC still covers the line). Tags are added this
   way (`SEA`, `ACT`, `CRIT` were), and then `HEAD` field 4 stays `1.1`.

## Line order

```
HEAD
ZONE, RAIN5M, DAY x n      <- the GPS zone, only when lat and lon are given
ZONE, RAIN5M, DAY x n      <- one block per watched zone, in zone id order
HAZ x m
SEA                        <- only with `spot`, and only when the swell could be read
ACT, CRIT x k              <- four times: surf, kite, boat, shore_fishing, in that order
END
```

Each `ZONE` is immediately followed by its own `RAIN5M` (always present, possibly empty) and
then its `DAY` lines (none when the zone has no weather). Each `ACT` is immediately followed by
the `CRIT` lines of its own verdict.

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

Twelve rain rates in mm/h, one per 5 minutes over the next hour, 2 decimals. The short-term
rain of the frame (this line and `ZONE` fields 16 to 18) comes **only from Open-Meteo's
`minutely_15` precipitation**, never from the RainViewer radar (the free RainViewer API has had
no nowcast since 2026-01-01). Open-Meteo documents that its 15-minute data is native only in
Central Europe and North America; elsewhere it is interpolated by Open-Meteo from hourly data,
so outside those regions the four values are not a true nowcast. The provider supplies only
four 15-minute amounts, so each amount is converted to a rate (amount x 4) and repeated for
its three 5-minute steps; nothing is interpolated or extrapolated by this API. The field is
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

## Activities of a surf spot: `SEA`, `ACT`, `CRIT`

Only with `spot`. These lines carry what `GET /v1/activities/{spot_id}` says for 24 hours from the
current hour (the question the display asks is "can I go today?"), in the order `SEA`, then for each of
`surf`, `kite`, `boat`, `shore_fishing` its `ACT` line followed by its `CRIT` lines. They are **additive**:
a decoder that does not know these tags skips them (the CRC still covers them) and `HEAD` field 4 stays
`1.1`.

- **Every verdict is a rule of Vigie** (ADR 0016: thresholds written in `rules` of
  `GET /v1/activities/{spot_id}`), not an official one, and **not for safety**. A display that shows a
  verdict says so, and says that the coefficient of the tide is an estimate (`tide_coefficients`), not
  the SHOM's.
- No block without `spot`. With `spot`, the block is missing when the swell could not be read: the
  weather and the alerts of the frame are not held back by the sea, and a display reads the missing
  block as **unknown**, not as "nothing to do".
- It costs the providers nothing more than the surf page of the same spot (the swell and the wind, both
  cached); the tide is computed by Vigie and the coastal warnings are the cached ones of
  `GET /v1/vigilances`.
- Times are UTC epoch seconds. Speeds are in the unit of the criterion (`CRIT` field 4), not always km/h.

### `SEA`

`SEA` has 18 fields: one line, the spot and what is common to the four activities.

| Index | Name | Format / unit | Unknown |
| --- | --- | --- | --- |
| 0 | tag | `SEA` | |
| 1 | `spot_name` | at most 32 characters | never |
| 2 | `spot_place` | the nearest town or village, at most 32 characters | empty |
| 3 | `generated_utc` | when the verdicts were worked out | never |
| 4 | `first_hour_utc` | start of the first of the 24 hours of the `ACT` strips (the current hour) | empty when no activity has an hour |
| 5 | `stale` | `1` when a provider did not answer and its last answer is served, else `0` | never |
| 6 | `wind_min_kn` | the kite's range as asked (default 12), 1 decimal | never |
| 7 | `wind_max_kn` | 1 decimal (default 25) | never |
| 8 | `sunrise_utc` | the first sunset still to come, and the sunrise of that day | empty |
| 9 | `sunset_utc` | | empty |
| 10 | `sunrise2_utc` | the day after | empty |
| 11 | `sunset2_utc` | | empty |
| 12 | `tide_extremes` | the high and low waters from 6 hours before the first hour to 30 hours after it, at most 8, as `<H or L>:<time_utc>:<height_m>` joined by `,` (height to the model's mean sea level, 2 decimals) | empty where no tide model is near the spot |
| 13 | `tide_coefficients` | the coefficients Vigie **estimates** (not the SHOM's), one per Brest high water of that window, at most 4, as `<brest_high_water_utc>:<value>` joined by `,` | empty with field 12 |
| 14 | `vigilance_status` | `ok`, or `unknown` when the coastal warnings could not be read: nothing is capped then, and an empty field 15 is **not** "nothing to fear" | never |
| 15 | `vigilance_level` | the highest level (`GREEN`, `YELLOW`, `ORANGE`, `RED`) of the official coastal warnings (in France the wave-submersion vigilance) that stand at some moment of the 24 hours; a warning whose level is not in that table is left out | empty when none stands, or with status `unknown` |
| 16 | `vigilance_until_utc` | the latest end of the warnings at that level | empty with field 15, or when one of them gives no end |
| 17 | `vigilance_source` | the source of the first warning at that level, at most 24 characters (`meteoalarm`...) | empty with field 15 |

The official warnings keep priority: orange keeps the boat and the shore fishing at `possible` at
best, red makes every verdict `poor` (the `ACT` lines already say so, with the reason
`vigilance.orange` or `vigilance.red`).

### `ACT`

`ACT` has 12 fields: one activity.

| Index | Name | Format / unit | Unknown |
| --- | --- | --- | --- |
| 0 | tag | `ACT` | |
| 1 | `activity` | `surf`, `kite` (kite, wing or windsurf), `boat`, `shore_fishing` | never |
| 2 | `available` | `1`, or `0` when the activity cannot be judged here (the shore fishing where no tide model is near): fields 3 to 11 are then empty | never |
| 3 | `verdict` | `good`, `possible` or `poor`: the best verdict of the daylight hours of the 24 hours | empty when no daylight hour has a verdict |
| 4 | `reason` | why it is not `good`: `<criterion>.<state>` of the hour field 9 stands on (the vocabulary below), or `vigilance.orange`, `vigilance.red` | empty when `good`, or with field 3 |
| 5 | `slot_start_utc` | the best slot: the longest run of consecutive daylight hours at the verdict of field 3, the first on a tie | empty when the verdict is `poor` (no slot to go), or with field 3 |
| 6 | `slot_end_utc` | the end of the last hour of the slot (exclusive) | empty with field 5 |
| 7 | `hours` | 24 characters, one per hour from `first_hour_utc`: `G` good, `P` possible, `X` poor, `-` no verdict or no hour | all `-` |
| 8 | `daylight` | 24 characters, one per hour: `1` daylight, `0` night, `-` unknown | all `-` |
| 9 | `detail_utc` | the hour the verdict stands on (the first hour of the slot, else the first daylight hour with a verdict): its criteria are the `CRIT` lines that follow | empty when there is none |
| 10 | `points` | surf only: the points of the rating of that hour | empty |
| 11 | `out_of` | surf only: the most the criteria that could be scored give | empty |

The headline looks at the daylight hours only; the strips show the night hours too, so that the display
draws all 24 bars.

### `CRIT`

`CRIT` has 9 fields: one criterion of the hour `ACT` field 9 names, in the order the verdict reads them.

| Index | Name | Format / unit | Unknown |
| --- | --- | --- | --- |
| 0 | tag | `CRIT` | |
| 1 | `activity` | the activity of the `ACT` line just above | never |
| 2 | `name` | the criterion (the table below) | never |
| 3 | `value` | 1 decimal, in the unit of field 4 | empty |
| 4 | `unit` | `m`, `s`, `deg`, `km/h`, `kn`, `Beaufort`, `min`, or empty (the coefficient) | never |
| 5 | `state` | what the rule makes of the value, as a code (the table below) | empty when the value, or what it is compared with, is unknown |
| 6 | `verdict` | `good`, `possible` or `poor` | empty: the criterion counts for nothing |
| 7 | `points` | surf only: the points the rating gives, `0` to `max_points` | empty |
| 8 | `max_points` | surf only | empty |

The vocabulary (the display writes its own text for each code; the thresholds are in `rules` of
`GET /v1/activities/{spot_id}` and in ADR 0016). `swell_height.too_small` and `swell_height.too_big` are
worth 0 points: the surf is then `poor` whatever the other criteria give. A surf criterion with no
points (the tide, which Vigie does not know the best stage of) is shown with its value only and is not
counted.

| Activity | Criterion | Unit | States |
| --- | --- | --- | --- |
| `surf` | `swell_height` | m | `too_small`, `small`, `right`, `big`, `too_big` |
| `surf` | `swell_period` | s | `short`, `medium`, `long` |
| `surf` | `swell_direction` | deg | `facing`, `oblique`, `off_axis` |
| `surf` | `wind` | km/h | `light`, `offshore`, `cross_shore`, `onshore`, `strong` |
| `surf` | `tide` | m | `right_stage`, `wrong_stage` |
| `kite` | `wind` | kn | `in_range`, `a_bit_light`, `a_bit_strong`, `too_light`, `too_strong` |
| `kite` | `gust_gap` | kn | `steady`, `gusty`, `very_gusty` |
| `kite` | `wind_direction` | deg | `onshore`, `offshore`, `cross_shore` |
| `boat` | `wind_force` | Beaufort | `up_to_moderate`, `fresh`, `strong_or_more` |
| `boat` | `gust_force` | Beaufort | `up_to_fresh`, `strong`, `gale_or_more` |
| `boat` | `sea_height` | m | `low`, `moderate`, `high` |
| `shore_fishing` | `low_water` | min | `in_window`, `outside_window` |
| `shore_fishing` | `coefficient` | (empty) | `small`, `moderate`, `large`, `very_large` |
| `shore_fishing` | `wind_force` | Beaufort | `up_to_fresh`, `strong`, `near_gale_or_more` |
| `shore_fishing` | `sea_height` | m | `low`, `moderate`, `high` |

`swell_direction` is the angle between where the swell comes from and the direction the beach faces
(0 is straight on); `wind_direction` is where the wind comes from, degrees from north; `gust_gap` is the
gust minus the mean wind; `low_water` is the minutes from the middle of the hour to the low water that
follows; `coefficient` is the one Vigie estimates for that low water.

### Example

The recorded swell and wind of Hossegor (the fixture of the tests) and an orange coastal warning. This
block is generated by the code: a test fails if it drifts.

<!-- example-activities:start -->
```text
SEA|Plage Centrale|Hossegor|1793182200|1793181600|0|12.0|25.0|1793169372|1793206936|1793255849|1793293253|H:1793160780:1.95,L:1793183100:-1.98,H:1793205480:1.86,L:1793227560:-1.79,H:1793249760:1.85,L:1793272320:-1.84|1793162580:97,1793207100:93,1793251500:91|ok|ORANGE|1793210400|meteoalarm
ACT|surf|1|good||1793181600|1793196000|GGGGPPPGPPPGGGGGGGGGGGGG|111111110000000000000111|1793181600|6|8
CRIT|surf|swell_height|2.6|m|big|possible|1|2
CRIT|surf|swell_period|10.0|s|medium|possible|1|2
CRIT|surf|swell_direction|21.0|deg|facing|good|2|2
CRIT|surf|wind|18.7|km/h|offshore|good|2|2
CRIT|surf|tide|-1.9|m||||2
ACT|kite|1|possible|wind.a_bit_light|1793199600|1793206800|PXXXXPPXPXXXXXXXXXXXXXXX|111111110000000000000111|1793199600||
CRIT|kite|wind|11.9|kn|a_bit_light|possible||
CRIT|kite|gust_gap|0.0|kn|steady|good||
CRIT|kite|wind_direction|350.0|deg|cross_shore|good||
ACT|boat|1|possible|sea_height.moderate|1793257200|1793268000|XXXXXXXXXXXXXXXXPPPPPPPP|111111110000000000000111|1793257200||
CRIT|boat|wind_force|2.0|Beaufort|up_to_moderate|good||
CRIT|boat|gust_force|3.0|Beaufort|up_to_fresh|good||
CRIT|boat|sea_height|1.9|m|moderate|possible||
ACT|shore_fishing|1|possible|sea_height.moderate|1793264400|1793268000|XXXXXXXXXXXXXXXXXXXXXXXP|111111110000000000000111|1793264400||
CRIT|shore_fishing|low_water|102.0|min|in_window|good||
CRIT|shore_fishing|coefficient|91.0||large|good||
CRIT|shore_fishing|wind_force|2.0|Beaufort|up_to_fresh|good||
CRIT|shore_fishing|sea_height|1.7|m|moderate|possible||
```
<!-- example-activities:end -->

## `END` and the CRC

`END` has 2 fields: `END|<CRC32>`.

The CRC32 (IEEE 802.3, the one of zlib / PNG, polynomial `0xEDB88320` reflected, initial value
and final XOR `0xFFFFFFFF`) is computed over **every byte before the `END|` line**, that is
from the first byte of `HEAD` up to and including the `\n` that ends the last line before
`END`. It is written as 8 **uppercase** hexadecimal digits followed by `\n`. A frame whose
`END` line is missing, or whose CRC does not match, must be rejected.

## Size budget and decoder limits

- The whole frame stays under **8 KiB** (8192 bytes) for the default zone set (GPS zone, 5
  watched zones, 5 days each, 12 hazards). A test enforces it, and enforces it again with the
  activities block of a spot added.
- The activities block (`SEA`, `ACT`, `CRIT`) is under **2 KiB** by itself. A test enforces it.
- No line exceeds **512 bytes**. The `SEA` line is the one that can grow (the tide): it holds at most
  8 high and low waters and 4 coefficients, which keeps it under 480 bytes with the longest names.
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

### Added later: the activities of a surf spot

`SEA`, `ACT` and `CRIT` lines, and the request parameters `spot`, `wind_min_kn` and `wind_max_kn`. Nothing
else changes: without `spot` the frame is byte for byte what it was, and a decoder that skips unknown tags
reads it as before.
