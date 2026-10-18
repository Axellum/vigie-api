# Contrat de Payload — API ↔ Terminal Waveshare 7B, M5Stack Tab5, Marine & Clients SIG/CAP

`maj: 2026-10-18` · Spécification des formats d'échange de `alerte_mondiale_api` (OpenAPI 3.1).

---

## 1. Architecture des Endpoints (`/v1/*` & `/dashboard`)

| Endpoint | Méthode | Standard / Format | Usage Principal |
| :--- | :--- | :--- | :--- |
| `/dashboard` | `GET` | HTML5 Tactique | **Salle de Crise & Supervision Web** (état des 12 providers, simulateur 1-clic, inspecteur de trame C++) |
| `/v1/health` | `GET` | JSON | Santé globale du serveur, mode SQLite WAL, scheduler, écrans supportés, résumé des 12 collecteurs |
| `/v1/providers` | `GET` | JSON | État détaillé de chaque provider (Circuit Breaker, latence ms, erreurs, âge du cache) |
| `/v1/scheduler/status` | `GET` | JSON | État du scheduler temps réel, nombre de cycles et d'alertes suivies |
| `/v1/scheduler/trigger` | `POST` | JSON | Déclenchement immédiat d'un cycle de collecte, calcul différentiel et push WebSocket |
| `/v1/zones` | `GET`/`POST`/`DELETE` | JSON | Gestion dynamique et persistante (SQLite WAL) de la Watchlist des zones mondiales surveillées |
| `/v1/history` | `GET` | JSON | Historique SQLite WAL des catastrophes mondiales dédupliquées (`?category=...&min_severity=...&limit=50`) |
| `/v1/point` | `GET` | JSON Compact (`< 6 Ko`) | **Mode GPS Mobile / Bateau / M5Stack Tab5 (GPS Grove)** : météo hyper-locale (`lat, lon`), pluie 15 min, radar, conditions marines (`marine=true`), crues fluviales GloFAS (`flood_risk`), qualité de l'air / poussières (`air_quality`), météo spatiale et alertes dans un rayon `radius_km` |
| `/v1/snapshot` | `GET` | JSON Compact (`< 12 Ko`) | **Mode Multi-Écrans (`waveshare-7b` 1024×600, `tab5` 1280×720, `marine`, `web`)** : statut global DEFCON + météo multi-zones + alertes mondiales dédupliquées |
| `/v1/embedded/frame` | `GET` | **Texte Pipe-Delimited + CRC32 (`< 8 Ko`)** | **Trame C++ sans `malloc`** (`HEAD\|...`, `ZONE\|...`, `RAIN5M\|...`, `DAY\|...`, `HAZ\|...`, `END\|<CRC32>`) alignée à 100 % avec `vigie_decoder_trame_api` |
| `/v1/geojson/hazards` | `GET` | **IETF RFC 7946 GeoJSON** | `FeatureCollection` standard pour cartes Web (MapLibre, Leaflet, Mapbox) et SIG (QGIS) |
| `/v1/cap/alerts` | `GET` | **OASIS CAP 1.2 JSON** | Alertes normalisées selon le *Common Alerting Protocol 1.2* (`urgency`, `severity`, `certainty`, `area`) |
| `/v1/simulate` | `POST` | JSON | Injection de scénarios de crise réalistes (Séisme M7.8 + Tsunami, Ouragan Cat 5, Volcan + Cendres, Grain en mer) |
| `/v1/stream` | `WS` | WebSocket JSON | Flux temps réel (`alert_push` avec `embedded_frame`, `simulation_update`, `initial_snapshot`) |

---

## 2. Spécification Exacte de la Trame C++ (`GET /v1/embedded/frame` ↔ `vigie_decoder_trame_api`)

