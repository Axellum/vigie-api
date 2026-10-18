# Objectifs — Vigie Mondiale API (`alerte_mondiale_api`)

`maj: 2026-10-18` · Un objectif = une preuve mesurable.

## OA-1 — Agrégation Mondiale Multi-Risques 100 % Automatisée
**actif** · depuis 2026-10-18
Collecter en tâche de fond asynchrone (`asyncio`) les flux temps réel des agences de référence mondiale :
1. **Séismes** : USGS GeoJSON Feed + EMSC WebSocket (temps réel à la seconde).
2. **Catastrophes Majeures (Volcans, Tsunamis, Cyclones, Inondations)** : GDACS (ONU / Commission Européenne).
3. **Alertes Marines, Ouragans, Tornades & Tsunamis** : NOAA / NWS / PTWC (CAP / JSON).
4. **Météo Mondiale, Pluie à 1h, Vent & Prévisions 10j** : Open-Meteo (modèles GFS, ECMWF, ICON, ARPEGE).
5. **Feux de Forêts & Tempêtes Solaires** : NASA FIRMS + NOAA SWPC (Indice Kp géomagnétique).
* **Preuve attendue** : `/v1/health` retourne l'état de fraîcheur de chaque collecteur avec 0 blocage si une agence externe est en maintenance.

## OA-2 — Normalisation & Payload Ultra-Léger pour ESP32-P4
**actif** · depuis 2026-10-18
Transformer les Mo de données brutes en un snapshot complet `< 12 Ko` (compatible avec les tampons statiques C++ du socle Waveshare 7B sans fragmentation mémoire).
* **Preuve attendue** : Temps de sérialisation `< 5 ms`, taille du payload vérifiée par test automatique `< 12 Ko` pour 10 zones mondiales + 25 alertes actives.

## OA-3 — Push Temps Réel (`< 2 s`) & Geofencing Pro
**actif** · depuis 2026-10-18
Dès qu'un événement critique survient (ex. Séisme `M >= 6.0`, Alerte Tsunami, ou événement `ORANGE/ROUGE` à moins de `X km` d'une zone surveillée), calculer la distance orthodromique (formule de Haversine) et pousser l'alerte via WebSocket (`/v1/stream`) à tous les écrans concernés.
* **Preuve attendue** : Latence entre l'injection d'une alerte test (ex. simulation Séisme M7.2 Tokyo) et la réception sur le client WebSocket `< 500 ms`.

## OA-4 — Simulateur de Crises Mondiales (Mode Démo & Test)
**actif** · depuis 2026-10-18
Fournir un endpoint `/v1/simulate` permettant d'injecter à la demande des scénarios réalistes (Séisme majeur + Tsunami, Éruption volcanique, Ouragan Catégorie 5, Pluie diluvienne locale) afin de tester l'affichage et la sonnerie de l'écran Waveshare 7B et de faire des démonstrations commerciales.
