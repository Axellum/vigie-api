# NOAA NDBC recorded answers

- `latest_obs-2026-11-15.txt`: `https://www.ndbc.noaa.gov/data/latest_obs/latest_obs.txt` as answered on
  `Sun, 15 Nov 2026 16:25:16 GMT` (99,841 bytes; `Cache-Control: max-age=600, must-revalidate`, `ETag: "18601-660530abab097-gzip"`,
  `Last-Modified: Sun, 15 Nov 2026 16:20:38 GMT`, `Content-Type: text/plain; charset=ISO-8859-1`).
- `activestations-2026-11-15.xml`: `https://www.ndbc.noaa.gov/activestations.xml` as answered on
  `Sun, 15 Nov 2026 16:33:28 GMT` (273,696 bytes; `Cache-Control: max-age=60, must-revalidate`, `ETag: "42d20-66053336e6c10-gzip"`,
  `Last-Modified: Sun, 15 Nov 2026 16:32:01 GMT`, `Content-Type: text/xml`).

Both are in the U.S. public domain under the NWS disclaimer (`https://www.weather.gov/disclaimer`), unchanged.

What they hold that tests rely on:

- `latest_obs-2026-11-15.txt` has 837 station rows, 194 with a wave height (`WVHT != MM`);
- `62107` (`Sevenstones Lightship`, `type="fixed"`, UK Met Office, `50.102, -6.100`) and `62304` (`Sandettie Lightship`) report
  wave height (`1.4` m and `0.7` m) and average period (`APD = 6` s) with `DPD` and `MWD` set to `MM`;
- `44025` (`LONG ISLAND - 30 NM South of Islip, NY`) and `46026` (`SAN FRANCISCO - 18NM West of San Francisco, CA`) report
  `WVHT`, `DPD`, `APD` and `MWD`;
- `46042` and `41009` report weather only (`WVHT = MM`) and are ignored by the wave-buoy parser;
- `46181` reports `WVHT = 0.0`, while all 194 wave stations in `latest_obs-2026-11-15.txt` exist in `activestations-2026-11-15.xml`;
- Korean KMA buoys `22101`..`22104`, `22108` report `DPD = 0`, which is kept as `None` (a wave period cannot be zero).

Used by `tests/test_ndbc.py`, `tests/test_buoys.py`, `tests/test_live_feeds_tool.py` and `tests/simulation.py` to replay without the network.
