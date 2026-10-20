# Recorded NASA FIRMS answers

Rows of real answers of the FIRMS area API (`https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/world/1/{DATE}`),
played back by the tests (no test reaches the network, and no key is in these files). The header and the values are the CSV's
own; only a selection of rows was kept (the full reads are 4 to 5 MB).

| File | From | What it is |
| --- | --- | --- |
| `viirs_noaa20_nrt_world_2026-10-20.csv` | `VIIRS_NOAA20_NRT`, `world`, one day, read on 2026-10-20 at 18:10 UTC (55,239 rows; 258 kept) | Rows of every region (Iberia and France, Italy to Turkey, Africa, Australia, the far east of Russia, Fiji on both sides of the antimeridian, the United States and Canada, South America, India and South-East Asia), the ten strongest of the world, and some of each confidence (`l`, `n`, `h`) and of each processing version (`2.0NRT`, and `2.1URT`: ultra real-time, United States and Canada only). `acq_time` is `HHMM` **without a leading zero** (`1` is 00:01, `949` is 09:49) |
| `viirs_noaa21_nrt_world_2026-10-20.csv` | `VIIRS_NOAA21_NRT`, same request, read at 18:33 UTC (47,048 rows; 218 kept) | The same selection for NOAA-21 (`satellite` `N21`; its ultra real-time version is `2.0URT`) |
| `world_read.headers.txt` | the response headers of the first read | `HTTP/2 200`, `content-type: text/plain;charset=UTF-8`, `cache-control: no-cache`: **no `ETag`, no `Last-Modified`, no `Content-Length`, no `Content-Encoding`** (a world read is never compressed on the wire, and a `HEAD` request gives no size either), so a read cannot be conditional |

Columns: `latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight`
(VIIRS). `confidence` is `l`, `n` or `h` (low, nominal, high); `frp` is in megawatts; `scan` and `track` are the pixel size in
kilometres; `bright_ti4` and `bright_ti5` are brightness temperatures in kelvin.

NASA's credit and disclaimer: `SOURCES.md`.
