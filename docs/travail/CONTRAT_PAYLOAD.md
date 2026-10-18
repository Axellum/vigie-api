# Contrat de Payload — API ↔ Terminal Waveshare 7B, M5Stack Tab5, Marine & Clients SIG/CAP

`maj: 2026-10-18` · Spécification des formats d'échange de `alerte_mondiale_api` (OpenAPI 3.1).

---

## 1. Architecture des Endpoints (`/v1/*`)

| Endpoint | Méthode | Standard / Format | Usage Principal |
| :--- | :--- | :--- | :--- |
| `/v1/health` | `GET` | JSON | Santé globale du serveur, mode SQLite WAL, écrans supportés, résumé des 12 collecteurs |
| `/v1/providers` | `GET` | JSON | État détaillé de chaque provider (Circuit Breaker, latence ms, erreurs, âge du cache) |
| `/v1/zones` | `GET`/`POST`/`DELETE` | JSON | Gestion dynamique et persistante (SQLite WAL) de la Watchlist des zones mondiales surveillées |
| `/v1/history` | `GET` | JSON | Historique SQLite WAL des catastrophes mondiales dédupliquées (`?category=...&min_severity=...&limit=50`) |
| `/v1/point` | `GET` | JSON Compact (`< 6 Ko`) | **Mode GPS Mobile / Bateau / M5Stack Tab5 (GPS Grove)** : météo hyper-locale (`lat, lon`), pluie 15 min, radar, conditions marines (`marine=true`), crues fluviales GloFAS (`flood_risk`), qualité de l'air / poussières (`air_quality`), météo spatiale et alertes dans un rayon `radius_km` |
| `/v1/snapshot` | `GET` | JSON Compact (`< 12 Ko`) | **Mode Multi-Écrans (`waveshare-7b` 1024×600, `tab5` 1280×720, `marine`, `web`)** : statut global DEFCON + météo multi-zones + alertes mondiales dédupliquées |
| `/v1/embedded/frame` | `GET` | **Texte Pipe-Delimited + CRC32 (`< 4 Ko`)** | **Trame C++ sans `malloc`** (`HEAD\|...`, `GPS\|...`, `ZONE\|...`, `HAZ\|...`, `END\|<CRC32>`) décodable en un seul passage sur ESP32-P4 |
| `/v1/geojson/hazards` | `GET` | **IETF RFC 7946 GeoJSON** | `FeatureCollection` standard pour cartes Web (MapLibre, Leaflet, Mapbox) et SIG (QGIS) |
| `/v1/cap/alerts` | `GET` | **OASIS CAP 1.2 JSON** | Alertes normalisées selon le *Common Alerting Protocol 1.2* (`urgency`, `severity`, `certainty`, `area`) |
| `/v1/simulate` | `POST` | JSON | Injection de scénarios de crise réalistes (Séisme M7.8 + Tsunami, Ouragan Cat 5, Volcan + Cendres, Grain en mer) |
| `/v1/stream` | `WS` | WebSocket JSON | Flux temps réel (`alert_new`, `simulation_update`, `initial_snapshot`) |

---

## 2. Spécification de la Trame Compacte Embarquée (`GET /v1/embedded/frame`)

Pour les firmwares C++ ESP32-P4 (`alerte_mondiale_7b` sur Waveshare 7B et M5Stack Tab5), l'endpoint `GET /v1/embedded/frame?device_type=waveshare-7b|tab5&lat=48.8566&lon=2.3522&marine=true` retourne une trame `text/plain; charset=utf-8` ultra-compacte (`< 4 Ko`), conçue pour être parsée avec `strtok_r` / `sscanf` dans un tampon statique sans aucune allocation dynamique (`malloc`) :

```text
HEAD|1.1|1792334800|tab5|RED|2|1|0|6.3|G2|1|1|1
GPS|35.6800|139.6900|RED|17.5|22.0|34.0|1014.0|0.20|2|1.20|MODERATE|GREEN|20|0.0
ZONE|z0|Paris, FR|48.8566|2.3522|GREEN|17.5|22.0|0.20|2|22
ZONE|z1|Tokyo, JP|35.6762|139.6503|RED|17.5|22.0|0.20|2|22
HAZ|sim_eq_sendai_m78|EARTHQUAKE|RED|38.1000|142.8500|7.8|Mw|1|29|383.3|45.5|NE|USGS+EMSC+GDACS|M 7.8 - Fosse du Japon au large de Sendai (SIMULATION)
END|A1B2C3D4
```

