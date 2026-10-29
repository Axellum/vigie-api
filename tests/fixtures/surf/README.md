# Surf fixtures

Real answers of Open-Meteo, asked on 2026-10-28 at about 23:10 (Paris) for Hossegor, Plage Centrale (43.6644, -1.4432), with the
parameters `app/collectors/surf.py` sends:

- `mfwam-hossegor.json`: Open-Meteo Marine, `models=meteofrance_wave` (Météo-France MFWAM, Copernicus Marine), the 10 hourly
  variables of `SWELL_VARIABLES`, `forecast_days=7`, `timeformat=unixtime`, `timezone=GMT`. 168 hours from 2026-10-28 00:00 UTC;
  the model cell that answered is 43.625, -1.458. The secondary swell is there but tiny (0 to a few centimetres, periods of 1 to
  2 s) for most hours: that is what the model gave.
- `wind-hossegor.json`: Open-Meteo forecast, `hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m`, `daily=sunrise,sunset`,
  same days, time format and time zone. The model is Open-Meteo's default (`best_match`).

Every spot at once (`GET /v1/surf/overview`): the real answers of one request naming the 51 spots of `app/data/surf-spots.json`, asked on
2026-10-29 at 09:05 UTC with the same parameters and the spots' positions comma-separated, **cut to three locations** (Lacanau-Océan,
Plage Centrale; Hossegor, Plage Sud; Biarritz, Plage de la Marbella, the 31st, 41st and 47th spots). Open-Meteo answers a list, one
item per location, numbered by `location_id` (absent on the first); the three items keep their content and their order of keys, and are
numbered 0 to 2 as Open-Meteo numbers three locations asked:

- `mfwam-three-spots.json`: Open-Meteo Marine, MFWAM. The model cells that answered: 45.042, -1.292; 43.625, -1.458; 43.458, -1.625.
- `wind-three-spots.json`: Open-Meteo forecast, the wind and the sunrise and sunset.
