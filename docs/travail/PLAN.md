# Plan & Historique — Vigie Mondiale API (`alerte_mondiale_api`)

`maj: 2026-10-18` · Plan de développement par lots et historique chronologique.

---

## 1. Découpage en Lots (Feuille de Route Backend)

### Lot A1 — Socle FastAPI, Modèles Pydantic & Simulateur de Crises *(Priorité P0)*
- Mise en place du serveur `app/main.py` (FastAPI + CORS + WebSocket Manager).
- Implémentation des schémas Pydantic conformes à [`CONTRAT_PAYLOAD.md`](CONTRAT_PAYLOAD.md).
- Endpoint `/v1/simulate` permettant de générer immédiatement un snapshot complet et des alertes temps réel simulées pour développer l'écran Waveshare 7B en parallèle sans attendre les vraies catastrophes.

### Lot A2 — Collecteurs Temps Réel Sismologie & Multi-Catastrophes (`USGS` + `GDACS`)
- `app/collectors/usgs.py` : récupération asynchrone des séismes mondiaux (M4.5+ et significatifs) + détection du flag `tsunami`.
- `app/collectors/gdacs.py` : récupération des alertes ONU/UE (Volcans, Cyclones/Ouragans, Tsunamis, Inondations, Feux).

### Lot A3 — Collecteur Météo Mondiale Multi-Zones (`Open-Meteo` + `NOAA`)
- `app/collectors/open_meteo.py` : requête groupée multi-coordonnées pour les 10 zones mondiales configurées (météo actuelle, pluie dans l'heure, prévisions 10 jours, UV, qualité de l'air).
- `app/collectors/noaa_nws.py` & `swpc_solar.py` : alertes tornades/ouragans/tsunamis NOAA et indice géomagnétique solaire Kp.

### Lot A4 — Moteur de Geofencing, Déduplication & Cache SQLite WAL
- Calcul de distance orthodromique (Haversine) entre chaque alerte mondiale et les zones surveillées par l'écran.
- Déduplication inter-sources (ex. un même séisme remonté par USGS et GDACS est fusionné en une seule alerte enrichie).
- Historisation locale SQLite WAL pour l'analyse de tendances et le rejeu.

### Lot A5 — Diffusion Temps Réel (`WSS /v1/stream`) & Conteneurisation Pro
- Push WebSocket instantané vers les écrans Waveshare 7B connectés dès qu'une nouvelle alerte `JAUNE`, `ORANGE` ou `ROUGE` apparaît.
- Packaging Docker / Service autonome prêt pour un déploiement serveur ou Cloud mondial.

---

## 2. Historique de Développement

### 📅 2026-10-18 — Initialisation du dépôt `alerte_mondiale_api`
- Création du dépôt autonome dédié au backend d'agrégation des alertes et de la météo mondiale.
- Spécification des sources officielles gratuites (`USGS`, `EMSC`, `GDACS`, `NOAA/NWS/PTWC`, `Open-Meteo`, `NASA FIRMS`, `NOAA SWPC`).
- Définition du contrat de payload unifié (`CONTRAT_PAYLOAD.md`) pensé pour le décodage haute performance sur l'ESP32-P4 de la Waveshare 7B.
