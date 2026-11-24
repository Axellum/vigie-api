- **PIAF forecast tiles**: a new run every 5 minutes draws the same times again, but a tile was kept an hour by browsers
  (`immutable`) and with no time limit in memory, so the map could show the rain of an old run. `tile_url_template` of
  `GET /v1/radar/france/nowcast` now holds the run (`?run=`); a tile is kept in memory under its run and is immutable only
  when asked with the run that drew it (a vigie-meteo gateway that names it in `X-Piaf-Run`), otherwise cached a minute.
