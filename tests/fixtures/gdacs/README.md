# Recorded GDACS answer

The real answer of the GDACS search API for tropical cyclones, recorded on **2026-10-20** and played back by the tests (no
test reaches the network). Nothing in the file was edited.

| File | From | What it is |
| --- | --- | --- |
| `geteventlist_TC_2026-10-20.json` | `https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH?eventlist=TC&alertlevel=Orange;Red&fromDate=2026-09-20&pageSize=100&pageNumber=1` | Five storms: CHAMPI-26 (red, west Pacific, current), SIMON-26 (orange, eastern Pacific), **ISAIAS-26** (orange, event 1001334, from 2026-10-06 21:00, last position 37.5 N 84.0 W, 194 km/h, source NOAA), POLO-26 and ONE-26. The storm is named `<NAME>-<year>` in `eventname`, which is how the NHC's storm is recognised |

All but CHAMPI-26 had stopped being current (`iscurrent` `"false"`) when the file was recorded. The tests that need Isaias
as GDACS had it while the storm lasted set `iscurrent` back to `"true"`, and say so.

GDACS had no tsunami event (`eventlist=TS`) since 2026-09-01 on 2026-10-20 (HTTP 204), so no tsunami record is kept here: the
test that merges a GDACS tsunami with a tsunami message uses a record shaped like these, and says so.
