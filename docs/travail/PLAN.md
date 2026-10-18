# Plan & Architecture — Vigie Mondiale API (`alerte_mondiale_api`)

`maj: 2026-10-18` · Plan de développement par lots et architecture modulaire.

---

## 1. Découpage en Lots (Feuille de Route Backend)

### Lot A1 — Socle FastAPI, Modèles Standards (Compact, GeoJSON, CAP 1.2) & Géodésie *(Priorité P0 — Livré)*
- Mise en place du serveur `app/main.py` (FastAPI OpenAPI 3.1 + CORS + WebSocket Hub).
- Implémentation des schémas Pydantic dans `app/models/schemas.py` conformes à [`CONTRAT_PAYLOAD.md`](CONTRAT_PAYLOAD.md) :
  - Profil Embarqué Multi-Écrans (`SnapshotResponse`, `PointResponse`, `< 12 Ko`).
  - Standard IETF RFC 7946 (`GeoJSONFeatureCollection`).
  - Standard OASIS CAP 1.2 (`CAPFeedResponse`, `CAPAlert`, `CAPInfo`, `CAPArea`).
- Implémentation du moteur géodésique `app/services/geodesy.py` (`haversine_km`, `initial_bearing_deg`, `bearing_to_cardinal`, `estimate_tsunami_eta_minutes`, `deduplicate_hazards`).

### Lot A2 — Collecteurs Sismologie, Volcans & Catastrophes avec Circuit Breaker *(Priorité P0 — Livré)*
- `app/collectors/base.py` : Classe de base `BaseCollector` avec Circuit Breaker (`CLOSED`, `OPEN`, `HALF_OPEN`), cache mémoire TTL et métriques de santé (`ProviderHealth`).
- `app/collectors/seismic.py` : `USGSCollector` (GeoJSON M4.5+ / Significant + PAGER + flag Tsunami) et `EMSCCollector` (SeismicPortal REST API Europe/Monde).
- `app/collectors/disasters.py` : `GDACSCollector` (`EQ`, `TS`, `VO`, `TC`, `FL`, `WF`) et `NOAANWSCollector` (Alertes actives CAP/GeoJSON : Tornades, Ouragans, Tsunamis, Crues éclair).
- `app/collectors/flood_air.py` : `USGSVolcanoCollector` (codes aviation OACI `GREEN`/`YELLOW`/`ORANGE`/`RED` + VEI) et `OpenMeteoFloodCollector` (débits fluviaux et crues Copernicus GloFAS).

### Lot A3 — Collecteurs Météo GPS Hyper-Locale, Fallback, Radar Pluie, Air/Cendres, Marine & Solaire *(Priorité P0 — Livré)*
- `app/collectors/weather.py` :
  - `OpenMeteoCollector` : prévisions multi-modèles HD (AROME, HRRR, IFS, ICON-D2) + pluie aux 15 minutes (`minutely_15`).
  - `MetNorwayCollector` : filet de sécurité (fallback automatique 100 % gratuit mondial `api.met.no`).
  - `RainViewerCollector` : métadonnées et tuiles radar de pluie mondiales + nowcast.
- `app/collectors/flood_air.py` : `OpenMeteoAirQualityCollector` (`european_aqi`, `us_aqi`, `pm2_5`, `pm10`, `uv_index`, `dust` pour panaches de poussières/cendres).
- `app/collectors/marine.py` : `OpenMeteoMarineCollector` (hauteur/direction/période des vagues, houle primaire, vitesse des courants marins aux coordonnées GPS exactes).
- `app/collectors/space_weather.py` : `NOAASWPCCollector` (indice Kp planétaire, tempêtes géomagnétiques G1–G5, dégradation GPS/HF).

### Lot A4 — Orchestrateur Multi-Providers, Persistance SQLite WAL & Trame Embarquée C++ *(Priorité P0 — Livré)*
- `app/services/storage_wal.py` : Base SQLite en mode `WAL` gérant la Watchlist dynamique (`/v1/zones`), l'historique dédupliqué des catastrophes (`/v1/history`) et le cache de snapshot à froid (`snapshot_cache`).
- `app/services/embedded_frame.py` : Générateur de trame texte ultra-compacte (`GET /v1/embedded/frame` : `HEAD|...`, `GPS|...`, `ZONE|...`, `HAZ|...`, `END|<CRC32>`) décodable en C++ sans `malloc`.
- `app/services/aggregator.py` : Orchestration asynchrone des 12 collecteurs, déduplication inter-agences, bascule météo automatique `Open-Meteo -> MET Norway` et injecteur de scénarios `/v1/simulate`.

### Lot A5 — Diffusion Temps Réel (`WS /v1/stream`) & Conteneurisation Cloud *(Priorité P1)*
- Push WebSocket instantané vers les écrans Waveshare 7B, M5Stack Tab5 et clients Web connectés.
- Packaging Docker / Service systemd pour déploiement serveur 24/7.
