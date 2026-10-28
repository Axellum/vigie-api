# `app/data`: the reference data bundled with the API

Built by the tools of `tools/` (their docstrings say where every input comes from and under which licence); nothing in this
folder is fetched at run time. **The code of this repository is under the MIT licence; the data here is not always.**

| File | What it is | Built by | Licence of the data |
| --- | --- | --- | --- |
| `countries.geojson.gz` | Borders of every country of Natural Earth, 244 (to find the country of a point without a request, and to name it when no source is read for it) | `tools/build_geometries.py` | Natural Earth: public domain |
| `geocodes-nuts.geojson.gz` | Shapes of the NUTS regions MeteoAlarm uses as geocodes (the French departments, the Belgian provinces) | `tools/build_geometries.py` | Natural Earth: public domain; codes from Wikidata: CC0 |
| `provinces-tr.geojson.gz` | The 81 provinces of Turkey, keyed by ISO 3166-2 code, with their Turkish names (the unofficial pre-alert pilot, ADR 0011) | `tools/build_geometries.py --provinces-tr` | Natural Earth: public domain; names from Wikidata: CC0 |
| `prealert-tr-thresholds.json` | The frozen thresholds per province of the unofficial pre-alert for Turkey (ours, learned on the ECMWF forecasts of 2024-2025; ADR 0011 and its amendment) | `tools/backtest_prealert_tr.py freeze` | Ours (MIT); computed from ECMWF open data, CC BY 4.0 |
| `swic_registry.json.gz` | Snapshot of the WMO registry of national warning services (without the people's names and e-mail addresses) | `tools/build_swic_snapshot.py` | The registry of the WMO: see `SOURCES.md` |
| `apex-latitudes.json.gz` | The magnetic latitude (modified apex latitude at 110 km) of every point of a 2-degree grid, epoch 2026.5, for the aurora estimate (ADR 0013) | `tools/build_magnetic_latitudes.py` | Ours (MIT), computed from the IGRF-14 coefficients of IAGA, whose page states no licence (**not verified**, `SOURCES.md`); the coefficients are not copied |
| `tides-eot20.npz` | **The harmonic constants of the EOT20 ocean tide model** at the 110,685 ocean cells within 3 cells of a coast (1/8 degree, 66 S to 66 N), to compute the tide (`GET /v1/tides`, ADR 0013). Changes: the cells far from a coast, beyond 66 degrees and where S1 or T2 exceeds 20 cm (and those within 2 of them) are not kept; the constants are rounded to the millimetre and stored as differences from the previous cell | `tools/build_tides.py` | **CC BY 4.0** (EOT20, Hart-Davis et al. 2021, DGFI-TUM, doi:10.17882/79489): credit required, the credit is in every answer |
| `tides-arguments.npz` | The nodal factors and phases of the 34 constituents every 10 days from 2024 to 2045, and the matrix that infers the 17 minor constituents | `tools/build_tides.py` (pyTMD 3.0.9) | Ours (MIT), computed with pyTMD (MIT) |
| `solar-eclipses.json.gz` | NASA's solar eclipses of 2001 to 2100: the catalogue fields and the Besselian elements of each, as NASA writes them | `tools/build_eclipses.py` | NASA's (Fred Espenak, GSFC): not protected by copyright, reproduced with the acknowledgment "Eclipse Predictions by Fred Espenak, NASA's GSFC" (`SOURCES.md`) |
| `gem-active-faults.geojson.gz` | **The traces of the named active faults of the world**, simplified, to say how far an earthquake is from one (`near_places`) | `tools/build_faults.py` | **CC BY-SA 4.0** (GEM Global Active Faults Database, Styron and Pagani 2020): share-alike, credit required. Notice and statement of the changes: [`gem-active-faults.NOTICE.md`](gem-active-faults.NOTICE.md) |

A shape that has no stated licence is not added here (`SOURCES.md` says which, and why). The Smithsonian list of the world's
volcanoes is **not** here: its licence is unclear and its owner has not been asked yet.
