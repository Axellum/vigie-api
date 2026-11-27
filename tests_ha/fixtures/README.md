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
| `point_chamonix_2026-11-22.json`, `point_angouleme_2026-11-22.json` | `GET /v1/point?compact=false&marine=true` | 2026-11-22 around 20:16 UTC, Chamonix (an avalanche bulletin at 4 of 5, a drought watch) and Angoulême (drought restrictions): the lines of the season of the card |
| `forestfire_biarritz_2026-11-22.json` | `GET /v1/forest-fire` | 2026-11-22, Biarritz: out of season (Météo-France publishes it from June to September) |
| `avalanches_chamonix_2026-11-27.json`, `avalanches_gourette_2026-11-27.json`, `avalanches_stubai_2026-11-27.json` | `GET /v1/avalanches` | 2026-11-27 around 12:25 UTC, Chamonix (Météo-France, Mont-Blanc: 2 below 2,700 m, 3 above), Gourette (Météo-France, Aspe-Ossau: 2 at every altitude) and the Stubai valley (Avalanche.report: 1 below 2,400 m, 2 above, aspects and a trend): the Mountain page of the card |
| `surfspot_biarritz-plage-des-corsaires_2026-11-27.json`, `activities_biarritz-plage-des-corsaires_2026-11-27.json`, `seaplaces_biarritz-plage-des-corsaires_2026-11-27.json` | `GET /v1/surf/spots/{spot_id}?days=2`, `GET /v1/activities/{spot_id}`, `GET /v1/sea/places?type=bathing&near_lat=&near_lon=&radius_km=2` | 2026-11-27 at 17:11 UTC, the Plage des Corsaires (Biarritz): a beach of the Sea page of the card |
| `earthquakes_biarritz_2026-11-22.json` | `GET /v1/earthquakes` (300 km, magnitude 4.5, 24 h: what the integration asks) | 2026-11-22 around 18:28 UTC, Biarritz: none that day |

The places are public places (a city centre, a beach), never the home of anyone. The tests that read the weather freeze the clock
just after the read, because "the next hours" and "the next high tide" are relative to now.
