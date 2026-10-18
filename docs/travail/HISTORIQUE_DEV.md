# Historique de Développement — Vigie Mondiale API (`alerte_mondiale_api`)

`maj: 2026-10-18` · Journal chronologique (le plus récent en haut).

## 📅 2026-10-18 — Vague 2 : Persistance SQLite WAL, Crues GloFAS, Air/Cendres, Volcans USGS & Trame C++ (`/v1/embedded/frame`)
- **Nouveaux Collecteurs Mondiaux (`app/collectors/flood_air.py`)** :
  - `OpenMeteoFloodCollector` : débits fluviaux et détection de crues via le modèle mondial Copernicus GloFAS (`river_discharge`, `river_discharge_mean`, `river_discharge_max`, `anomaly_ratio`).
  - `OpenMeteoAirQualityCollector` : indices `european_aqi`, `us_aqi`, `pm2_5`, `pm10`, `uv_index` et détection de panaches de poussières désertiques ou cendres volcaniques (`dust_ugm3`).
  - `USGSVolcanoCollector` : surveillance dédiée des volcans actifs avec normalisation des codes couleur d'aviation OACI (`GREEN`, `YELLOW`, `ORANGE`, `RED`) et du VEI.
- **Persistance SQLite WAL (`app/services/storage_wal.py`)** :
  - Base SQLite configurée en `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`.
  - Gestion dynamique de la Watchlist des zones mondiales surveillées (`GET /v1/zones`, `POST /v1/zones`, `DELETE /v1/zones/{zone_id}`).
  - Historisation dédupliquée des catastrophes (`hazards_history`) exposée sur `GET /v1/history?category=...&min_severity=...&limit=50`.
  - Cache persistant du dernier snapshot valide (`snapshot_cache`) pour démarrage à froid instantané.
- **Générateur de Trame Compacte Embarquée (`app/services/embedded_frame.py`)** :
  - Exposition de `GET /v1/embedded/frame` générant une trame texte délimitée par `|` (`HEAD|...`, `GPS|...`, `ZONE|...`, `HAZ|...`, `END|<CRC32>`) parsable en C++ en un seul passage sans `malloc` sur l'ESP32-P4 (Waveshare 7B et M5Stack Tab5).
- **Tests Automatisés (`tests/test_storage_flood_and_frame.py`)** : `15 passed` sur l'ensemble de la suite (`pytest`).

## 📅 2026-10-18 — Implémentation du Socle Multi-Providers, Géodésie Marine & Standards (GeoJSON / CAP 1.2)
- **Audit & Conception** : Rédaction de [`docs/audits/AUDIT_CONCEPTION_API_MONDIALE.md`](../audits/AUDIT_CONCEPTION_API_MONDIALE.md) détaillant l'architecture multi-providers, la déduplication spatiotemporelle et le mécanisme de Circuit Breaker + Fallback.
- **Moteur Géodésique (`app/services/geodesy.py`)** : Implémentation de `haversine_km`, `initial_bearing_deg` + point cardinal (`N`, `NE`, `E`...), `estimate_tsunami_eta_minutes` ($\approx 713\text{ km/h}$ en océan profond) et `deduplicate_hazards` (fusion multi-agences `USGS + EMSC + GDACS`).
- **Collecteurs Asynchrones Résilients (`app/collectors/`)** :
  - `base.py` : Circuit Breaker à 3 états (`CLOSED`, `OPEN`, `HALF_OPEN`), cache mémoire TTL et télémétrie de santé.
  - `seismic.py` : `USGSCollector` et `EMSCCollector`.
  - `disasters.py` : `GDACSCollector` et `NOAANWSCollector`.
  - `weather.py` : `OpenMeteoCollector` (avec pluie `minutely_15`), `MetNorwayCollector` (filet de sécurité 100 % gratuit mondial) et `RainViewerCollector` (tuiles radar de pluie).
  - `marine.py` : `OpenMeteoMarineCollector` (vagues, houle, période, courants marins sur coordonnées GPS).
  - `space_weather.py` : `NOAASWPCCollector` (indice Kp et alertes géomagnétiques G1–G5).
- **API FastAPI (`app/main.py`) & Orchestrateur (`app/services/aggregator.py`)** : Exposition de `/v1/health`, `/v1/providers`, `/v1/point`, `/v1/snapshot` (`< 12 Ko`), `/v1/geojson/hazards` (RFC 7946), `/v1/cap/alerts` (OASIS CAP 1.2), `/v1/simulate` et `WS /v1/stream`.

## 📅 2026-10-18 — Création du dépôt & Architecture du Backend Mondial
- Décision d'isoler l'agrégation des APIs météo et catastrophes naturelles dans un dépôt serveur indépendant du firmware de l'écran Waveshare 7B (`alerte_mondiale_7b`).
- Création de l'arborescence (`app/collectors`, `app/models`, `app/routers`, `app/services`, `docs/travail`, `tests`).
- Rédaction du catalogue des APIs institutionnelles gratuites ([`SOURCES_API_MONDIALES.md`](SOURCES_API_MONDIALES.md)) et du contrat d'échange JSON/WebSocket ([`CONTRAT_PAYLOAD.md`](CONTRAT_PAYLOAD.md)).
