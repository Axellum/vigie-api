# Tide fixtures

- `pytmd-reference.json`: **pyTMD 3.0.9**'s own prediction of the EOT20 ocean tide (the call is in the file) at three model cells
  (Brest, Saint-Malo, Honolulu), every hour over two days in October 2026 and March 2039, in millimetres. It was written from the EOT20
  files with the environment of `tools/build_tides.py`; `tests/test_tides.py` checks that the API's own prediction stays within 5 mm of it.
- `refmar-3-sample.json`: the answer of REFMAR (SHOM, Licence Ouverte 2.0) for the tide gauge of Brest (id 3), raw high-frequency data
  (`sources=1`), cut to the first 70 minutes of 2026-09-27, as `tools/measure_tides.py` downloaded it on 2026-10-28.
- `noaa-9414290-10-sample.json`, `noaa-9414290-hilo-sample.json`: the first values of NOAA CO-OPS's predictions for San Francisco
  (9414290, `datum=MSL`, `time_zone=gmt`, `units=metric`), every 10 minutes and the high and low waters, downloaded the same day.
