# Recorded NASA FIRMS answers

Rows of real answers of FIRMS, played back by the tests (no test reaches the network, and no key is in these files). The header and
the values are the CSV's own; only a selection of rows was kept (the full files are 8 to 15 MB, the area API's reads 4 to 5 MB).

Two families: the **static files** (`https://firms.modaps.eosdis.nasa.gov/data/active_fire/{folder}/csv/{prefix}_Global_{24h|48h|7d}.csv`),
which the application reads, and the **area API** reads (`https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/world/1/{DATE}`),
which it reads only as a fallback, with a key.

## Static files (read on 2026-10-20)

| File | From | What it is |
| --- | --- | --- |
| `static_noaa20_24h.csv` | `noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_24h.csv`, read on 2026-10-20 evening | 237 rows kept, from 2026-10-19 00:07 to 2026-10-20 17:15 UTC: the "24 h" file holds **yesterday and the day read** (UTC days), so between 24 and 48 hours of history. A selection of rows of every region. No `instrument` column, `confidence` in words (`low`, `nominal`, `high`), `acq_time` zero-padded (`0039`), `daynight` `D` / `N`, only version `2.0NRT` |
| `static_noaa21_24h.csv` | `noaa-21-viirs-c2/csv/J2_VIIRS_C2_Global_24h.csv`, same day | 235 rows kept, the same selection for NOAA-21 (`satellite` `N21`), last detection 2026-10-20 16:20 UTC |
| `static_noaa21_48h.csv` | `noaa-21-viirs-c2/csv/J2_VIIRS_C2_Global_48h.csv`, same day | 241 rows kept over three UTC days (the 18th, 19th and 20th: the "48 h" file starts at 00:00 UTC two days before the day read): used to test the window of 48 hours |
| `static_file.headers.txt` | the response headers of NOAA-21's 24 h file | `ETag` (the MD5 of the whole file), `Last-Modified`, `Content-Length`, `Accept-Ranges: bytes`; a conditional request (`If-None-Match`, `If-Modified-Since`) gets **200 and the whole file**, never 304; `Range` gets 206. One request took 34.7 s to give its first byte: the server stalls from time to time |

Columns: `latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti5,frp,daynight`.

## Area API (read on 2026-10-20)

| File | From | What it is |
| --- | --- | --- |
| `viirs_noaa20_nrt_world_2026-10-20.csv` | `VIIRS_NOAA20_NRT`, `world`, one day, read on 2026-10-20 at 18:10 UTC (55,239 rows; 258 kept) | Rows of every region (Iberia and France, Italy to Turkey, Africa, Australia, the far east of Russia, Fiji on both sides of the antimeridian, the United States and Canada, South America, India and South-East Asia), the ten strongest of the world, and some of each confidence (`l`, `n`, `h`) and of each processing version (`2.0NRT`, and `2.1URT`: ultra real-time, United States and Canada only). `acq_time` is `HHMM` **without a leading zero** (`1` is 00:01, `949` is 09:49) |
| `viirs_noaa21_nrt_world_2026-10-20.csv` | `VIIRS_NOAA21_NRT`, same request, read at 18:33 UTC (47,048 rows; 218 kept) | The same selection for NOAA-21 (`satellite` `N21`; its ultra real-time version is `2.0URT`) |
| `world_read.headers.txt` | the response headers of the first read | `HTTP/2 200`, `content-type: text/plain;charset=UTF-8`, `cache-control: no-cache`: **no `ETag`, no `Last-Modified`, no `Content-Length`, no `Content-Encoding`** (a world read is never compressed on the wire, and a `HEAD` request gives no size either), so a read cannot be conditional |

Columns: `latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight`
(VIIRS). `confidence` is `l`, `n` or `h` (low, nominal, high); `frp` is in megawatts; `scan` and `track` are the pixel size in
kilometres; `bright_ti4` and `bright_ti5` are brightness temperatures in kelvin.

NASA's credit and disclaimer: `SOURCES.md`.