### Structure exacte des enregistrements :
1. **`HEAD`** : `HEAD|<version>|<server_utc>|<device_type>|<max_severity>|<red_cnt>|<orange_cnt>|<yellow_cnt>|<solar_kp>|<solar_g>|<gps_degraded_0_1>|<hf_risk_0_1>|<sim_active_0_1>`
2. **`GPS`** *(optionnel, présent si `lat` et `lon` sont passés en paramètres)* :
   `GPS|<lat>|<lon>|<local_alert>|<temp_c>|<wind_kmh>|<gust_kmh>|<pressure_hpa>|<rain_1h_mm>|<wmo_code>|<wave_m>|<sea_state>|<flood_alert>|<aqi_eu>|<dust_ugm3>`
3. **`ZONE`** *(1 ligne par zone de la Watchlist)* :
   `ZONE|<id>|<name>|<lat>|<lon>|<local_alert>|<temp_c>|<wind_kmh>|<rain_1h_mm>|<wmo_code>|<aqi_eu>`
4. **`HAZ`** *(1 ligne par catastrophe active triée par criticité)* :
   `HAZ|<id>|<category>|<severity>|<lat>|<lon>|<value_primary>|<unit>|<tsunami_0_1>|<eta_min>|<dist_km>|<bearing_deg>|<cardinal>|<sources>|<title>`
5. **`END`** : `END|<CRC32_HEX8>` — Somme de contrôle **CRC32 IEEE 802.3** (8 chiffres hexadécimaux majuscules) calculée sur tous les octets qui précèdent `END|` (incluant le `\n` de la ligne précédente).

---

## 3. Schéma JSON du Point GPS / Marine (`GET /v1/point`)

Inclut désormais en plus des conditions marines (`marine`), le risque d'inondation/crue fluviale Copernicus GloFAS (`flood_risk`) et la qualité de l'air / poussières / cendres volcaniques (`air_quality`) :

```json
{
  "version": "1.1",
  "server_utc": 1792334800,
  "device_type": "tab5",
  "compact": true,
  "coordinates": {
    "lat": 47.75,
    "lon": -3.36,
    "sog_knots": 7.5,
    "cog_deg": 245.0
  },
  "weather_provider": "Open-Meteo (AROME/HRRR/IFS HD)",
  "fallback_used": false,
  "local_alert_level": "GREEN",
  "current": {
    "temp_c": 16.4,
    "feels_like_c": 15.8,
    "humidity_pct": 78,
    "pressure_hpa": 1013.2,
    "wind_kmh": 24.5,
    "gust_kmh": 38.0,
    "wind_deg": 250,
    "rain_1h_mm": 0.2,
    "weather_code": 2,
    "uv_index": 3.0,
    "aqi_eu": 20
  },
  "rain_nowcast": {
    "interval_minutes": 15,
    "mm_per_step": [0.0, 0.2, 0.6, 0.1],
    "rain_expected_60m": true,
    "max_mm_h": 2.4,
    "radar_tile_url_template": "https://tilecache.rainviewer.com/v2/radar/1792334400/256/{z}/{x}/{y}/2/1_1.png",
    "radar_timestamp_utc": 1792334400
  },
  "marine": {
    "wave_height_m": 1.8,
    "wave_direction_deg": 265,
    "wave_period_s": 9.5,
    "swell_wave_height_m": 1.5,
    "swell_wave_direction_deg": 270,
    "swell_wave_period_s": 11.2,
    "ocean_current_velocity_kmh": 1.4,
    "ocean_current_direction_deg": 95,
    "sea_state_label": "MODERATE",
    "navigation_warning": false
  },
  "flood_risk": {
    "river_discharge_m3s": 42.0,
    "river_discharge_mean_m3s": 40.0,
    "river_discharge_max_m3s": 46.0,
    "anomaly_ratio": 1.15,
    "flood_alert_level": "GREEN"
  },
  "air_quality": {
    "european_aqi": 20,
    "us_aqi": 25,
    "pm2_5_ugm3": 5.0,
    "pm10_ugm3": 12.0,
    "uv_index": 3.0,
    "dust_ugm3": 2.0,
    "ash_or_dust_warning": false
  },
  "space_weather": {
    "solar_kp": 3.3,
    "solar_storm_level": "G0",
    "gps_degraded": false,
    "hf_radio_blackout_risk": false
  },
  "nearby_hazards": []
}
```
