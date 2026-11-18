# 0030. The radar mosaic of Météo-France over France, drawn as tiles by the gateway and relayed by this API

- Status: accepted
- Date: 2026-11-18
- Number: reserved with `scripts/prochain_adr.py` and in the state file of the sessions.

## Context

The owner decided on 2026-11-18 to plug the Météo-France APIs the project subscribes to, a lot at a time. This is the lot of the radar. `GET /v1/point` carried `rain_nowcast.radar_tile_url_template` and `radar_timestamp_utc` from RainViewer's free API, which is for personal use: a public instance (this one) turns it off, so both fields were `null` in France and everywhere else.

What Météo-France offers (read on 2026-11-18 with the project's key):

- `DPRadar/v1`: the mosaic of the radars of metropolitan France (`METROPOLE`) and of three overseas areas (`ANTILLES`, `REUNION`, `NOUVELLE-CALEDONIE`), every 5 minutes, only the **latest** image (no history). Products `LAME_D_EAU` (the rain accumulated over the 5 minutes before the date) and `REFLECTIVITE`; at 500 m an HDF5 file of 2.0 MB (`T_IPRN20_C_LFPW_…h5`, ODIM `H5rad 2.3`), at 1 km a BUFR file. The image ending 08:25 UTC was listed by the API at 08:29 (its file dated 08:27).
- `DPPaquetRadar`: a tar.gz of 18.5 MB of BUFR and HDF5 files for the last quarter of an hour. Not used: ten times more to download for what `DPRadar` gives in one file.
- The HDF5 mosaic is a 3,472 x 3,472 grid of 16-bit integers (`ACRR`, 0.01 mm, `65535` outside the radars' range: 51 % of the grid), in a polar stereographic projection (`+proj=stere +lat_0=90 +lon_0=0 +lat_ts=45`), corner at (0 ; 1,736,000) m.
- Licence: the catalogue entry of the API names none (`Source : Météo-France` in every answer); the **"Package Radar" dataset of Météo-France on data.gouv.fr is under Licence Ouverte 2.0** (<https://www.data.gouv.fr/datasets/package-radar/informations>), and the mosaics are what that package holds. Licence Ouverte 2.0 allows any use, commercial included, with the source named.

## Decision

1. **The gateway does the work** ([vigie-meteo#7](https://github.com/Axellum/vigie-meteo/pull/7), [#8](https://github.com/Axellum/vigie-meteo/pull/8)): it reads the date of the newest image, downloads the file when it is new (two requests every 5 minutes of the 850 per 5 minutes the portal allows), keeps three hours of images as intensity classes (9 classes from 0.1 to 64 mm/h, 230 kB each on disk), and draws **256 px Web Mercator PNG tiles on demand** (zooms 3 to 10; the projection is written in numpy, reproducing the four corners of the file to 0.05 m; below zoom 7 a screen pixel stands for 2 to 8 radar pixels and shows the strongest class of the block, so a shower stays visible). This API has no HDF5 reader and no image code.
2. **This API relays** (`app/collectors/meteofrance_radar.py`): `GET /v1/radar/france` lists the images, the tile path, the area, the zooms and the legend (from the gateway); `GET /v1/radar/france/tiles/{epoch}/{z}/{x}/{y}.png` relays a tile (immutable, `Cache-Control: public, max-age=86400, immutable`: the address holds the date). A tile is relayed only for an image the gateway lists, within its zoom range and map; a tile that does not touch the radar's area is a transparent PNG without a request. 600 tiles are kept in memory and a crowd asking for the same new tile makes one request to the gateway.
3. **`rain_nowcast.radar_tile_url_template`** points, for a point in the France box, to the newest image: a **path relative to the root of this API** (`/v1/radar/france/tiles/<epoch>/{z}/{x}/{y}.png`, RainViewer's was an absolute URL: the API does not know its public address, the client knows where it read it), `radar_timestamp_utc` is that image's date, and the credit is Météo-France's. In France it replaces RainViewer's even when RainViewer is on; elsewhere RainViewer's stays when it is on. When the gateway has no image, the fields are what RainViewer gives, or `null`.
4. **Not a forecast and not for safety**: the tiles show what the radars measured; the next hour is `rate_5m_mm_h` (PIAF).

## Checked

- The projection reproduces the four corners of the real file (`UL`, `UR`, `LL`, `LR`) to 0.05 m. The registration of the image on the map was compared with PIAF's map the same minute (rain rate of PIAF's first step against the mosaic resampled on PIAF's nodes, 167,000 nodes): the correlation is highest with no shift (0.77) and falls to 0.52-0.56 with a shift of four pixels (2 km) north-south; it was also seen on a MapLibre map over OpenFreeMap (showers over the Channel, the Bay of Biscay and the Pyrenees where they were).
- Gateway on Oracle: image fetched in 0.65 s, 230 kB stored, a tile in 30 ms, memory 130 to 300 MB of the container's 1 GB.

## Not verified

- The mosaic against rain gauges (only against PIAF, which is itself fed by the radars). A radar measures the sky, not the ground: in mountains, far from a radar or in a strong wind the rain on the ground differs.
- The overseas mosaics (other grids and projections: the code reads only `METROPOLE`), the reflectivity product, and the `PAG`/`PAM` hail products of `DPPaquetRadar`.
- The licence of the API itself (its page is rendered by script and was not read): the conclusion rests on the dataset of the same data.
- The load of a crowd on the 3 GB VPS, and its bandwidth: a map loads about a dozen tiles of 1 to 6 kB per image.
