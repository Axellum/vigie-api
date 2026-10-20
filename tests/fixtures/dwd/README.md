# Recorded DWD answers

Real answers of `opendata.dwd.de`, recorded on **2026-10-20 at 11:55 UTC** and played back by the tests (no test reaches
the network). The ZIP files are kept as the server sent them, byte for byte; the `*.headers.txt` files are the response
headers.

| File | From | Kept |
| --- | --- | --- |
| `status_district_mul.zip` | `https://opendata.dwd.de/weather/alerts/cap/DISTRICT_DWD_STAT/Z_CAP_C_EDZW_LATEST_PVW_STATUS_PREMIUMDWD_DISTRICT_MUL.zip` | all of it: 16,389 bytes, 4 CAP documents with 8 languages each, 7 areas, all `Minor` wind (`II` 11 and 51): two coastal-sea warnings of the Seewetterdienst Hamburg and five areas of the North Sea coast and islands (Borkum, Friesland, Aurich, Wittmund, Helgoland) |
| `difference_district_mul.zip` | the `DIFFERENCE` product of the same production (`DISTRICT_DWD_DIFF/`) | all of it: an empty ZIP (22 bytes), because nothing had changed since the previous production |

`ETag: "6ad75578-4005"` and `Last-Modified: Tue, 20 Oct 2026 11:50:16 GMT`: a request that repeats either validator is
answered `304` (checked the same day), which `tests/test_dwd.py` and `tests/simulation.py` replay. The server sends no
`Cache-Control`.

No orange or red warning, no hole in an area and no all-clear were in force that day: `tests/test_dwd.py` reaches them with
**synthetic** documents built in the test, in the structure of these ones, and says so.

Licence of the content: CC BY 4.0, source `Deutscher Wetterdienst`; the areas carry `© GeoBasis-DE / BKG 2021 (Daten
modifiziert)`. See `SOURCES.md`.
