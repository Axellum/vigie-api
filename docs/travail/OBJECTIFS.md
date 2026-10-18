# Objectifs — Vigie Mondiale API (`alerte_mondiale_api`)

`maj: 2026-10-18` · Un objectif = une preuve mesurable.

## OA-1 — Agrégation Mondiale Multi-Providers & Filet de Sécurité (Circuit Breaker + Fallback)
**actif** · depuis 2026-10-18
Collecter en tâche de fond asynchrone (`asyncio`) et fusionner les flux temps réel des agences de référence mondiale avec continuité de service garantie :
1. **Séismes (Tri-Providers avec Déduplication)** : `USGS` (GeoJSON) + `EMSC / SeismicPortal` (API REST Europe/Monde) + `GDACS EQ`. Déduplication spatiotemporelle (`|Δt| <= 120s`, distance Haversine `<= 100 km`) et fusion du meilleur score (`sources: ["USGS", "EMSC", "GDACS"]`).
2. **Catastrophes Majeures (Volcans, Tsunamis, Cyclones/Ouragans, Tornades, Inondations, Feux)** : `GDACS` (`EQ`, `TS`, `VO`, `TC`, `FL`, `WF`) + `NOAA NWS Active Alerts` (CAP/JSON : Tornado, Hurricane, Tsunami, Flash Flood).
3. **Météo Hyper-Locale par Coordonnées GPS (`lat`, `lon` au centième de degré) & Pluie 15 min** :
   - Primaire : `Open-Meteo Forecast & Minutely-15` (AROME 1.5 km, HRRR 3 km, ECMWF IFS, ICON-D2, JMA).
   - Fallback 100 % gratuit mondial : `MET Norway Locationforecast 2.0` (`api.met.no`) prenant automatiquement le relais si Open-Meteo est indisponible.
   - Radar Pluie Mondial : `RainViewer API` (horodatages et tuiles radar mondiales + nowcast).
4. **Météo Marine & Océanographie (Mode Bateau / GPS)** : `Open-Meteo Marine API` (`wave_height`, `wave_direction`, `wave_period`, `swell_wave_height`, `ocean_current_velocity`).
5. **Météo Spatiale (GNSS/GPS & Radio HF en mer)** : `NOAA SWPC` (Indice Kp géomagnétique, tempêtes solaires `G1`–`G5`, risque de dégradation GPS/HF).
* **Preuve attendue** : `/v1/health` et `/v1/providers` retournent l'état du Circuit Breaker de chaque collecteur ; en cas de panne simulée d'Open-Meteo, `/v1/point` et `/v1/snapshot` basculent automatiquement sur `MET Norway` avec 0 erreur.

## OA-2 — Standards Internationaux (OpenAPI 3.1, GeoJSON RFC 7946, OASIS CAP 1.2) & Profil Embarqué `< 12 Ko`
**actif** · depuis 2026-10-18
Assurer une interopérabilité mondiale universelle tout en respectant l'enveloppe mémoire stricte de l'ESP32-P4 :
- `/v1/snapshot` et `/v1/point` : JSON ultra-compact `< 12 Ko` (compatible avec les tampons statiques C++ du socle Waveshare 7B).
- `/v1/geojson/hazards` : `FeatureCollection` conforme IETF RFC 7946 pour SIG / MapLibre / Leaflet.
- `/v1/cap/alerts` : Format d'alerte standardisé OASIS CAP 1.2 (`urgency`, `severity`, `certainty`, `category`, `event`, `area`).
* **Preuve attendue** : Validation par tests automatisés (`pytest`) des schémas et de la taille `< 12 Ko` pour 10 zones mondiales + 25 alertes actives.

## OA-3 — Géodésie Marine/Mobile, ETA Tsunami & Push Temps Réel (`< 2 s`)
**actif** · depuis 2026-10-18
Pour chaque zone ou position GPS mobile (`lat, lon, sog_knots, cog_deg`), calculer la distance orthodromique (`haversine_km`), le relèvement exact (`initial_bearing_deg` + cardinal `N`, `NE`, `E`...) et l'estimation du temps d'arrivée d'un tsunami (`estimate_tsunami_eta_minutes` à $\approx 713\text{ km/h}$), puis diffuser les alertes via WebSocket (`/v1/stream`).
* **Preuve attendue** : Vérification mathématique exacte de la géodésie et diffusion WebSocket instantanée lors d'une alerte.

## OA-4 — Simulateur de Crises Mondiales (`/v1/simulate`)
**actif** · depuis 2026-10-18
Fournir un endpoint `POST /v1/simulate` permettant d'injecter à la demande des scénarios réalistes (`mega_tsunami_japan`, `cat5_hurricane_caribbean`, `volcano_eruption_iceland`, `severe_squall_marine`, `clear`) afin de tester l'affichage et la sonnerie de l'écran Waveshare 7B ainsi que les clients SIG/CAP.
