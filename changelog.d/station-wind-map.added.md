- **Wind map**: `GET /v1/stations/wind?bbox=` lists the wind measured now (the latest 6-minute observation) by every Météo-France
  ground station of a box, strongest first, with its position, name and altitude; the 6-minute package already read for the
  coastal stations serves it, so no new request goes to Météo-France. Names and altitudes come from `POSTES_MF.csv`
  (`tools/build_station_names.py`).
