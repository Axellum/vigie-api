- **The sunshine and the fresh snow of each day**: `daily[]` of `/v1/point` and `/v1/snapshot` gives `sunshine_h` (hours of
  sunshine, Open-Meteo's sunshine duration) and `snowfall_cm` (the model's snowfall sum), `null` when the model gives none (in
  France the first four days come from the Météo-France models through the gateway, the later days from Open-Meteo). The daily
  Open-Meteo request asks two more variables and still weighs one call. The dashboard card says the hours of sunshine under the
  icon of each day and tints the day by their share of the daylight; it does not draw the fresh snow yet.
