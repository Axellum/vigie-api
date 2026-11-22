# 0038. The forecast wind of the map: AROME and ARPEGE read from the portal's WCS, served as three small images

- Status: accepted
- Date: 2026-11-22
- Number: given by `scripts/prochain_adr.py` on 2026-11-22.

## Context

The site shows the wind measured by the stations and the buoys (`GET /v1/stations/wind`). The owner asked for the wind of the whole
map as a quiet animated background (moving trails, as many weather sites draw it): a **forecast** field, everywhere, readable at any
zoom and light enough for a phone.

What was measured on 2026-11-22 with the project's key (subscribed to the `AROME` and `ARPEGE` APIs of the portal):

| Grid (WCS service) | Area, step | One component at 10 m, `GetCoverage` | Answer |
| --- | --- | --- | --- |
| `MF-NWP-HIGHRES-AROME-0025-FRANCE-WCS` | 12 W-16 E, 37.5-55.4 N, 0.025 (1121 x 717) | `U_COMPONENT_OF_WIND__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND___<run>`, `subset=time(<ISO>)`, `subset=height(10)` | 1.1 MB GRIB2 `grid_simple`, a bitmap (no value in the corners of the grid), 0.5 s |
| `MF-NWP-GLOBAL-ARPEGE-01-EUROPE-WCS` | 32 W-42 E, 20-72 N, 0.1 (741 x 521) | the same | 0.6 MB, 0.4 s |
| `MF-NWP-GLOBAL-ARPEGE-025-GLOBE-WCS` | the globe from 0 E, 0.25 (1440 x 721) | the same | 1.6 MB, 0.7 s |

- `time(<seconds>)` (a lead) and `time(<ISO date>)` give the same file. At about 23:45 UTC, the newest AROME run that had
  00 UTC was that of 18 UTC (21 UTC not yet); ARPEGE of 18 UTC was there.
- The open-data packages of the same models (`meteofrance-pnt` on data.gouv.fr) hold every surface field of six hours in one file
  (AROME `SP1` 00H06H: 56 MB; ARPEGE 0.25 `SP1` 000H024H: 267 MB, read on data.gouv.fr's API) and have no index: far too heavy for
  a wind of one hour.
- The licence documents of the AROME and ARPEGE APIs on the portal (read on 2026-11-22) put the data under Licence Ouverte 2.0:
  reuse, commercial included, with the source and the date of the last update named and the data not altered.

## Decision

1. **Météo-France only, three grids**: AROME over France (the best model there, 1.3 km), ARPEGE over Europe and over the world
   (AROME is run inside ARPEGE, so the edge of AROME shows no jump). One provider, one licence, the key the API already has; ECMWF
   and GFS were not needed.
2. **This API reads them itself** (`app/collectors/meteofrance_wind_field.py`), not the vigie-meteo gateway: six small requests an
   hour (two components of three grids) and a second of work, as the UV index of PHEALTH is read (ADR 0028 kept the heavy runs on
   the gateway). The hour served is the whole hour nearest to now; for each grid the newest run old enough to be published is
   asked, then the ones before (a run that lacks the hour answers 400 or 404 and is not asked again for 15 minutes).
3. **Images, not numbers**: each grid becomes a PNG of every 2nd node after a 3 x 3 binomial mean (world 0.5, Europe 0.2, France
   0.05; about 280, 120 and 190 KB on 2026-11-22), red the east component and green the north one on one byte each against the
   strongest component of the grid (`scale_ms`, a step of about 0.2 m/s), blue 255 where the model has a value. No alpha channel,
   so that a browser gives the bytes back unchanged. The PNG is written by numpy and zlib (no Pillow).
4. **Routes**: `GET /v1/wind/field` (the grids of the hour: box, size, step, scale, run, hour, `min_zoom`, address of the image,
   credit) and `GET /v1/wind/field/{level}/{stamp}.png` (immutable: the stamp is the hour and the run). A grid that fails keeps its
   image of an earlier hour (at most 3 hours, its own hour said) and the answer is made again after 10 minutes; with no grid at all
   the earlier answer is served as `stale`, else `unavailable`. `VIGIE_WIND_FIELD=0` turns it off.
5. **The map loads what it needs**: the world always, Europe from zoom 3, France from zoom 5, the finest grid that has a value
   winning at each point (the site, vigie-web, draws the trails).
6. **ecCodes behind one lock** for every reader of GRIB2 (`app/collectors/grib.py`): the UV index, the pre-alert and the wind no
   longer each hold their own lock, since two decodes at once crashed ecCodes (2026-10-25).

## Checked

- The three grids read from the real portal on 2026-11-21 at about 23:50 UTC and again at 00:01 UTC (run of 18 UTC, valid at
  00 UTC; AROME of 21 UTC still lacked the hour, the run before was taken): 1.3 to 1.7 s for the requests and the images.
- Real small files of the three services (a box of each, the same run) decode to the right place and hour; the images give the
  components back within half a step; the whole-world grid starts at 180 W; the choice of runs, the fallback to an earlier run, the
  grid left out when it fails, the routes and the switch are tested without the network.

## Not verified

- How often a run lacks the hour at the moment it is asked (the delays of 2 h for AROME and 3 h 30 for ARPEGE are a first guess
  from one evening).
- The load on the free plan of the portal shared with the other readers of the key (6 requests an hour here).
- The smoothing against the raw grid at the finest zooms (0.05 for AROME is coarser than its 0.025).
