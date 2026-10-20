# Vigie API

*[Read this document in English](README.md)*

Un projet personnel, fait avec l'aide de l'IA et partagé au cas où il servirait à quelqu'un. Il est fourni
tel quel, sans aucune promesse de support, de disponibilité ni d'exactitude.

Il rassemble des dangers naturels dans le monde (séismes, tsunamis, volcans, cyclones, tornades,
inondations, incendies, orages géomagnétiques), de la météo (conditions actuelles, pluie à 15 minutes,
prévisions, mer, débit des rivières, qualité de l'air) et les vigilances officielles des services nationaux
(MeteoAlarm en Europe, le Deutscher Wetterdienst en Allemagne, Environnement et Changement climatique Canada,
MetService en Nouvelle-Zélande, le National Weather Service aux États-Unis, et Vigicrues pour les cours d'eau de
France) à partir de sources publiques, puis les sert :

- aux écrans ESP32-P4 du projet Vigie (Waveshare 7B, M5Stack Tab5), par une trame texte compacte :
  [`docs/embedded-frame.md`](docs/embedded-frame.md) ;
- à tout autre client, en JSON, en GeoJSON (IETF RFC 7946) et en OASIS CAP 1.2.

> **Ce n'est pas un service d'alerte officiel.** Les alertes sont relayées depuis des flux publics. Elles
> peuvent arriver en retard, être incomplètes ou fausses, et ne doivent pas être la seule base d'une
> décision. **Les alertes de GDACS sont automatiques et ne remplacent pas les alertes officielles.**
> Suivez les avertissements des autorités de votre pays.

> **Aucune donnée inventée.** Quand une source ne fournit pas une valeur, l'API la déclare inconnue
> (`null` en JSON, champ vide dans la trame). Elle n'affiche jamais une valeur par défaut qui aurait l'air
> plausible. Voir l'[ADR 0001](docs/decisions/0001-two-repos-no-fabricated-data-frame-format.md)
> (en anglais).

## Licence, données et crédits

Le code est sous [licence MIT](LICENSE). **Les données n'en font pas partie** : elles appartiennent à
chaque fournisseur et restent soumises à ses propres conditions. [`SOURCES.md`](SOURCES.md) (en anglais)
donne, pour chaque source, la licence, les conditions, le crédit demandé, le quota, et ce qui n'a pas pu
être vérifié.

- Chaque bloc de données des réponses JSON porte un champ `attribution` avec le crédit que sa source
  demande (la trame compacte n'a pas ce champ). Les textes sont déclarés dans
  [`app/attribution.py`](app/attribution.py).
- **L'API gratuite d'Open-Meteo ne peut être utilisée qu'à des fins non commerciales**
  ([conditions](https://open-meteo.com/en/terms)), dans la limite de 10 000 appels par jour, avec un lien
  vers Open-Meteo partout où ses données sont affichées. Ce projet est personnel et non commercial.
  **Ne l'utilisez pas à des fins commerciales sans abonnement payant à Open-Meteo**, et vérifiez les
  conditions des autres fournisseurs.
- L'API gratuite de RainViewer est réservée à un usage personnel et pédagogique. Désactivez-la sur une
  instance qui relaie ses données à d'autres personnes (`VIGIE_ENABLE_RAINVIEWER=0` ; l'image Docker le
  fait).
- MetService (Nouvelle-Zélande) publie ses vigilances sous licence CC BY 4.0 et avec un code de bonne pratique, mais les
  conditions du site auxquelles il renvoie n'autorisent qu'un usage personnel et interdisent la rediffusion. Aucun
  souci pour une maison ; demandez à MetService avant de relayer ses vigilances à d'autres personnes, ou désactivez-la
  sur une instance qui en sert (`VIGIE_ENABLE_METSERVICE=0` ; activée par défaut, l'image Docker la garde activée)
  ([`SOURCES.md`](SOURCES.md)).

## Points d'accès

| Point d'accès | Rôle |
| --- | --- |
| `GET /v1/embedded/frame` | Trame texte compacte pour les écrans ([contrat](docs/embedded-frame.md)). |
| `GET /v1/snapshot` | Zones surveillées (météo) et dangers mondiaux, JSON compact. |
| `GET /v1/point?lat=&lon=` | Météo en un point GPS, mer (`marine=true`), débit des rivières, qualité de l'air, météo spatiale, dangers proches. |
| `GET /v1/geojson/hazards` | Dangers actifs en GeoJSON (RFC 7946). |
| `GET /v1/cap/alerts` | Dangers actifs en OASIS CAP 1.2. |
| `GET /v1/vigilances?lat=&lon=` ou `?country=FR` | Vigilances météo officielles qui couvrent un point, ou toutes celles d'un pays (MeteoAlarm, DWD, ECCC, MetService, NWS, Vigicrues), avec les mots de l'émetteur intacts. Voir plus bas. |
| `GET /v1/vigilances.geojson` | Les mêmes vigilances en GeoJSON, la forme de chaque zone en géométrie. |
| `GET/POST /v1/zones`, `DELETE /v1/zones/{id}` | Zones surveillées (la liste de suivi). Une zone doit donner son `utc_offset_s`. |
| `GET /v1/history` | Historique des dangers, stocké dans SQLite. |
| `GET /v1/health`, `GET /v1/providers` | État, disjoncteur et cache de chaque source, avec ses appels, son poids et son quota sur 24 heures. |
| `GET/POST /v1/scheduler/...` | État du planificateur en arrière-plan (facultatif), lancement d'un cycle immédiat. |
| `POST /v1/simulate` | Injecte un scénario **simulé** pour les essais (toujours étiqueté comme simulation). **Désactivé par défaut** : il répond 403 sauf si `VIGIE_ENABLE_SIMULATION=1`. |
| `WS /v1/stream` | WebSocket avec l'état initial et les alertes poussées ; un client peut aussi suivre un point et être prévenu quand ses vigilances officielles changent. |
| `GET /dashboard` | Page web de supervision. |
| `GET /docs` | Documentation OpenAPI interactive. |

**L'API n'a aucune authentification.** Utilisez-la sur un réseau de confiance. Ne l'exposez pas telle quelle
sur Internet : quiconque peut l'atteindre peut ajouter ou supprimer des zones surveillées et déclencher des
cycles de collecte. Si vous devez l'exposer, placez devant elle un proxy inverse qui authentifie.

## Vigilances météo officielles

`GET /v1/vigilances` donne les vigilances que publient les services météo nationaux : **MeteoAlarm** (EUMETNET)
pour 39 pays d'Europe, le **Deutscher Wetterdienst** pour l'Allemagne, **Environnement et Changement climatique
Canada**, **MetService** pour la Nouvelle-Zélande, le **National Weather Service** pour les États-Unis et
**Vigicrues** pour les crues des cours d'eau de France. L'API les relaie, elle n'en émet aucune. Les mots de
l'émetteur (titre, description et consigne, dans chaque langue qu'il a écrite) sont rendus tels quels.

**Une seule source répond pour un pays.** Pour l'Allemagne, c'est le DWD (la copie des vigilances allemandes par
MeteoAlarm n'est pas interrogée, sauf si le DWD ne répond pas) ; le Canada et la Nouvelle-Zélande ont leur propre
service ; les autres pays d'Europe sont ceux de MeteoAlarm. La France en a deux, qui disent deux choses :
MeteoAlarm une couleur par département, Vigicrues une couleur par **tronçon de cours d'eau**. Une vigilance
« inondation » de MeteoAlarm qu'un tronçon Vigicrues de la même réponse dit au même niveau ou plus haut est écartée ;
sinon elle reste ([`docs/data-sources.md`](docs/data-sources.md), en anglais).

```
GET /v1/vigilances?lat=44.9334&lon=4.8924      les vigilances dont la zone contient ce point exact
GET /v1/vigilances?country=FR&lang=fr          toutes les vigilances d'un pays, textes en français seulement
GET /v1/vigilances.geojson?lat=..&lon=..       les mêmes en GeoJSON (la forme de chaque zone est la géométrie)
GET /v1/vigilances?lat=..&lon=..&river_radius_km=10   jusqu'où un tronçon de cours d'eau compte encore (5 par défaut, 50 au plus)
```

Réponse réelle pour un point de la Drôme, abrégée : les flux du 20/10/2026 rejoués par les tests (les mots de
l'émetteur sont ceux de Météo-France, les noms des champs restent en anglais).

```json
{
  "query": {"lat": 44.9334, "lon": 4.8924, "country": null, "river_radius_km": 5.0},
  "countries": ["FR"],
  "vigilances": [{
    "id": "meteoalarm:b77d3bc0-76a2-4886-8da8-bac8d99f57fc:6-0-1",
    "source": "meteoalarm", "country": "FR",
    "attribution": "Source: EUMETNET – MeteoAlarm (https://meteoalarm.org/en/live/), ... | Issued by METEO-FRANCE",
    "identifier": "2.49.0.0.250.1.FR.20261020114209.041024",
    "sent_utc": 1792489329, "onset_utc": 1792533600, "expires_utc": 1792620000,
    "message_type": "Alert", "status": "new",
    "level": "YELLOW", "level_original": "Yellow",
    "phenomenon": "flood", "phenomenon_original": "Flooding", "event": "Moderate flooding warning",
    "issuer": "METEO-FRANCE",
    "texts": [{"language": "fr-FR", "headline": "Vigilance jaune inondations", "description": "...", "instruction": "..."}],
    "area": {"description": "Drôme", "geocodes": [{"scheme": "NUTS3", "value": "FR713"}, ...], "has_polygon": false, "localization": "geocode"},
    "link": "https://feeds.meteoalarm.org/api/v1/warnings/feeds-france/b77d3bc0-...",
    "derived": ["status", "level", "phenomenon"]
  }],
  "coverage": [{"source": "meteoalarm", "country": "FR", "status": "ok", "warnings_in_feed": 15, "warnings_not_matchable": 0}],
  "attribution": "...", "disclaimer": "Time delays between this website and the www.meteoalarm.org website are possible. ..."
}
```

Un tronçon de cours d'eau est une **ligne** : la même route pour Nîmes (Gard), à 3,9 km du Vistre, que Vigicrues
avait en orange ce jour-là (extrait ; le bulletin officiel du territoire disait la même chose). L'« inondation »
du département, orange elle aussi chez MeteoAlarm, est écartée de cette réponse parce que le tronçon la dit, plus
finement.

```json
{
  "vigilances": [{
    "id": "vigicrues:GA32", "source": "vigicrues", "country": "FR",
    "attribution": "Source: Vigicrues, Service central d'hydrométéorologie et d'appui à la prévision des inondations (SCHAPI), https://www.vigicrues.gouv.fr. Licence Ouverte 2.0 | Last update of the flow: 2026-10-20T07:55:15+00:00",
    "level": "ORANGE", "level_original": "3", "phenomenon": "flood",
    "area": {"description": "Vistre", "geocodes": [{"scheme": "VIGICRUES_SECTION", "value": "GA32"}, ...], "has_polygon": false, "localization": "line"},
    "distance_km": 3.93,
    "web": "https://www.vigicrues.gouv.fr/bulletin_local/20#GA32",
    "derived": ["level", "phenomenon"]
  }],
  "coverage": [{"source": "meteoalarm", "country": "FR", "status": "ok", ...}, {"source": "vigicrues", "country": "FR", "status": "ok", ...}]
}
```

- **Les mots de la source et les champs de l'API sont distingués.** `level` (GREEN, YELLOW, ORANGE, RED),
  `phenomenon` (wind, rain, thunderstorm, snow-ice, heat, cold, flood, avalanche, coastal, fire, fog, other) et
  `status` (new, update, cancel) sont ceux de l'API et sont nommés dans `derived` ; les tables sont dans
  [`docs/data-sources.md`](docs/data-sources.md). Un mot qu'aucune table ne couvre donne `null`. `level_original`,
  `phenomenon_original`, `event` et `texts` sont ceux de la source.
- **Affichez le crédit et l'avis.** Chaque vigilance a son `attribution`, et la réponse a le `disclaimer` que les
  conditions de MeteoAlarm obligent un redistributeur à publier. Ce n'est pas un service d'alerte officiel. Seules les
  sources qui ont répondu sont créditées.
- **Une vigilance qui ne peut pas être rattachée à un point le dit.** Un point est rattaché par le polygone de la
  vigilance, par la forme du code de sa zone ou, pour un tronçon de cours d'eau, par sa distance à la ligne
  (`river_radius_km` ; `distance_km` dans la réponse). Les vigilances de la France, de la Belgique, de l'Allemagne, du
  Canada et des pays qui envoient des polygones (par exemple la Suisse et la Norvège) sont rattachées. Celles qui ne
  nomment leur zone que par des codes MeteoAlarm `EMMA_ID` (Italie, Espagne, Autriche, Pologne...) sont listées par `country=`
  mais **ne sont pas rattachées à un point** : les formes de ces codes n'ont pas de licence connue et ne sont pas
  incluses ([`SOURCES.md`](SOURCES.md), en anglais). Pour un point, `coverage[].warnings_not_matchable` les compte, de sorte
  qu'une liste vide ne se lit pas comme « rien à craindre ». C'est pareil pour les alertes NWS qui nomment des zones
  au lieu d'un polygone. Un exploitant peut fabriquer les formes `EMMA_ID` pour sa propre instance
  (`tools/build_geometries.py --emma`, puis `VIGIE_EMMA_GEOMETRIES`).
- **Envoi.** Sur `WS /v1/stream`, envoyez `{"action": "follow", "lat": 44.9334, "lon": 4.8924}` : la réponse est un
  `vigilance_state` (ce que donnerait la route), puis, avec `VIGIE_SCHEDULER=1`, un `vigilance_push` chaque fois qu'une
  vigilance de ce point est `appeared` (apparue), `upgraded` (montée d'un niveau : `previous_level` dit depuis lequel)
  ou `ended` (terminée). `{"action": "unfollow", ...}` l'arrête. Les zones surveillées sont suivies elles aussi par le
  planificateur et leurs changements vont à tous les clients. Rien n'est envoyé pour ce qui existait déjà quand un
  point est suivi pour la première fois.
- **Peu de requêtes.** Seules les sources des pays nécessaires sont interrogées (le pays d'un point se trouve hors
  ligne), chacune gardée aussi longtemps que son rythme le permet (MeteoAlarm 180 s, DWD 120 s avec une requête
  conditionnelle, ECCC et MetService 300 s, Vigicrues 30 minutes) ; un document CAP, qui contient les mots de
  l'émetteur, n'est demandé qu'une fois et seulement pour une vigilance qu'une réponse renvoie ; le NWS n'a besoin
  d'aucune requête propre. Voir « Appels aux sources » plus bas.
- La trame embarquée des écrans **ne change pas**.

## Lancer

Python 3.12 ou plus récent (la CI couvre 3.12 et 3.13).

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt     (Windows)
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Sous Windows, `scripts\run.ps1` fait tout cela (voir plus bas). Ouvrez ensuite
`http://localhost:8080/dashboard` ou `http://localhost:8080/v1/embedded/frame`.

### Avec Docker

```
docker build -t vigie-api .
docker run --rm -p 8080:8080 -v vigie-data:/data -e VIGIE_CONTACT=vous@example.org vigie-api
```

L'image (`python:3.13-slim`, construite pour linux/amd64 par la CI) tourne sous un utilisateur non
administrateur, garde sa base dans le volume `/data` et a un contrôle de santé sur `/v1/health`. RainViewer
est désactivé dans l'image ; ajoutez `-e VIGIE_ENABLE_RAINVIEWER=1` pour une instance personnelle. Le
workflow de publication pousse `ghcr.io/axellum/vigie-api` quand une étiquette de version (`v*`) est créée ;
tant qu'il n'y en a pas, construisez l'image vous-même.

### Configuration

Tous les réglages sont des variables d'environnement facultatives.

| Variable | Valeur par défaut | Rôle |
| --- | --- | --- |
| `VIGIE_DB_PATH` | `vigie.db` à la racine du dépôt (ignoré par git) ; `/data/vigie.db` dans l'image Docker | Fichier SQLite (zones surveillées, historique, dernier instantané). |
| `VIGIE_SCHEDULER` | `0` | `1` démarre le planificateur en arrière-plan (rafraîchissement toutes les 90 s, envoi par WebSocket). |
| `VIGIE_ENABLE_SIMULATION` | `0` | `1` active `POST /v1/simulate` (et les boutons du tableau de bord). La route n'a pas d'authentification et écrit les alertes simulées dans l'historique des dangers : ne l'activez que sur une instance que seuls des clients de confiance peuvent atteindre. |
| `VIGIE_ENABLE_RAINVIEWER` | `1` ; `0` dans l'image Docker | `0` cesse d'interroger RainViewer (les champs radar de `/v1/point` restent inconnus). Son API gratuite est réservée à un usage personnel et pédagogique : mettez `0` sur une instance qui sert d'autres personnes. |
| `VIGIE_ENABLE_METSERVICE` | `1` (aussi dans l'image Docker) | `0` cesse d'interroger MetService (Nouvelle-Zélande) : une position là-bas n'a alors aucune source, et la réponse dit que la source est désactivée. Son flux indique CC BY 4.0 mais les conditions du site auxquelles il renvoie n'autorisent qu'un usage personnel et interdisent la rediffusion, et on n'a pas vérifié laquelle prévaut : mettez `0` sur une instance qui sert d'autres personnes, sauf accord de MetService. |
| `VIGIE_CONTACT` | aucune | Adresse e-mail ou URL ajoutée au `User-Agent` envoyé aux sources. MET Norway et NOAA exigent un contact identifiable : renseignez-la. |
| `VIGIE_CAP_SENDER` | `vigie-api` | `sender` des alertes CAP. |
| `VIGIE_GRID_FORECAST_DEG` | `0.05` | Grille (en degrés, environ 5 km) sur laquelle la position est arrondie avant d'interroger la prévision Open-Meteo et MET Norway. |
| `VIGIE_GRID_AIR_QUALITY_DEG` | `0.1` | Idem pour Open-Meteo Air Quality (environ 11 km). |
| `VIGIE_GRID_FLOOD_DEG` | `0.1` | Idem pour Open-Meteo Flood (GloFAS). |
| `VIGIE_GRID_MARINE_DEG` | `0.1` | Idem pour Open-Meteo Marine. |
| `VIGIE_EMMA_GEOMETRIES` | aucune | Chemin d'un GeoJSON compressé des formes MeteoAlarm `EMMA_ID` que vous avez fabriqué vous-même (`python tools/build_geometries.py --emma` ; par défaut `app/data/local/emma_id.geojson.gz` est lu s'il existe). Il permet de rattacher à un point les vigilances qui ne nomment leur zone que par des codes `EMMA_ID`. Non incluses dans le dépôt : voir [`SOURCES.md`](SOURCES.md). |
| `VIGIE_BUDGET_FRACTION` | `0.9` | Part du quota publié de chaque source (Open-Meteo 10 000 appels par jour, MET Norway, RainViewer) que l'API laisse passer. Au-delà, le cache est servi, marqué périmé. |

Aucune source ne demande de clé d'API. Une valeur de grille hors de 0,0001 à 10 degrés est ignorée (la
valeur par défaut est utilisée). Seules la requête envoyée à la source et la clé du cache utilisent le point
arrondi : la réponse donne toujours la position exacte du client, et les distances aux dangers l'utilisent.

### Appels aux sources

Les appels aux sources ne devraient pas augmenter avec le nombre de clients : ils dépendent du nombre de
cases de 5 km distinctes demandées et de la vitesse à laquelle chaque source change vraiment (grilles, une
entrée de cache par case, une seule requête pour des appelants simultanés, durées de cache calées sur le
rythme de chaque source, un budget d'appels ; voir [`docs/architecture.md`](docs/architecture.md) et
[`docs/data-sources.md`](docs/data-sources.md), en anglais). `tests/test_call_sobriety.py` rejoue une journée
simulée avec le planificateur actif et ses 5 zones par défaut, et 1 000 clients sur 50 points (20 chacun,
qui interrogent toutes les 30 minutes ; 10 points sont des ports qui demandent aussi la mer ; deux points ne
partagent jamais une case, donc les grilles n'aident pas), contre une source simulée, et compte les requêtes
que reçoit chaque source :

| Source | Appels par jour, avant | après |
| --- | ---: | ---: |
| Open-Meteo prévision | 17 212 | 6 167 |
| Open-Meteo qualité de l'air | 7 182 | 1 320 |
| Open-Meteo débit des rivières | 6 122 | 200 |
| Open-Meteo mer | 2 216 | 240 |
| **Open-Meteo, pondéré** (le quota gratuit est de 10 000 par jour) | **46 502** | **8 412** |
| Séismes USGS, EMSC | 1 980 chacun | 1 393 chacun |
| Alertes NOAA NWS | 1 069 | 705 |
| GDACS | 676 | 474 |
| RainViewer | 662 | 144 |
| NOAA SWPC | 403 | 286 |
| Volcans USGS | 212 | 144 |
| Flux MeteoAlarm (France et Islande : les pays de deux des cinq zones) | sans objet | 640 |
| Documents CAP de MeteoAlarm | sans objet | 0 |
| Flux Vigicrues (Paris est en France ; 2,2 Mo chacun ; 138 tant qu'un tronçon est en orange, 46 un jour calme) | sans objet | 138 |
| DWD, ECCC, MetService (aucune zone en Allemagne, au Canada ni en Nouvelle-Zélande) | sans objet | 0 |

« Avant » est `main` au commit 1864e25 (même simulation, mesurée une fois) ; « après » est ce code. L'heure
la plus chargée d'Open-Meteo pèse 2 305 avant et 412 après (quota de 5 000), la minute la plus chargée 497 et
231 (quota de 600). Avec des clients qui interrogent toutes les 15 minutes, « avant » passe à 54 766 et
« après » à 8 667 : la charge des clients change à peine le « après ». Les 55 cases distinctes de ce scénario
consomment 84 % du quota gratuit ; au-delà, le budget arrête les requêtes et sert le cache, marqué périmé.
À rejouer soi-même : `python -m tests.simulation` (une vingtaine de secondes).

Les vigilances officielles ajoutent un flux MeteoAlarm par pays nécessaire, soit 320 requêtes par jour quand le
planificateur le garde à jour (un cache de 180 s, redemandé au cycle de 90 s suivant), et rien pour le NWS : sa
réponse est celle des dangers, ce que confirme la même journée simulée (705 appels avec ou sans les vigilances).
`tests/test_vigilance_sobriety.py` joue 500 clients sur 50 positions de quatre pays pendant 6 heures : 6 000
réponses coûtent 320 requêtes de flux (80 par pays) et 4 documents CAP.

Une zone, un point suivi ou un client en **Allemagne** coûte 480 requêtes par jour au DWD (479 d'entre elles en
`304`, quelques centaines d'octets) et plus le flux MeteoAlarm allemand (320 par jour) ; au **Canada**, 240 à ECCC
(31 Ko chacune avec gzip) ; en **Nouvelle-Zélande**, 240 à MetService (239 en `304`). Quel que soit le nombre de
lecteurs des vigilances, une source n'est interrogée qu'une fois par durée de cache au plus (720, 288, 288 et 144
par jour pour le DWD, ECCC, MetService et Vigicrues). `tests/test_national_sobriety.py` joue ces journées.

### Lancer sous Windows (un PC qui alimente l'écran)

L'écran récupère `http://<adresse du PC>:8080/v1/embedded/frame`. Donnez au PC une adresse fixe dans la box
ou le routeur pour que cette URL ne change pas. Ci-dessous, `<dossier du dépôt>` est le dossier où vous avez
cloné ce dépôt.

1. **Démarrer l'API**, depuis n'importe quel dossier, dans un PowerShell normal (pas administrateur) :

   ```
   powershell -NoProfile -ExecutionPolicy Bypass -File <dossier du dépôt>\scripts\run.ps1
   ```

   Le script crée `.venv` dans le dépôt au premier lancement, installe `requirements.txt` (de nouveau
   seulement si le fichier change) et démarre `uvicorn app.main:app --host 0.0.0.0 --port 8080`.
   Options : `-Port`, `-BindAddress`, `-LogFile <fichier>`, `-NoInstall`. Arrêt avec Ctrl+C.

2. **Autoriser l'écran à l'atteindre.** Le pare-feu Windows bloque les connexions entrantes par défaut.
   Lancez cette ligne **une seule fois, dans un PowerShell ouvert en administrateur** (elle ouvre le port
   TCP 8080 sur le profil réseau Privé, pour le sous-réseau local seulement) :

   ```
   New-NetFirewallRule -DisplayName "Vigie API 8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Private -RemoteAddress LocalSubnet
   ```

   Le réseau de la maison doit être déclaré *Privé* dans Windows (Paramètres, Réseau et Internet). Pour
   retirer la règle plus tard : `Remove-NetFirewallRule -DisplayName "Vigie API 8080"`.

3. **Vérifier depuis un autre appareil** du même réseau :

   ```
   Invoke-WebRequest http://<adresse du PC>:8080/v1/health -UseBasicParsing
   ```

4. **Démarrer à l'ouverture de session** avec le Planificateur de tâches (le dépôt ne crée rien).
   Planificateur de tâches, *Créer une tâche...* (pas *Créer une tâche de base*) :

   - *Général* : nom `Vigie API` ; « Exécuter uniquement si l'utilisateur est connecté ».
   - *Déclencheurs* : Nouveau, « À l'ouverture de session », utilisateur précis : vous.
   - *Actions* : Nouvelle, « Démarrer un programme ». Programme : `powershell.exe`. Arguments :
     `-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "<dossier du dépôt>\scripts\run.ps1" -LogFile "<dossier du dépôt>\logs\vigie-api.log"`.
     Commencer dans : `<dossier du dépôt>`.
   - *Paramètres* : « Si la tâche échoue, la redémarrer toutes les » 1 minute, 3 tentatives ; décochez
     « Arrêter la tâche si elle s'exécute plus de ». Si le dépôt est sur un autre disque, ce disque doit être
     disponible à l'ouverture de session. Si le dossier du dépôt est renommé ou déplacé, mettez les chemins à
     jour. La sortie du serveur va dans `logs\vigie-api.log` (ignoré par git) ; arrêtez le serveur en
     terminant la tâche dans le Planificateur de tâches.

## Développer

```
python -m pytest              # aucun test ne touche le réseau
python -m ruff check .        # analyse statique
python tools/capture_frame.py frame.txt     # écrit une VRAIE trame (utilise les vraies sources)
python tools/check_live_feeds.py            # interroge chaque vraie source une fois, vérifie que les analyseurs la lisent encore
python tools/build_geometries.py            # reconstruit app/data (frontières et formes NUTS) depuis Natural Earth et Wikidata
```

`tools/capture_frame.py` démarre l'application dans le processus, récupère
`/v1/embedded/frame?device_type=waveshare-7b` auprès des vraies sources et l'écrit dans le fichier donné.
Il sert à produire le jeu d'essai du dépôt de l'écran.

CI (`.github/workflows/`) :

- `ci.yml`, à chaque envoi sur `main` et chaque pull request : ruff et pytest sur Python 3.12 et 3.13, et une
  tâche qui construit l'image Docker, la lance et interroge `/v1/health` et `/v1/embedded/frame`.
- `secret-scan.yml` : gitleaks sur tout l'historique git, à chaque pull request et envoi sur `main`.
- `live-feeds.yml` : chaque lundi et à la demande, jamais dans une pull request, `tools/check_live_feeds.py`
  interroge chaque vraie source une fois (MeteoAlarm, les vigilances du NWS, le DWD, ECCC, MetService et Vigicrues
compris). Une exécution rouge
  veut dire qu'une source a changé de format, retiré un point d'accès, ou envoyé un mot de niveau ou de
  phénomène que les tables ne connaissent pas.
- `publish-image.yml` : pousse l'image Docker vers `ghcr.io/axellum/vigie-api` quand une étiquette `v*` est
  poussée.

## Sources de données

| Domaine | Source |
| --- | --- |
| Séismes | USGS, EMSC / SeismicPortal, GDACS |
| Catastrophes | GDACS, NOAA NWS, USGS Volcano Hazards Program |
| Vigilances météo officielles | MeteoAlarm (EUMETNET, 39 pays d'Europe), Deutscher Wetterdienst (Allemagne), Environnement et Changement climatique Canada, MetService (Nouvelle-Zélande), NOAA NWS (États-Unis), Vigicrues (crues des cours d'eau de France) |
| Météo spatiale | NOAA SWPC |
| Météo | Open-Meteo (principale), MET Norway (secours), RainViewer (radar) |
| Mer, débit des rivières, qualité de l'air | Open-Meteo Marine, Flood (GloFAS), Air Quality (CAMS) |

Licence, conditions, crédit, quota et clauses de chaque source, et ce qui n'est pas vérifié :
[`SOURCES.md`](SOURCES.md). Comment chacune est mise en cache et ce qu'elle coûte en appels :
[`docs/data-sources.md`](docs/data-sources.md).

## Documentation

La documentation du dépôt est en anglais.

- [`SOURCES.md`](SOURCES.md) : les sources, leurs licences, leurs crédits et leurs quotas.
- [`docs/architecture.md`](docs/architecture.md) : composants, flux de données, limites.
- [`docs/embedded-frame.md`](docs/embedded-frame.md) : le contrat de la trame (source unique de vérité).
- [`docs/data-sources.md`](docs/data-sources.md) : comment chaque source est utilisée, cache et volume d'appels.
- [`docs/decisions/`](docs/decisions) : décisions d'architecture.
- [`CHANGELOG.md`](CHANGELOG.md), [`AGENTS.md`](AGENTS.md) (guide pour les agents de code IA).
