# Open-Meteo Air Quality / CAMS Europe pollen fixtures

Recorded on **2026-11-15** directly from `https://air-quality-api.open-meteo.com/v1/air-quality` with the production query parameters (`current=european_aqi,us_aqi,pm2_5,pm10,uv_index,dust`, `hourly=alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,olive_pollen,ragweed_pollen`, `forecast_days=4`).

- `malaga_2026-11-15.json` — `latitude=36.7&longitude=-4.4` (Malaga, Spain, inside CAMS Europe). Non-zero autumn `mugwort_pollen` (`2.5 grains/m³` at `current.time = 2026-11-15T18:00`) and 4 daily forecast peaks (`4.6`, `2.5`, `4.7`, `0.5`).
- `paris_2026-11-15.json` — `latitude=48.9&longitude=2.4` (Paris, France, inside CAMS Europe). Off-season November values where all six pollen series return `0.0`.
- `new_york_outside_europe_2026-11-15.json` — `latitude=40.7&longitude=-74.0` (New York, USA, outside CAMS Europe). All six pollen series return `null`, proving CAMS pollen coverage is restricted to the European domain (`~30°N..72°N`, `25°W..45°E`) and must never be fabricated outside Europe.

Attribution required by Open-Meteo and Copernicus CAMS:
- `Weather data by Open-Meteo.com (https://open-meteo.com/)`
- `Air quality: CAMS ENSEMBLE data provider (Copernicus Atmosphere Monitoring Service)`
- Copernicus CAMS licence clause 1.3 (`https://apps.ecmwf.int/datasets/licences/cams`): `Generated using Copernicus Atmosphere Monitoring Service Information 2026`
