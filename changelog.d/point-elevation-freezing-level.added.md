- **The weather of a point at an altitude, and the freezing level of its hours**: `GET /v1/point?elevation=` (metres, -500 to
  9,000) computes the current conditions, the days and the hours at that altitude, the top of a ski area for instance; without it
  the weather stays the one of the forecast cell (about 5 km) at the cell's own altitude, which in the mountains can be hundreds of
  metres from the point's. `coordinates.elevation_m` repeats the altitude asked. The hours of `compact=false` give
  `freezing_level_m` (iso 0, Open-Meteo's, also in France where the Météo-France models have none); the hourly Open-Meteo request
  asks one more variable and weighs 1.8 calls instead of 1.7.
