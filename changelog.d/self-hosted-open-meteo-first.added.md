- **Open-Meteo from a self-hosted instance first (ADR 0039)**: when `VIGIE_OPEN_METEO_FORECAST_URL`, `VIGIE_OPEN_METEO_MARINE_URL`
  or the new `VIGIE_OPEN_METEO_AIR_QUALITY_URL` points to a self-hosted Open-Meteo, the forecast, the sea, the surf spots and
  the air quality are asked there first, outside the public quota; the public API answers when the instance cannot be reached,
  fails or has no value for the place (`VIGIE_OPEN_METEO_PUBLIC_FALLBACK=0` turns that off).
