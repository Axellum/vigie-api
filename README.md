# Vigie Mondiale API (`alerte_mondiale_api`) — Backend Multi-Providers Météo GPS/Marine & Catastrophes Mondiales

> **Statut :** Socle Multi-Providers, Standards (RFC 7946 GeoJSON / OASIS CAP 1.2) & Géodésie Marine (`2026-10-18`)
> **Stack Technique :** Python 3.12+ · FastAPI (OpenAPI 3.1) · `asyncio` · `httpx` · Pydantic v2 · WebSockets (`WS`).
> **Client Matériel Principal :** Terminal 7" Waveshare ESP32-P4 ([`alerte_mondiale_7b`](../alerte_mondiale_7b/README.md)) + Bateau/Véhicule GPS + Clients SIG / CAP 1.2.

---

## 🎯 Rôle du Dépôt & Architecture Multi-Providers

Un écran embarqué ESP32-P4 (fixe ou mobile en mer/véhicule) ou un centre de crise ne doit **jamais** interroger directement une dizaine d'agences scientifiques internationales sans couche de fusion et de résilience :
1. **Fusion & Déduplication Spatiotemporelle (`USGS + EMSC + GDACS`)** : Fusionne les doublons d'un même séisme (`|Δt| <= 120s`, distance `< 100 km`) en conservant la sévérité maximale, le flag tsunami et la liste des sources confirmantes (`sources: ["USGS", "EMSC", "GDACS"]`).
2. **Filet de Sécurité Météo (Circuit Breaker + Fallback 100 % Gratuit)** : Météo hyper-locale aux coordonnées GPS exactes (`lat, lon` au centième de degré) via **Open-Meteo HD** (AROME 1.5 km, HRRR 3 km, IFS + pluie `minutely_15`), avec bascule automatique transparente vers **MET Norway (`api.met.no`)** en cas d'indisponibilité + tuiles radar **RainViewer**.
3. **Mode Bateau / Marine & Météo Spatiale** : Conditions marines (**Open-Meteo Marine** : vagues, houle, période, courants), météo spatiale (**NOAA SWPC** : indice Kp, tempêtes G1–G5, risque GPS/HF) et géodésie marine (distance Haversine, cap exact en degrés + cardinal `N/NE/E...`, et **ETA Tsunami en minutes**).
4. **Standards Internationaux & Profil Embarqué `< 12 Ko`** :
   - `GET /v1/snapshot` & `GET /v1/point` : Format ultra-compact `< 12 Ko` décodable sans allocation dynamique sur ESP32-P4.
   - `GET /v1/geojson/hazards` : Standard **IETF RFC 7946 GeoJSON** pour MapLibre / Leaflet / QGIS.
   - `GET /v1/cap/alerts` : Standard **OASIS CAP 1.2** (*Common Alerting Protocol*) pour systèmes institutionnels.

---

## 📂 Structure du Dépôt

| Dossier / Fichier | Rôle |
| :--- | :--- |
| [`docs/audits/AUDIT_CONCEPTION_API_MONDIALE.md`](docs/audits/AUDIT_CONCEPTION_API_MONDIALE.md) | Audit d'architecture multi-providers, fusion, fallback et standards internationaux |
| [`docs/travail/INDEX.md`](docs/travail/INDEX.md) | Point d'entrée du kit documentaire de l'API |
| `app/models/schemas.py` | Modèles Pydantic v2 (Snapshot, Point GPS/Marine, GeoJSON RFC 7946, OASIS CAP 1.2, ProviderHealth) |
| `app/services/geodesy.py` | Géodésie (Haversine, relèvement initial + cardinal, ETA Tsunami, déduplication multi-sources) |
| `app/collectors/base.py` | Classe de base asynchrone avec Circuit Breaker (`CLOSED`/`OPEN`/`HALF_OPEN`) et cache TTL |
| `app/collectors/seismic.py` | Collecteurs sismiques `USGSCollector` et `EMSCCollector` |
| `app/collectors/disasters.py` | Collecteurs multirisques `GDACSCollector` et `NOAANWSCollector` |
| `app/collectors/weather.py` | `OpenMeteoCollector` (`minutely_15`), `MetNorwayCollector` (Fallback) et `RainViewerCollector` |
| `app/collectors/marine.py` | `OpenMeteoMarineCollector` (vagues, houle, courants aux coordonnées GPS) |
| `app/collectors/space_weather.py` | `NOAASWPCCollector` (indice Kp géomagnétique, tempêtes solaires G1–G5) |
| `app/services/aggregator.py` | Orchestrateur multi-providers, geofencing, fallback météo et simulateur de crises |
| `app/main.py` | Application FastAPI (`/v1/health`, `/v1/providers`, `/v1/point`, `/v1/snapshot`, `/v1/geojson/hazards`, `/v1/cap/alerts`, `/v1/simulate`, `WS /v1/stream`) |
| `tests/test_api_and_fusion.py` | Suite de tests unitaires et d'intégration (`pytest`) |
