# Contrat de Payload — API ↔ Terminal Waveshare 7B, Mobile GPS & Clients SIG/CAP

`maj: 2026-10-18` · Spécification des formats d'échange de `alerte_mondiale_api` (OpenAPI 3.1).

---

## 1. Architecture des Endpoints (`/v1/*`)

| Endpoint | Méthode | Standard / Format | Usage Principal |
| :--- | :--- | :--- | :--- |
| `/v1/health` | `GET` | JSON | Santé globale du serveur, écrans supportés, résumé des collecteurs |
| `/v1/providers` | `GET` | JSON | État détaillé de chaque provider (Circuit Breaker, latence ms, erreurs, âge du cache) |
| `/v1/point` | `GET` | JSON Compact (`< 6 Ko`) | **Mode GPS Mobile / Bateau / M5Stack Tab5 (GPS Grove)** : météo hyper-locale (`lat, lon`), pluie 15 min, radar, conditions marines (`marine=true`), météo spatiale et alertes dans un rayon `radius_km` avec cap et ETA tsunami. Supporte `device_type=tab5\|waveshare-7b\|marine\|web`, `max_hazards`, `compact=true` |
| `/v1/snapshot` | `GET` | JSON Compact (`< 12 Ko`) | **Mode Multi-Écrans (`waveshare-7b` 1024×600, `tab5` 1280×720, `marine`, `web`)** : statut global DEFCON + météo multi-zones + alertes mondiales dédupliquées (`?device_type=...&max_hazards=...&compact=true`) |
| `/v1/geojson/hazards` | `GET` | **IETF RFC 7946 GeoJSON** | `FeatureCollection` standard pour cartes Web (MapLibre, Leaflet, Mapbox) et SIG (QGIS) |
| `/v1/cap/alerts` | `GET` | **OASIS CAP 1.2 JSON** | Alertes normalisées selon le *Common Alerting Protocol 1.2* (`urgency`, `severity`, `certainty`, `area`) |
| `/v1/simulate` | `POST` | JSON | Injection de scénarios de crise réalistes (Séisme M7.8 + Tsunami, Ouragan Cat 5, Volcan, Grain en mer) |
| `/v1/stream` | `WS` | WebSocket JSON | Flux temps réel (`alert_new`, `simulation_update`, `initial_snapshot`) |

---

## 2. Schéma JSON du Point GPS / Marine (`GET /v1/point`)

Idéal pour un terminal embarqué sur un bateau ou un véhicule disposant d'une puce GPS (`?lat=47.75&lon=-3.36&marine=true&radius_km=500&sog_knots=7.5&cog_deg=245`) :

```json
{
  "version": "1.1",
  "server_utc": 1792334800,
  "coordinates": {
    "lat": 47.75,
    "lon": -3.36,
    "sog_knots": 7.5,
    "cog_deg": 245.0
  },
  "weather_provider": "Open-Meteo (AROME/IFS HD)",
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
  "space_weather": {
    "solar_kp": 3.3,
    "solar_storm_level": "G0",
    "gps_degraded": false,
    "hf_radio_blackout_risk": false
  },
  "nearby_hazards": [
    {
      "id": "eq_usgs_7000abcd",
      "category": "EARTHQUAKE",
      "severity": "ORANGE",
      "title": "M 6.4 - Dorsale Atlantique Nord",
      "source": "USGS+EMSC",
      "sources": ["USGS", "EMSC"],
      "timestamp_utc": 1792334510,
      "lat": 45.20,
      "lon": -28.10,
      "value_primary": 6.4,
      "value_unit": "Mw",
      "depth_km": 10.0,
      "tsunami_warning": true,
      "tsunami_eta_minutes": 162,
      "distance_to_zone_km": 1924.5,
      "bearing_deg": 268.4,
      "bearing_cardinal": "W"
    }
  ]
}
```

---

## 3. Schéma JSON du Snapshot (`GET /v1/snapshot`)

Conçu pour tenir systématiquement sous la barre des **12 Ko** pour le terminal Waveshare 7B :

```json
{
  "version": "1.1",
  "server_utc": 1792334800,
  "simulation_active": false,
  "global_status": {
    "max_severity": "RED",
    "active_red_count": 1,
    "active_orange_count": 2,
    "active_yellow_count": 4,
    "solar_kp": 5.3,
    "solar_storm_level": "G1",
    "gps_degraded": false,
    "hf_radio_blackout_risk": true,
    "weather_fallback_active": false
  },
  "zones": [
    {
      "id": "z0",
      "name": "Paris, FR",
      "lat": 48.8566,
      "lon": 2.3522,
      "utc_offset_s": 7200,
      "alert_radius_km": 500,
      "local_alert_level": "GREEN",
      "weather_source": "Open-Meteo",
      "current": {
        "temp_c": 16.4,
        "feels_like_c": 15.8,
        "humidity_pct": 72,
        "pressure_hpa": 1015.0,
        "wind_kmh": 18.0,
        "gust_kmh": 32.0,
        "wind_deg": 240,
        "rain_1h_mm": 0.0,
        "weather_code": 2,
        "uv_index": 3.1,
        "aqi_eu": 24
      },
      "rain_next_60m": [0.0, 0.0, 0.2, 0.8],
      "daily_10d": [
        {"d": "Lun", "code": 61, "tmin": 11.0, "tmax": 17.0, "rain_mm": 4.2, "wind_max": 35.0}
      ]
    }
  ],
  "hazards": [
    {
      "id": "usgs_us7000abcd",
      "category": "EARTHQUAKE",
      "severity": "RED",
      "title": "M 7.8 - Au large de Sendai, Japon",
      "source": "USGS+EMSC+GDACS",
      "sources": ["USGS", "EMSC", "GDACS"],
      "timestamp_utc": 1792334510,
      "lat": 38.10,
      "lon": 142.85,
      "value_primary": 7.8,
      "value_unit": "Mw",
      "depth_km": 28.0,
      "tsunami_warning": true,
      "tsunami_eta_minutes": 29,
      "nearest_zone_id": "z1",
      "nearest_zone_name": "Tokyo, JP",
      "distance_to_zone_km": 348.2,
      "bearing_deg": 38.5,
      "bearing_cardinal": "NE"
    }
  ]
}
```

---

## 4. Interopérabilité Standards : GeoJSON (RFC 7946) & OASIS CAP 1.2

### A. `GET /v1/geojson/hazards` (RFC 7946)
Retourne une `FeatureCollection` dont chaque `Feature` possède une géométrie `Point` (`coordinates: [lon, lat, depth_km]`), une `bbox` globale `[min_lon, min_lat, max_lon, max_lat]` et toutes les métadonnées enrichies dans `properties`.

### B. `GET /v1/cap/alerts` (OASIS CAP 1.2)
Retourne une enveloppe d'alertes respectant le *Common Alerting Protocol v1.2* :
- `identifier`, `sender`, `sent` (ISO 8601 UTC), `status` (`Actual` ou `Exercise` en mode simulation), `msgType` (`Alert`), `scope` (`Public`).
- Bloc `info` : `category` (`Geo`, `Met`, `Safety`, `Fire`), `event`, `urgency` (`Immediate`, `Expected`, `Future`), `severity` (`Extreme`, `Severe`, `Moderate`, `Minor`), `certainty` (`Observed`, `Likely`), `headline`, `description`, `instruction`, et `area` (`circle` au format `"lat,lon radius_km"`).
