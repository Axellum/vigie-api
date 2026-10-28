# Recorded answers of the NOAA Space Weather Prediction Center

Real answers of `https://services.swpc.noaa.gov`, recorded on **2026-10-28** around 20:32 UTC and played back by the tests (no test
reaches the network). Nothing in these files was edited; the OVATION grid is stored compressed (`gzip -9`), the others as they came.

| File | From | What it is |
| --- | --- | --- |
| `ovation_aurora_latest.json.gz` | `/json/ovation_aurora_latest.json` | The OVATION grid: `Observation Time` 2026-10-28T20:17:00Z, `Forecast Time` 21:26:00Z, `Data Format` `[Longitude, Latitude, Aurora]`, 65,160 cells (360 longitudes by 181 latitudes), highest value 27 (a quiet evening). `Last-Modified: Wed, 28 Oct 2026 20:30:20 GMT`; 923,762 bytes, 157,087 on the wire. A request with `If-Modified-Since` of that date was answered `304` with no body |
| `noaa-planetary-k-index-forecast.json` | `/products/noaa-planetary-k-index-forecast.json` | 81 intervals of 3 hours, a list of objects (`time_tag`, `kp`, `observed`, `noaa_scale`): observed from 2026-10-21 00:00, then estimated, then predicted to 2026-10-31 00:00 (Kp 5.00, `G1`: the watch of the same evening). `Last-Modified: Wed, 28 Oct 2026 20:32:04 GMT` |
| `propagated-solar-wind-1-hour.json` | `/products/geospace/propagated-solar-wind-1-hour.json` | The solar wind of the last hour, one row a minute under a header row (`time_tag`, `speed`, `density`, `temperature`, `bx`, `by`, `bz`, `bt`, `vx`, `vy`, `vz`, `propagated_time_tag`) |
| `alerts.json` | `/products/alerts.json` | 89 messages issued between 2026-09-29 05:02 and 2026-10-28 16:38 UTC, among them the watch `WATA20` serial 1133 (16:38 UTC, "Geomagnetic Storm Category G1 Predicted" for 31 October) and electron flux, radio and X-ray messages that the aurora route leaves out |

Public domain by the NWS disclaimer, whose extension to SWPC is not verified (see `SOURCES.md`).
