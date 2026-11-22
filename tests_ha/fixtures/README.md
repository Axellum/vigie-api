# Recorded answers

Real answers of the public instance, kept as they came, for the tests of the integration. `tests/test_home_assistant_contract.py`
checks each one against the API's model of its route (in the API's own CI), so they cannot drift from the real shape; a new file
here must match one of the prefixes that test knows.

| Files | Route | Read |
| --- | --- | --- |
| `vigilances_haute_corse.json` | `GET /v1/vigilances` | earlier, for a point of Haute-Corse |
| `earthquakes_istanbul_2026-10-25.json`, `prealert_istanbul_2026-10-25.json` | `GET /v1/earthquakes`, `GET /v1/experimental/prealert` | 2026-10-25, Istanbul |
| `point_*`, `tides_*`, `normals_*`, `vigilances_biarritz_*` | `GET /v1/point?compact=false&marine=true`, `/v1/tides`, `/v1/climate/normals`, `/v1/vigilances` | 2026-11-22 around 13:11 UTC, Biarritz and Tokyo |
| `stationwind_biarritz_2026-11-22.json` | `GET /v1/stations/wind?bbox=` (a box around Biarritz) | same read |
| `spots_2026-11-22.json`, `activities_biarritz-plage-des-corsaires_2026-11-22.json` | `GET /v1/surf/spots`, `GET /v1/activities/{spot_id}` | same read |
| `earthquakes_biarritz_2026-11-22.json` | `GET /v1/earthquakes` (300 km, magnitude 4.5, 24 h: what the integration asks) | 2026-11-22 around 18:28 UTC, Biarritz: none that day |

The places are public places (a city centre, a beach), never the home of anyone. The tests that read the weather freeze the clock
just after the read, because "the next hours" and "the next high tide" are relative to now.
