# Catalogue des Sources d'APIs Mondiales Officielles

`maj: 2026-10-18` · Sources publiques institutionnelles gratuites utilisées par `alerte_mondiale_api`.

---

## 1. Tremblements de Terre (Sismologie Mondiale)

### A. USGS (United States Geological Survey) — *Temps réel mondial*
- **Couverture** : Monde entier (tous les séismes M2.5+ ou M4.5+ ou significatifs).
- **Endpoint GeoJSON Temps Réel (Dernières 24h, M4.5+)** :
  `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson`
- **Endpoint Événements Significatifs / Alertes PAGER (Vert/Jaune/Orange/Rouge)** :
  `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_month.geojson`
- **Données clés extraites** : `mag` (magnitude), `place`, `time` (epoch ms), `tsunami` (flag `1` si risque tsunami !), `alert` (`green`, `yellow`, `orange`, `red`), ` coordinates` (`[lon, lat, depth_km]`).
- **Fréquence de polling recommandée** : `60 s` (sans clé API, 100 % gratuit).

### B. EMSC / SeismicPortal (Centre Sismologique Euro-Méditerranéen)
- **WebSocket Temps Réel (Push instantané mondial)** :
  `wss://www.seismicportal.eu/standing_order/websocket`
- **Intérêt** : Détection ultra-rapide en Europe, Méditerranée, Asie et monde entier dès les premières secondes.

---

## 2. Tsunamis, Cyclones, Volcans, Inondations (Multi-Catastrophes ONU / UE)

### GDACS (Global Disaster Alert and Coordination System)
- **Couverture** : Monde entier (ONU + Centre Commun de Recherche de la Commission Européenne).
- **Endpoint RSS / GeoJSON des événements actifs** :
  `https://www.gdacs.org/gdacsapi/api/events/geteventlist/MAP`
- **Types d'événements normalisés par GDACS** :
  - `EQ` : Earthquake (Séisme)
  - `TC` : Tropical Cyclone (Ouragan / Typhon / Cyclone)
  - `TS` : Tsunami
  - `VO` : Volcano (Éruption volcanique)
  - `FL` : Flood (Inondation majeure)
  - `WF` : Wildfire (Feu de forêt majeur)
  - `DR` : Drought (Sécheresse sévère)
- **Données clés** : Niveau d'alerte déjà calculé (`Green`, `Orange`, `Red`), population exposée, pays, coordonnées, trajectoire des cyclones.
- **Fréquence de polling recommandée** : `180 s` (100 % gratuit).

---

## 3. Alertes Météo Sévères, Tornades, Ouragans & Tsunamis (NOAA / NWS)

### A. NOAA National Weather Service (CAP / GeoJSON)
- **Alertes actives (Tornades, Ouragans, Crues éclair, Tsunami Warnings)** :
  `https://api.weather.gov/alerts/active`
- **En-tête requis** : `User-Agent: (VigieMondialePro, contact@auxfilsdesidees.fr)` (gratuit).

### B. NOAA Space Weather Prediction Center (Météo Spatiale / Tempêtes Géomagnétiques)
- **Indice Kp Planétaire Temps Réel** :
  `https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json`
- **Alertes Éruptions Solaires / Blackout Radio / GPS** :
  `https://services.swpc.noaa.gov/products/alerts.json`

---

## 4. Météo Mondiale Multi-Zones, Pluie & Prévisions 10 Jours

### Open-Meteo API (Agrégateur Modèles Nationaux : NOAA GFS, ECMWF, Météo-France ARPEGE/AROME, DWD ICON, JMA)
- **Endpoint Forecast Multi-Points** :
  `https://api.open-meteo.com/v1/forecast`
- **Capacité clé** : Accepte une liste de latitudes/longitudes en une seule requête HTTP (`latitude=48.85,35.68,40.71&longitude=2.35,139.69,-74.00`), retournant instantanément :
  - Météo actuelle (`temperature_2m`, `weather_code`, `wind_speed_10m`, `wind_gusts_10m`, `surface_pressure`, `precipitation`).
  - Précipitations toutes les 15 minutes / par heure (`minutely_15`, `hourly`).
  - Prévisions à 10–14 jours (`daily`) + fuseau horaire exact (`utc_offset_seconds`).
- **Qualité de l'Air & UV mondial** :
  `https://air-quality-api.open-meteo.com/v1/air-quality`
