# Météo-France radar mosaic, recorded answer of the gateway

- `frames-2026-11-18.json`: `GET /v1/radar/frames` of a vigie-meteo gateway as answered on 2026-11-18 at about 08:40 UTC (two
  images kept, ending 08:35 and 08:40 UTC), re-indented, nothing else changed. The gateway reads `DPRadar/v1`
  (mosaic `METROPOLE`, `LAME_D_EAU`, 500 m) of Météo-France, public data (see `SOURCES.md` for what is known of the licence).

What the tests rely on: `source_model` `meteofrance-lame-d-eau`, bounds -9.97 / 37.45 / 17.57 / 53.67, zooms 3 to 10, nine legend
classes from 0.1 to 64 mm/h. Used by `tests/test_meteofrance_radar.py`.