Pour les firmwares C++ ESP32-P4 (`alerte_mondiale_7b` sur Waveshare 7B et M5Stack Tab5), l'endpoint `GET /v1/embedded/frame?device_type=waveshare-7b|tab5&lat=48.3904&lon=-4.4861&marine=true` retourne une trame `text/plain; charset=utf-8` conçue pour peupler **100 % des champs de `VigieEtatMondial`** en un seul passage sans `malloc` :

```text
HEAD|1792334800|6.3|0|1.1|waveshare-7b|RED|2|1|0|1|1|1
ZONE|GPS|Position GPS / Bateau|48.3904|-4.4861|0|19.4|18.8|66|1015.2|24.0|38.5|250|61|3.0|20|15|3.2|0.00,0.30,0.80,0.20|1.20|8.5|260|1.00|0.59
RAIN5M|GPS|0.00,0.40,0.80,1.20,1.87,2.53,3.20,2.40,1.60,0.80,0.75,0.69
DAY|GPS|Dim|61|12.0|19.5|3.2|45|34.0|3.0
ZONE|z0|Paris, FR|48.8566|2.3522|3600|19.4|18.8|66|1015.2|24.0|38.5|250|61|3.5|22|15|3.2|0.00,0.30,0.80,0.20|0.00|0.0|0|0.00|0.00
RAIN5M|z0|0.00,0.40,0.80,1.20,1.87,2.53,3.20,2.40,1.60,0.80,0.75,0.69
DAY|z0|Dim|61|12.0|19.5|3.2|45|34.0|3.5
HAZ|sim_eq_sendai_m78|EARTHQUAKE|RED|38.1000|142.8500|7.8|24.0|1|1792334800|USGS+EMSC+GDACS|M 7.8 - Fosse du Japon au large de Sendai (SIMULATION)
END|A1B2C3D4
```

### Colonnes exactes de chaque type de ligne :
1. **`HEAD` (13 colonnes)** :
   `HEAD|<server_utc>|<solar_kp:.1f>|0|<version>|<device_type>|<max_severity>|<red_count>|<orange_count>|<yellow_count>|<gps_degraded_0_1>|<hf_risk_0_1>|<sim_0_1>`
2. **`ZONE` (24 colonnes)** — Si `lat`/`lon` sont fournis à la requête, la première zone émise a `id="GPS"` et `nom="Position GPS / Bateau"` :
   `ZONE|<id>|<nom>|<lat:.4f>|<lon:.4f>|<utc_offset_s>|<temp_c:.1f>|<feels_like_c:.1f>|<humidity_pct>|<pressure_hpa:.1f>|<wind_kmh:.1f>|<gust_kmh:.1f>|<wind_deg>|<weather_code>|<uv_index:.1f>|<aqi_eu>|<pluie_dans_min>|<intensite_max_mmh:.1f>|<c15_0,c15_1,c15_2,c15_3>|<wave_m:.2f>|<wave_period_s:.1f>|<wave_dir_deg>|<swell_m:.2f>|<current_knots:.2f>`
3. **`RAIN5M` (3 colonnes dont 12 valeurs CSV)** :
   `RAIN5M|<id>|<v0,v1,v2,v3,v4,v5,v6,v7,v8,v9,v10,v11>` (précipitations interpolées toutes les 5 minutes sur 60 min).
4. **`DAY` (10 colonnes)** :
   `DAY|<id>|<d>|<code_meteo>|<tmin:.1f>|<tmax:.1f>|<rain_mm:.1f>|<rain_prob_pct>|<wind_max:.1f>|<uv_max:.1f>`
5. **`HAZ` (12 colonnes)** :
   `HAZ|<id>|<category>|<severity>|<lat:.4f>|<lon:.4f>|<value_primary:.1f>|<depth_km:.1f>|<tsunami_0_1>|<timestamp_utc>|<sources_plus>|<title>`
6. **`END` (2 colonnes)** :
   `END|<CRC32_HEX8>` — Somme de contrôle **CRC32 IEEE 802.3** calculée sur tous les octets qui précèdent `END|` (incluant le `\n` précédent).
