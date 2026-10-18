# Historique de Développement — Vigie Mondiale API (`alerte_mondiale_api`)

`maj: 2026-10-18` · Journal chronologique (le plus récent en haut).

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
- **Tests Automatisés (`tests/test_api_and_fusion.py`)** : Couverture unitaire et d'intégration complète (géodésie, déduplication tri-providers, fallback météo automatique, standards et contrat `< 12 Ko`).

## 📅 2026-10-18 — Création du dépôt & Architecture du Backend Mondial
- Décision d'isoler l'agrégation des APIs météo et catastrophes naturelles dans un dépôt serveur indépendant du firmware de l'écran Waveshare 7B (`alerte_mondiale_7b`).
- Création de l'arborescence (`app/collectors`, `app/models`, `app/routers`, `app/services`, `docs/travail`, `tests`).
- Rédaction du catalogue des APIs institutionnelles gratuites ([`SOURCES_API_MONDIALES.md`](SOURCES_API_MONDIALES.md)) et du contrat d'échange JSON/WebSocket ([`CONTRAT_PAYLOAD.md`](CONTRAT_PAYLOAD.md)).
