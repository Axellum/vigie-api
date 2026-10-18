# Catalogue des Sources d'APIs Mondiales Officielles

`maj: 2026-10-18` · Sources publiques institutionnelles gratuites utilisées par `alerte_mondiale_api` avec stratégie de fusion et fallback.

---

## 1. Tremblements de Terre (Sismologie Mondiale — Fusion Tri-Sources)

### A. USGS (United States Geological Survey) — *Primaire Mondial & PAGER*
- **Couverture** : Monde entier (tous les séismes M4.5+ ou significatifs).
- **Endpoint GeoJSON Temps Réel (Dernières 24h, M4.5+)** :
  `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson`
- **Endpoint Événements Significatifs / Alertes PAGER** :
  `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_month.geojson`
- **Données clés extraites** : `mag` (magnitude), `place`, `time` (epoch ms), `tsunami` (flag `1` si risque tsunami océanique), `alert` (`green`, `yellow`, `orange`, `red`), `coordinates` (`[lon, lat, depth_km]`).
- **Fréquence de polling / TTL** : `60 s` (sans clé API, 100 % gratuit).

### B. EMSC / SeismicPortal (Centre Sismologique Euro-Méditerranéen) — *Co-Primaire Rapide Europe/Asie/Monde*
- **Endpoint REST GeoJSON Temps Réel** :
  `https://www.seismicportal.eu/fdsnws/event/1/query?limit=50&minmag=4.5&format=json`
- **WebSocket Temps Réel** :
  `wss://www.seismicportal.eu/standing_order/websocket`
- **Intérêt** : Détection ultra-rapide en Europe, Méditerranée, Moyen-Orient, Asie et monde entier dès les premières secondes. Fusionné avec USGS via `deduplicate_hazards` (`|Δt| <= 120s`, distance Haversine `<= 100 km`).

---

## 2. Tsunamis, Cyclones, Volcans, Inondations, Feux (Multi-Catastrophes ONU / UE)

### GDACS (Global Disaster Alert and Coordination System)
- **Couverture** : Monde entier (ONU + Centre Commun de Recherche de la Commission Européenne).
- **Endpoint GeoJSON des événements actifs** :
  `https://www.gdacs.org/gdacsapi/api/events/geteventlist/MAP`
- **Types d'événements normalisés par GDACS** :
  - `EQ` : Earthquake (Séisme — fusionné avec USGS/EMSC)
  - `TC` : Tropical Cyclone (Ouragan / Typhon / Cyclone)
  - `TS` : Tsunami
  - `VO` : Volcano (Éruption volcanique)
  - `FL` : Flood (Inondation majeure)
  - `WF` : Wildfire (Feu de forêt majeur)
- **Données clés** : Niveau d'alerte (`Green`, `Orange`, `Red`), sévérité numérique, pays, coordonnées `[lon, lat]`.
- **Fréquence de polling / TTL** : `180 s` (100 % gratuit).

---

## 3. Alertes Météo Sévères, Tornades, Ouragans & Tsunamis (NOAA / NWS)

### A. NOAA National Weather Service (CAP / GeoJSON)
- **Alertes actives (Tornades, Ouragans, Crues éclair, Tsunami Warnings, Coups de vent marins)** :
  `https://api.weather.gov/alerts/active?status=actual`
- **En-tête requis** : `User-Agent: (VigieMondialePro, contact@auxfilsdesidees.fr)` (gratuit).

### B. NOAA Space Weather Prediction Center (Météo Spatiale / Tempêtes Géomagnétiques)
- **Indice Kp Planétaire Temps Réel** :
  `https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json`
- **Usage critique** : Détection des tempêtes géomagnétiques `G1` (`Kp >= 5`) à `G5` (`Kp >= 9`) provoquant la dégradation de la précision GPS/GNSS et les blackouts radio HF en mer.

---

## 4. Météo Hyper-Locale par Coordonnées GPS & Pluie (Primaire + Fallback + Radar)

### A. Open-Meteo Forecast & Minutely-15 (Provider Primaire Haute Résolution)
- **Endpoint Forecast** :
  `https://api.open-meteo.com/v1/forecast`
- **Modèles combinés automatiquement (`best_match`)** : Météo-France AROME ($1.5\text{ km}$), NOAA HRRR ($3\text{ km}$), DWD ICON-D2 ($2\text{ km}$), ECMWF IFS, JMA MSM.
- **Données extraites aux coordonnées `lat, lon` exactes** :
  - Météo actuelle (`temperature_2m`, `apparent_temperature`, `relative_humidity_2m`, `surface_pressure`, `wind_speed_10m`, `wind_gusts_10m`, `wind_direction_10m`, `precipitation`, `weather_code`, `uv_index`).
  - Précipitations hyper-locales aux 15 minutes (`minutely_15.precipitation`) pour l'alerte pluie dans l'heure (`rain_next_60m`).
  - Prévisions quotidiennes à 10 jours (`daily`).

### B. MET Norway Locationforecast 2.0 (Provider Secondaire / Filet de Sécurité 100 % Gratuit Mondial)
- **Institution** : Norwegian Meteorological Institute (NRK / MET Norway).
- **Endpoint Compact JSON** :
  `https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={lat}&lon={lon}`
- **En-tête requis** : `User-Agent: VigieMondialePro/1.0 contact@auxfilsdesidees.fr`
- **Rôle** : Bascule automatique instantanée si Open-Meteo est en timeout ou indisponible, garantissant 100 % de disponibilité météo sur terre comme en mer dans le monde entier.

### C. RainViewer API (Radar de Pluie Mondial & Nowcast)
- **Endpoint Cartes Radar** :
  `https://api.rainviewer.com/public/weather-maps.json`
- **Données fournies** : Timestamps radar passés (2h) et prévisions radar nowcast (+30 min) avec modèle d'URL de tuiles slippy map (`{host}{path}/256/{z}/{x}/{y}/2/1_1.png`).

---

## 5. Météo Marine & Océanographie (Mode Bateau / Navigation GPS)

### Open-Meteo Marine API
- **Endpoint Marine** :
  `https://marine-api.open-meteo.com/v1/marine`
- **Paramètres extraits aux coordonnées GPS du navire** :
  - `wave_height`, `wave_direction`, `wave_period` (mer totale).
  - `swell_wave_height`, `swell_wave_direction`, `swell_wave_period` (houle primaire).
  - `ocean_current_velocity`, `ocean_current_direction` (courant de surface).
