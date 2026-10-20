# Recorded National Hurricane Center answers

Real answers of the NHC (`https://www.nhc.noaa.gov`), played back by the tests (no test reaches the network). Nothing in these
files was edited.

| File | From | What it is |
| --- | --- | --- |
| `CurrentStorms_2026-10-09-2358.json` | the NHC's archive of the status file, `/archive/activeJson/2026/`, the snapshot of 2026-10-09 23:58 UTC | Three active storms: **Hurricane Isaias** (`al092026`, 95 kt, 967 hPa, 30.1 N 86.6 W, advisory 13A), Tropical Storm Rachel and Hurricane Simon (eastern Pacific). Note that the intensity and the pressure are strings and the position is written both as text (`30.1N`) and as `latitudeNumeric` |
| `CurrentStorms_2026-10-20-0324.json` | the same archive, 2026-10-20 03:24 UTC | One storm: Potential Tropical Cyclone Trudy (`ep212026`, 30 kt) |
| `CurrentStorms_empty.json`, `CurrentStorms.headers.txt` | `/CurrentStorms.json` on 2026-10-20 at 16:38 UTC, and its response headers | The file with no active storm (`{"activeStorms": []}`, 24 bytes). `Cache-Control: max-age=300`, `ETag: "18-65e4347ce0632"`, `Last-Modified`; a request with `If-None-Match` is answered `304` with 484 bytes of head |
| `al092026_5day_013A.zip`, `al092026_5day_013A.headers.txt` | `/gis/forecast/archive/al092026_5day_013A.zip` | The bundle of Isaias's advisory 13A (29,533 bytes): the shapefiles `5day_pgn` (the cone), `5day_lin` (the track), `5day_pts` (5 forecast positions) and `ww_wwlin` (3 coastal warnings), with their `.dbf`, `.prj`, `.shx` and metadata |
| `cp012026_5day_064.zip` | the NHC's GIS forecast archive (`/gis/forecast/archive/`), advisory 64 of the Central Pacific storm CP01 of 2026, fetched on 2026-10-20 | A storm on the **date line**: its cone has longitudes on both sides of 180 degrees. There is no `CurrentStorms` file for it here; the tests read the bundle directly |

On 2026-10-20 at 17:18 UTC, when these tests were written, the NHC had **no active storm** (`activeStorms` empty): the
Atlantic and eastern Pacific storms above are archived ones. `tools/check_live_feeds.py` reads the real file and, when a
storm is active, its bundle.

Public domain (NHC disclaimer, see `SOURCES.md`).
