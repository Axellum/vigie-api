# Contrat de Payload — API ↔ Terminal Waveshare 7B

`maj: 2026-10-18` · Format d'échange unifié entre `alerte_mondiale_api` et `alerte_mondiale_7b`.

---

## 1. Principes de Conception
1. **Un seul appel au démarrage (`GET /v1/snapshot`)** fournit à l'écran :
   - L'heure UTC serveur.
   - Le statut de menace mondiale global (`DEFCON` : `GREEN`, `YELLOW`, `ORANGE`, `RED`) et l'indice solaire `Kp`.
   - Les données météo complètes (actuel + horaire + 10 jours) des **zones mondiales surveillées** (jusqu'à 10 zones).
   - La liste triée des **alertes catastrophes mondiales actives** (jusqu'à 30 alertes, avec distance pré-calculée vers la zone surveillée la plus proche).
2. **Un flux continu (`WSS /v1/stream`)** pousse en temps réel :
   - `alert_new` / `alert_update` : dès qu'un séisme, tsunami, éruption ou ouragan tombe.
   - `weather_update` : rafraîchissement périodique léger des zones.

---

## 2. Schéma JSON du Snapshot (`/v1/snapshot`)

```json
{
  "version": "1.0",
  "server_utc": 1792334800,
  "global_status": {
    "max_severity": "RED",
    "active_red_count": 1,
    "active_orange_count": 3,
    "active_yellow_count": 8,
    "solar_kp": 5.3,
    "solar_storm_level": "G1"
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
      "current": {
        "temp_c": 16.4,
        "feels_like_c": 15.8,
        "humidity_pct": 72,
        "pressure_hpa": 1015,
        "wind_kmh": 18,
        "gust_kmh": 32,
        "wind_deg": 240,
        "rain_1h_mm": 0.0,
        "weather_code": 2,
        "uv_index": 3.1,
        "aqi_eu": 24
      },
      "rain_next_60m": [0.0, 0.0, 0.2, 0.8, 1.4, 0.5],
      "daily_10d": [
        {"d": "Lun", "code": 61, "tmin": 11, "tmax": 17, "rain_mm": 4.2, "wind_max": 35}
      ]
    }
  ],
  "hazards": [
    {
      "id": "usgs_us7000abcd",
      "category": "EARTHQUAKE",
      "severity": "RED",
      "title": "M 7.3 - Au large de Sendai, Japon",
      "source": "USGS+PTWC",
      "timestamp_utc": 1792334510,
      "lat": 38.10,
      "lon": 142.85,
      "value_primary": 7.3,
      "value_unit": "Mw",
      "depth_km": 32.0,
      "tsunami_warning": true,
      "nearest_zone_id": "z1",
      "nearest_zone_name": "Tokyo, JP",
      "distance_to_zone_km": 348
    }
  ]
}
```

---

## 3. Catégories d'Alertes Normalisées (`category`)

| Code `category` | Icône / Nature | Unité `value_primary` |
| :--- | :--- | :--- |
| `EARTHQUAKE` | Tremblement de terre | Magnitude (`Mw` / `ML`) + profondeur `depth_km` |
| `TSUNAMI` | Alerte Tsunami côtier / océanique | Hauteur estimée (`m`) ou niveau PTWC |
| `VOLCANO` | Éruption volcanique / Cendres | Code aviation / VEI |
| `HURRICANE` | Ouragan / Typhon / Cyclone tropical | Vents max soutenus (`km/h`) + Catégorie (1–5) |
| `TORNADO` | Alerte Tornade / Orage supercellulaire | Échelle EF / Vents (`km/h`) |
| `FLOOD` | Inondation majeure / Crue éclair | Cumul (`mm`) ou cote (`m`) |
| `EXTREME_RAIN` | Pluie diluvienne / Vigilance Pluie | Intensité (`mm/h`) |
| `WILDFIRE` | Feu de forêt majeur | Puissance radiative (`MW` FRP) |
| `SOLAR_STORM` | Tempête géomagnétique / Éruption solaire | Indice `Kp` (0 à 9) / Classe `G1`–`G5` |
