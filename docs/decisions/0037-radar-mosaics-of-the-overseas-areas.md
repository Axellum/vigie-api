# 0037. The radar mosaics of the overseas areas (Antilles, La Réunion, New Caledonia), drawn as tiles like the metropolitan one

- Status: accepted
- Date: 2026-11-21
- Number: given by `scripts/prochain_adr.py` on 2026-11-21 (0036 went to the overseas vigilances of the same day).

## Context

[ADR 0030](0030-radar-mosaic-of-france-as-tiles.md) relays the mosaic `METROPOLE` of `DPRadar` as Web Mercator tiles drawn by the vigie-meteo gateway, and notes that the three overseas mosaics (`ANTILLES`, `REUNION`, `NOUVELLE-CALEDONIE`) "were never opened here (their grid and projection are to be checked)". They were opened on 2026-11-21 with the project's key (one request for the date and one for the file, per area):

| Area | File (`maille=500`) | Grid | Projection (`where/projdef`) | Box of the grid (W, S, E, N) |
| --- | --- | --- | --- | --- |
| `ANTILLES` | `T_IPAN20_C_LFPW_…h5`, 269 kB | 2310 rows x 1930 columns | `+proj=merc +lat_ts=15.501 +x_0=7081535.14 +y_0=-1135674.47` | -66.0, 10.59, -57.0, 21.0 |
| `REUNION` | 173 kB | 2000 x 2300 | `+proj=merc +lat_ts=-20.387 +x_0=-5323837.27 +y_0=2681326.18` | 51.0, -25.01, 62.02, -16.0 |
| `NOUVELLE-CALEDONIE` | 192 kB | 2528 x 2528 | `+proj=merc +lat_ts=-17 +x_0=-17005787.17 +y_0=3001128.55` | 159.7, -27.27, 171.57, -16.2 |

- Same HDF5 (`H5rad 2.3`) as the metropolitan file: `dataset1/data1/data` is `ACRR` in units of 0.01 mm over 5 minutes, `65535` = outside the radars' range, `65534` = no rain detected; an image every 5 minutes (the image ending 20:30 UTC was listed with a file dated 20:32), the same document of the date before the file (`validity_time`), only the latest image kept by the API.
- The grid is **rectangular and in Mercator** with a false origin that puts the top-left corner at (0 ; rows x 500 m): the four corners of each file (`UL_lat`, `UL_lon`, …) are reproduced by the formula to 5 mm. 72.6 %, 82.4 % and 80.9 % of the pixels are outside the radars' range (`65535`).
- Radars see Martinique, Guadeloupe (Antilles), La Réunion and Mauritius, Nouméa and Lifou; they do not see Saint-Martin (inside the Antilles grid, beyond the range), Mayotte, Guyane, French Polynesia or Saint-Pierre-et-Miquelon (no mosaic of their own in `DPRadar`).
- Quota: 850 requests per 5 minutes for `DPRadar`; the gateway makes 2 per area and per image, 8 per 5 minutes for the four areas.
- Licence: the metropolitan mosaic is under Licence Ouverte 2.0 as part of the "Package Radar" dataset (ADR 0030). The overseas mosaics were assumed to be in the same dataset; the listing of that dataset was not re-read for them.

## Decision

1. **The gateway** ([vigie-meteo#14](https://github.com/Axellum/vigie-meteo/pull/14)) describes each area (`RadarArea`: grid, box, projection written in numpy, no pyproj), keeps one store and one collector per area (`radar/` for France as before, `radar/<area>/` for the others) and serves `GET /v1/radar/{area}/frames` and `GET /v1/radar/{area}/tiles/{epoch}/{z}/{x}/{y}.png` (`france`, `antilles`, `reunion`, `new-caledonia`); the first routes stay and are France's. The size of a screen pixel, which decides how many 500 m pixels are pooled at a zoom, is worked out at the latitude of the area, and a grid that is not a multiple of the pooling factor keeps its edge.
2. **This API relays each area on its own** (`app/collectors/meteofrance_radar.py`): its own list of images (cached two minutes) and its own tiles in memory, asked from the gateway's area route. `GET /v1/radar/{area}` and `GET /v1/radar/{area}/tiles/…` replace the two France routes (`/v1/radar/france` is the same address as before; an unknown area is a 422 listing the four).
3. **A point gets the radar of the area whose grid holds it**: `rain_nowcast.radar_tile_url_template` points to the newest image of that area (France first, then the Antilles, La Réunion, New Caledonia by the box of their grid), with the credit of Météo-France; the other points are unchanged (RainViewer's when it is on). A point of the box that the radars do not reach (Saint-Martin, a boat far from the islands) gets the template and transparent tiles, as a point of France beyond a radar does. The boxes are the grids of the files, so the Réunion mosaic also serves Mauritius.
4. **Order of deployment**: the gateway first. An API in front of an older gateway answers `unavailable` for an overseas area (the gateway's 404) and does not touch France.

## Checked

- The projection reproduces the four corners of each of the three real files to 5 mm; the real files decode (the decoder checks the shape, the projection, the quantity, the codes and the date); on the evening of 2026-11-21 they held 24,343, 1,958 and 7,589 pixels of rain (at least 0.12 mm/h).
- Martinique, Guadeloupe, La Réunion, Mauritius, Nouméa and Lifou fall on pixels the radars cover; Saint-Martin, Mayotte and Vanuatu do not.
- Tests of the gateway (corners, decoding refused for another area's file, tiles at three zooms for each area, store shape, URL asked, routes) and of this API (each area asks its own address, the point, the routes, an old gateway).

## Not verified

- The registration of the overseas tiles on a map (only the corners and which places are covered): the metropolitan one was compared with PIAF; there is no equivalent model here.
- The licence of the overseas mosaics (assumed to be that of the "Package Radar" dataset, ADR 0030).
- The load of four areas on the Oracle gateway (memory, disk: about 150 to 270 kB per image, three hours kept), and the real behaviour of the tiles in the site's map for an overseas point.
