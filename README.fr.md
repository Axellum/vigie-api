# <img src="docs/logo.svg" alt="" width="40" height="40" align="top"> Vigie API

*[Read this document in English](README.md)*

**Démo en ligne : [vigie.auxfilsdesidees.fr](https://vigie.auxfilsdesidees.fr)**, le site Vigie, tourne sur cette
API ([vigie-web](https://github.com/Axellum/vigie-web)).

Un projet personnel, fait avec l'aide de l'IA et partagé au cas où il servirait à quelqu'un. Il est fourni
tel quel, sans aucune promesse de support, de disponibilité ni d'exactitude.

Il rassemble des dangers naturels dans le monde (séismes, tsunamis, volcans, cyclones, tornades,
inondations, incendies, orages géomagnétiques), de la météo (conditions actuelles, pluie à 15 minutes,
prévisions, mer, débit des rivières, qualité de l'air) et les vigilances officielles des services nationaux
(MeteoAlarm en Europe, le Deutscher Wetterdienst en Allemagne, Environnement et Changement climatique Canada,
MetService en Nouvelle-Zélande, le National Weather Service aux États-Unis, et Vigicrues pour les cours d'eau de
France) à partir de sources publiques, puis les sert :

- **à un écran météo et d'alerte autonome**, sans Home Assistant : un écran ESP32-P4 lit une trame texte compacte
  ([`docs/embedded-frame.md`](docs/embedded-frame.md)), voir
  [vigie-display](https://github.com/Axellum/vigie-display) (Waveshare 7B) ;
- **à un écran domotique relié à Home Assistant**, comme
  [M5-Tab5-ESPHome-LVGL](https://github.com/Axellum/M5-Tab5-ESPHome-LVGL) (M5Stack Tab5, Waveshare 7B) ;
- **à Home Assistant lui-même**, par son intégration (`custom_components/vigie`) : capteurs et événements pour
  lancer des automatisations ;
- à tout autre client, site ou script.

Elle suit les normes internationales : GeoJSON (IETF RFC 7946), OASIS CAP 1.2 pour les vigilances, niveaux et textes
des services émetteurs laissés tels quels, services nationaux du registre de l'OMM, horodatage en UTC.

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
- Le fichier des failles actives nommées (`app/data/gem-active-faults.geojson.gz`, utilisé pour `near_places`) est sous **CC BY-SA 4.0**
  (GEM Global Active Faults Database, Styron et Pagani 2020), pas MIT : partage dans les mêmes conditions, avec sa notice à côté
  ([`app/data/README.md`](app/data/README.md), [`SOURCES.md`](SOURCES.md)).
- Les services nationaux du registre de l'OMM (`VIGIE_WMO_SOURCES`) : ce sont les conditions de chaque émetteur qui comptent,
  et la plupart n'ont pas été trouvées ou limitent la copie. Le réglage par défaut ne lit que les 7 dont les conditions
  propres ont été lues et permettent de les relayer ; `all` est pour une instance que son propriétaire est seul à utiliser.
  Hong Kong et la Chine ne sont jamais lus ([`SOURCES.md`](SOURCES.md)).

## Points d'accès

| Point d'accès | Rôle |
| --- | --- |
| `GET /v1/embedded/frame` | Trame texte compacte pour les écrans ([contrat](docs/embedded-frame.md)). |
| `GET /v1/snapshot` | Zones surveillées (météo) et dangers mondiaux, JSON compact. |
| `GET /v1/point?lat=&lon=` | Météo en un point GPS, mer (`marine=true`), débit des rivières, qualité de l'air, météo spatiale, dangers proches. |
| `GET /v1/geojson/hazards` | Dangers actifs en GeoJSON (RFC 7946) : un point chacun. `has_forecast` dit si une tempête ou un tsunami a une prévision ; `near_places` et `derived` disent de quoi un séisme ou un événement volcanique est proche (calculé par Vigie). Voir « Lieux proches d'un danger » plus bas. |
| `GET /v1/geojson/hazards/{id}/forecast` | La prévision d'un danger en GeoJSON : le cône, la trajectoire et les positions d'un cyclone tropical, l'épicentre et les heures d'arrivée d'un tsunami. Voir « Tempêtes et tsunamis » plus bas. |
| `GET /v1/earthquakes?lat=&lon=` | Les séismes (USGS et EMSC, fusionnés) dans un rayon `radius_km` autour d'une position sur les dernières `hours` heures, le plus fort d'abord, avec leur distance ; `min_magnitude` les filtre. Aucun fournisseur météo n'est appelé. Ce n'est pas une vigilance officielle. |
| `GET /v1/fires?bbox=w,s,e,n`, `GET /v1/fires/grid` | Les feux actifs vus par NASA FIRMS (VIIRS, NOAA-20 et NOAA-21) : les détections d'une zone en GeoJSON, ou comptées par cellule pour les petits zooms. Sans clé (404 seulement sur une instance qui a désactivé la source avec `VIGIE_ENABLE_FIRMS=0`). Voir « Feux actifs » plus bas. |
| `GET /v1/cap/alerts` | Dangers actifs en OASIS CAP 1.2. |
| `GET /v1/vigilances?lat=&lon=` ou `?country=FR` ou `?source=tsunami` | Vigilances météo officielles qui couvrent un point, ou toutes celles d'un pays (MeteoAlarm, DWD, ECCC, MetService, NWS, Vigicrues et les services nationaux du registre de l'OMM), ou les messages de tsunami en cours, avec les mots de l'émetteur intacts. Voir plus bas. |
| `GET /v1/vigilances/countries` | Les pays que cette instance sert avec ses réglages, les sources qui répondent pour chacun, et si leur licence est vérifiée. Avec `?uncovered=true`, tous les autres pays du monde aussi, chacun avec la raison pour laquelle rien n'y est lu. |
| `GET /v1/vigilances.geojson` | Les mêmes vigilances en GeoJSON, la forme de chaque zone en géométrie. |
| `GET/POST /v1/zones`, `DELETE /v1/zones/{id}` | Zones surveillées (la liste de suivi). Une zone doit donner son `utc_offset_s`. |
| `GET/POST /v1/watched-points`, `DELETE /v1/watched-points/{id}` | Les points que vous surveillez (un lieu, une faille, un volcan...), chacun avec un rayon. **Personnels** : listés ici et dans le statut, jamais dans les dangers. `POST` et `DELETE` sont **désactivés** (403) sauf si `VIGIE_ENABLE_WATCHED_POINTS_WRITE=1`. |
| `GET /v1/watched-points/status?days=7` | Pour chaque point surveillé, les événements dans son rayon sur les derniers jours, et `quiet` seulement si chaque source de dangers a répondu et a été lue sans trou. |
| `GET /v1/history` | Historique des dangers, stocké dans SQLite. |
| `GET /v1/health`, `GET /v1/providers` | État, disjoncteur et cache de chaque source, avec ses appels, son poids et son quota sur 24 heures. |
| `GET/POST /v1/scheduler/...` | État du planificateur en arrière-plan (facultatif), lancement d'un cycle immédiat. |
| `POST /v1/simulate` | Injecte un scénario **simulé** pour les essais (toujours étiqueté comme simulation). **Désactivé par défaut** : il répond 403 sauf si `VIGIE_ENABLE_SIMULATION=1`. |
| `WS /v1/stream` | WebSocket avec l'état initial et les alertes poussées ; un client peut aussi suivre un point et être prévenu quand ses vigilances officielles changent. |
| `GET /dashboard` | Page web de supervision. |
| `GET /docs` | Documentation OpenAPI interactive. |

**L'API n'a aucune authentification par défaut.** Utilisez-la sur un réseau de confiance. Ne l'exposez pas telle quelle
sur Internet : quiconque peut l'atteindre peut ajouter ou supprimer des zones et des points surveillés et déclencher des
cycles de collecte. Si vous devez l'exposer, placez devant elle un proxy inverse qui authentifie. **`VIGIE_ADMIN_TOKEN`** est un
premier pas : quand il est défini, toute route qui modifie quelque chose (`POST`, `PUT`, `PATCH`, `DELETE` : aujourd'hui `/v1/zones`,
`/v1/watched-points`, `/v1/scheduler/trigger` et `/v1/simulate`) exige `Authorization: Bearer <jeton>` et répond 401 sans lui ; les lectures
n'en ont jamais besoin. Non défini, rien ne change. Le jeton est un secret partagé dans un en-tête : mettez TLS devant une instance qui n'est pas
sur un réseau de confiance ([`docs/architecture.md`](docs/architecture.md) et [ADR 0009](docs/decisions/0009-every-border-a-reason-per-country-and-the-admin-token.md), en anglais).

**Sur une instance publique, `POST` et `DELETE` de `/v1/watched-points` ne doivent pas être atteignables depuis Internet**, comme `POST /v1/zones` et
`POST /v1/simulate` : placez l'instance derrière un proxy inverse qui les bloque (les lectures peuvent rester ouvertes). Comme un point surveillé est
personnel (un domicile, par exemple), ces deux routes sont aussi **désactivées par défaut** : elles répondent 403 tant que l'instance n'a pas
`VIGIE_ENABLE_WATCHED_POINTS_WRITE=1` (`GET /v1/health` indique `watched_points_write_enabled`), à réserver à une instance que seuls des clients de
confiance peuvent atteindre.

## Vigilances météo officielles

`GET /v1/vigilances` donne les vigilances que publient les services météo nationaux : **MeteoAlarm** (EUMETNET)
pour 39 pays d'Europe, le **Deutscher Wetterdienst** pour l'Allemagne, **Environnement et Changement climatique
Canada**, **MetService** pour la Nouvelle-Zélande, le **National Weather Service** pour les États-Unis et
**Vigicrues** pour les crues des cours d'eau de France, et, pour plus de pays, les services nationaux du registre de l'OMM (plus bas). L'API les relaie, elle n'en émet aucune. Les mots de
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
  nomment leur zone que par des codes MeteoAlarm `EMMA_ID` (Italie, Autriche, Pologne...) sont listées par `country=`
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
  conditionnelle, ECCC et MetService 300 s, Vigicrues un coup d'œil à une image de 330 octets toutes les 15 minutes et
  le flux de 2,2 Mo seulement quand il a changé) ; un document CAP, qui contient les mots de
  l'émetteur, n'est demandé qu'une fois et seulement pour une vigilance qu'une réponse renvoie ; le NWS n'a besoin
  d'aucune requête propre. Voir « Appels aux sources » plus bas.
- La trame embarquée des écrans **ne change pas**.

### Plus de pays, par le registre de l'OMM

L'Organisation météorologique mondiale tient un registre des services nationaux qui publient des vigilances CAP (le
Severe Weather Information Centre : 140 services dans 135 pays au 2026-10-20). Pour un pays qui n'a **aucune source
propre**, l'API lit le service de ce registre, avec les mêmes règles (une seule source répond, les mots de l'émetteur
restent intacts, les champs de l'API sont nommés dans `derived`). `VIGIE_WMO_SOURCES` choisit les services lus, parce que ce
sont les conditions propres à chaque émetteur qui comptent et que la plupart n'ont pas été trouvées ou limitent la copie
([`SOURCES.md`](SOURCES.md), en anglais, une ligne pour chacun des 28 services examinés) :

- `verified` (par défaut) : seulement les **7 services dont les conditions propres ont été lues et permettent de les
  relayer** : l'Algérie, l'Argentine, la Barbade, la Jamaïque, le Mexique, les Philippines et l'Espagne (AEMET à la place
  de MeteoAlarm, parce que le flux d'AEMET donne des polygones là où celui de MeteoAlarm donne des codes : 34 contre 0 le
  2026-10-20, [ADR 0006](docs/decisions/0006-wmo-registry-services.md), en anglais).
- `all` : tous les services qui ne sont pas exclus, 95 services dans 94 pays. Seulement pour une instance que son
  propriétaire est seul à utiliser : 88 d'entre eux ont des conditions inconnues ou qui limitent la copie. Hong Kong et la
  Chine ne sont jamais lus.
- `off` : aucun.

```
GET /v1/vigilances/countries                   les pays que cette instance sert avec ses réglages : les sources, et si la licence est vérifiée
GET /v1/vigilances?country=MX                  les vigilances du Mexique (SMN), polygones, textes en espagnol tels qu'écrits
GET /v1/vigilances?lat=38.9067&lon=1.4206      Ibiza : les vigilances d'AEMET dont le polygone contient le point
```

Vraie réponse pour Ibiza à 18 h 34 UTC le 2026-10-20 (les flux ont été lus en direct ; extrait) :

```json
{
  "query": {"lat": 38.9067, "lon": 1.4206, "country": null, "river_radius_km": 5.0},
  "countries": ["ES"],
  "vigilances": [{
    "id": "swic:es-aemet-es:2.49.0.0.724.0.ES.20261020172954.645301PRP120201792517394:0.0",
    "source": "swic", "country": "ES",
    "attribution": "Source: AEMET (Agencia Estatal de Meteorología), Spain. Información elaborada por la Agencia Estatal de Meteorología. © AEMET | Warnings of a national meteorological service, from the registry of the WMO Severe Weather Information Centre (https://severeweather.wmo.int). The issuer's own terms apply to each warning",
    "identifier": "2.49.0.0.724.0.ES.20261020172954.645301PRP120201792517394",
    "onset_utc": 1792515600, "expires_utc": 1792526399, "message_type": "Alert", "status": "new",
    "level": "YELLOW", "level_original": "amarillo",
    "phenomenon": "rain", "phenomenon_original": "Aviso de lluvias de nivel amarillo",
    "event": "Aviso de lluvias de nivel amarillo", "issuer": "AEMET. Agencia Estatal de Meteorología",
    "texts": [{"language": "es-ES", "headline": "Aviso de lluvias de nivel amarillo. Ibiza y Formentera", "description": "Precipitación acumulada en una hora: 20 mm. ...", "instruction": "..."},
              {"language": "en-GB", "headline": "Moderate rain warning. Ibiza y Formentera", "description": "...", "instruction": "..."}],
    "area": {"description": "Ibiza y Formentera", "geocodes": [{"scheme": "AEMET-Meteoalerta zona", "value": "645301"}], "has_polygon": true, "localization": "polygon"},
    "derived": ["status", "level", "phenomenon"]
  }],
  "coverage": [{"source": "swic", "source_id": "es-aemet-es", "country": "ES", "status": "ok", "warnings_in_feed": 19, "warnings_not_matchable": 0}]
}
```

- **Un service qui ne répond pas n'est pas « rien en vigueur ».** Chaque service est sa propre ligne de `coverage`
  (`source: "swic"`, `source_id`) : `ok`, `stale` (sa dernière réponse, signalée) ou `unavailable`. Le même jour, le flux
  des Philippines n'a pas répondu à temps depuis le PC d'essai, et l'API a dit `unavailable` pour lui, jamais une liste
  vide.
- **Les vigilances qui ne nomment que des codes** (Philippines, Inde) sont listées par `country=` et comptées dans
  `coverage[].warnings_not_matchable` ; elles ne sont pas placées sur un point. Une vigilance avec un polygone ou un cercle
  est rattachée.
- **`level` et `phenomenon` sont dérivés** d'une couleur que l'émetteur écrit (`amarillo`, `Alerta Roja`...) ou de la
  gravité CAP (`Minor` ne donne aucun niveau), et des mots de l'événement en anglais, espagnol, portugais et français ;
  tout le reste donne `null`. `event`, `texts` dans chaque langue et `level_original` de l'émetteur restent tels qu'écrits.
- **Ce que ça coûte** : un service est interrogé toutes les 6 minutes au plus, avec des requêtes conditionnelles (`304`)
  quand le serveur donne un validateur, et chaque document CAP est lu une fois. Une journée simulée du planificateur
  (`tests/test_swic_sobriety.py`) : **2 010 requêtes et 10,6 Mo** pour les 7 services par défaut, **36 924 requêtes et
  51 Mo** pour `all` avec un point suivi dans chacun des 94 pays (le pire cas ; le nombre de clients n'ajoute rien). Un
  plafond de 50 000 requêtes par jour couvre l'ensemble. [`docs/data-sources.md`](docs/data-sources.md) (en anglais) donne le
  rythme et ses raisons.

### Un pays sans source dit pourquoi

Les frontières couvrent tous les pays (Natural Earth, 244), donc un point au Japon, en Australie ou en Chine trouve son pays. La réponse le nomme et
dit pourquoi rien n'y est lu, dans `uncovered` et dans `note`, avec un `reason_code` stable qu'un client peut traduire :

```
GET /v1/vigilances?lat=35.6762&lon=139.6503      Tokyo (abrégé)
{
  "countries": [], "vigilances": [], "coverage": [],
  "uncovered": [{"country": "JP", "name": "Japan", "covered": false, "reason_code": "law_restricts",
                 "reason": "Japanese law reserves the giving of warnings to the Japan Meteorological Agency (Meteorological Service Act, article 23): this instance reads none of its warnings."}],
  "note": "Japan (JP): Japanese law reserves ... The countries this instance reads: GET /v1/vigilances/countries."
}
GET /v1/vigilances/countries?uncovered=true      tous les pays, ceux qui ont une source inchangés
```

Les codes : `no_source` (aucun flux d'alertes officiel n'est lu pour le pays, et rien de plus n'est connu), `licence_unverified`, `terms_restrict`,
`terms_contradictory`, `redistribution_forbidden`, `law_restricts` et `source_off`. **Une raison n'est écrite que si elle a été lue** : où, et ce qui
n'est pas vérifié, est dans [`SOURCES.md`](SOURCES.md) (en anglais), « Countries with no source ». Un point en mer, à plus de 25 km environ d'une côte,
n'est dans aucun pays et le dit. [ADR 0009](docs/decisions/0009-every-border-a-reason-per-country-and-the-admin-token.md).

## Tempêtes et tsunamis

`GET /v1/geojson/hazards` liste aussi les **cyclones tropicaux** du National Hurricane Center (NHC) et les **messages de
tsunami** des U.S. Tsunami Warning Centers (le NTWC pour les États-Unis et le Canada, le PTWC pour le Pacifique et les
Caraïbes). Chacun est un **point**, comme tout autre danger (une tempête à sa position actuelle, un tsunami à
l'épicentre), avec les propriétés habituelles et `has_forecast`. La prévision est une autre route, pour qu'une carte ne la
demande que pour la tempête qu'un visiteur ouvre :

```
GET /v1/geojson/hazards/nhc_al092026/forecast             le cône, la trajectoire et les positions prévues d'une tempête
GET /v1/geojson/hazards/tsunami_pheb_26282000/forecast    l'épicentre et les heures d'arrivée d'un tsunami
GET /v1/vigilances?source=tsunami                         tous les messages de tsunami en cours, les mots du centre intacts
```

- **La prévision est une FeatureCollection** (RFC 7946) avec `source`, `issued_utc`, `advisory`, `advisory_url` et
  `attribution` ; chaque entité a `properties.kind` : `cone` (Polygon), `track` (LineString), `position` (Point avec
  `valid_utc`, `wind_kt`, `pressure_hpa`...) et `watch_warning` pour une tempête ; `epicentre` et `eta` (Point avec `eta_utc`)
  pour un tsunami. Un cône qui franchit la ligne de changement de date est coupé en deux, comme le demande la RFC 7946. Les
  coordonnées ont 3 décimales ; une pression que le NHC ne donne pas est `null`. L'avis 13A de l'ouragan Isaias pèse 22,7 ko.
  Un danger sans prévision reçoit une collection vide et une `reason` ; un identifiant inconnu donne un 404.
- **Un bulletin d'information n'est pas une alerte.** Les centres en publient beaucoup, qui disent qu'il n'y a pas de menace :
  ils ne sont jamais montrés. Une annulation, un essai, une classe de menace que les tables ne connaissent pas et le
  message « final » d'un événement n'en sont pas non plus.
- **Les niveaux sont les nôtres** (les mots sont ceux des centres) : une tempête selon son vent (34 nœuds jaune, 64 orange,
  96 rouge) ; un message de tsunami américain selon sa catégorie (veille jaune, avis orange, alerte rouge) ; un message de
  menace du PTWC selon sa classe de hauteur de vague (0,3-1 m jaune, 1-3 m ou une menace possible orange, plus de 3 m
  rouge). Les tables : [`docs/data-sources.md`](docs/data-sources.md).
- **Un événement, une fois.** GDACS signale les mêmes tempêtes et le NWS relaie les messages de tsunami américains : on sait
  qu'ils ne font qu'un par le nom et la saison de la tempête ou par le code VTEC du message, et la source qui observe
  l'événement donne le niveau.
- **Ce n'est pas une alerte officielle.** Le PTWC dit que ses messages de menace s'adressent aux autorités nationales, qui
  décident du niveau d'alerte de chaque pays. Un message de tsunami nomme ses côtes par un code de zone ou un pays, sans
  forme : il est listé par `country=US` ou `CA`, ou par `source=tsunami`, et jamais rapproché d'un point.
- **Peu de requêtes.** tsunami.gov et le NHC sont interrogés de façon conditionnelle : 960 et 240 requêtes par jour (toutes
  sauf la première en `304`, de quelques centaines d'octets), 230 ko et 116 ko un jour calme ; un message une fois, une
  archive une fois par avis. [ADR 0005](docs/decisions/0005-tsunamis-and-storms.md).

## Feux actifs

`GET /v1/fires` et `GET /v1/fires/grid` servent les feux actifs vus par les instruments VIIRS de NOAA-20 et NOAA-21 (le produit
quasi temps réel de [NASA FIRMS](https://www.earthdata.nasa.gov/data/tools/firms), pixels de 375 m). **Ils ne demandent aucune clé** :
l'instance lit les fichiers statiques que FIRMS publie. Une instance qui a désactivé la source (`VIGIE_ENABLE_FIRMS=0`, voir
« Configuration ») répond 404 et `GET /v1/health` liste la source dans `disabled_sources`. Les feux ne sont pas dans
`GET /v1/geojson/hazards`, qu'une carte charge en entier.

```
GET /v1/fires?bbox=28,-22,38,-12                          les détections d'une zone, les plus fortes d'abord (1 000 au plus)
GET /v1/fires?bbox=170,-25,-170,-10&hours=12&min_confidence=nominal   à cheval sur l'antiméridien, les 12 dernières heures
GET /v1/fires/grid?cell_deg=5                             le monde compté par cellule de 5 degrés
GET /v1/point?lat=-15.4&lon=28.3&fire_radius_km=50        fires_nearby : nombre, distance la plus proche, puissance maximale
```

- **Les champs sont ceux du fichier** : `acq_utc`, `satellite`, `instrument`, `confidence` (`low`, `nominal`, `high`), `frp_mw` (puissance
  radiative du feu), `bright_ti4_k`, `bright_ti5_k`, `scan_km`, `track_km`, `daynight`, `version`. Une valeur que le fichier ne donne pas est `null`.
- **Les limites** : `bbox` est `ouest,sud,est,nord` (un ouest plus grand que l'est passe l'antiméridien ; latitudes entre -90 et 90,
  `sud < nord`) ; `hours` jusqu'à la fenêtre des fichiers que l'instance lit (48 par défaut, `VIGIE_FIRMS_WINDOW_HOURS` ; au-delà, 422) ; `limit` 1 000 par défaut et 5 000 au plus (la grille : 5 000 et 20 000) ; `cell_deg` 0,25, 0,5, 1, 2, 5 ou
  10. Tout le reste est un 422. `metadata` dit combien de détections correspondaient et si la réponse a été coupée.
- **Une détection n'est pas une alerte.** C'est un pixel de satellite qui ressemble à un feu : l'absence de détection n'est pas
  l'absence de feu (un satellite passe quelques fois par jour, les nuages cachent le sol), et la NASA ne conseille pas ces données pour
  des décisions tactiques ni pour des conditions locales. Les données sont publiées environ 3 heures après l'observation ; les
  fichiers statiques ne contiennent que la version quasi temps réel, donc les détections ultra temps réel des États-Unis et du
  Canada (en quelques minutes) n'y sont pas. `fires_nearby` est nommé dans `derived` et ne relève jamais `local_alert_level`.
- **Le crédit** que demande la NASA (`attribution`) et son avertissement (`disclaimer`) sont dans chaque réponse.
- **Peu de requêtes, et un serveur qui se fige.** FIRMS écrit chaque fichier une fois par heure, et son serveur se fige par moments (14 %
  des vérifications envoyées depuis un PC n'ont pas eu de réponse en 40 secondes). L'instance vérifie un fichier au plus toutes les
  60 minutes (`VIGIE_FIRMS_REFRESH_MINUTES`), seulement quand quelqu'un demande (et une fois au démarrage), avec une requête qu'elle ferme
  après les en-têtes quand rien n'a changé, et ne lit que les octets ajoutés à un fichier qui a grandi (vérifiés contre son `ETag`). Une
  journée de 1 000 clients, c'est environ 86 requêtes et 56 Mo par simulation, sans clé et sans transaction (la première version : 60
  lectures, 2 160 transactions, 170 Mo). Une lecture qui ne reçoit aucun octet pendant 25 secondes échoue ; cette source est redemandée
  après 1, 2, 5, puis toutes les 10 minutes, et sa dernière lecture continue d'être servie, avec `stale` vrai. Une requête n'attend jamais
  FIRMS dès que quelque chose a été lu. [ADR 0007](docs/decisions/0007-active-fires-from-nasa-firms.md).
- **La clé est facultative, et jamais montrée.** Avec `VIGIE_FIRMS_MAP_KEY`, une source dont le fichier n'a pas pu être lu est lue par
  l'API de zone à la place. La clé est dans l'URL de ces requêtes : elle est masquée dans chaque erreur, dans `last_error` et dans les
  journaux. Gardez-la hors du dépôt et des commandes que vous partagez.

## Lieux proches d'un danger, et points surveillés

Un séisme ou un événement volcanique dit de quoi il est proche. Chaque danger de `/v1/geojson/hazards` (et de `/v1/history`, et de
`/v1/snapshot` et `/v1/point` avec `compact=false`) porte `near_places`, du plus proche au plus lointain, et le nomme dans `derived` :

```json
"near_places": [
  {"name": "Azuero Fault Zone", "kind": "fault", "distance_km": 4.3, "sources": ["GEM"]}
],
"derived": ["near_places"]
```

Séisme M 7,7 du Panama, 9 octobre 2026, USGS `us6000u18k` : la distance à la trace de surface cartographiée, vérifiée par un second calcul
(Vincenty sur l'ellipsoïde, jusqu'à la trace d'origine : 4,19 km).

- **C'est calculé par Vigie**, pas rapporté par une source, et cela **ne change jamais un `severity`**. Affichez-le comme « calculé par Vigie ».
  `[]` veut dire que rien n'est proche ; `null` que ce n'a pas été calculé (un danger comparé à rien, comme une tempête ou une inondation ; le fichier des
  failles est illisible ; une alerte simulée ; ou le profil compact de `/v1/snapshot` et `/v1/point`, qui reste sous 16 Kio, donc les dangers poussés sur
  `/v1/stream`).
- **Deux ensembles de lieux de référence.** Les failles actives qui ont un nom, de la base GEM Global Active Faults Database (séismes et événements
  volcaniques, à 70 km de profondeur au plus, à moins de 10 km) ; et les volcans que les sources de dangers rapportent elles-mêmes (un séisme à moins
  de 20 km de l'un d'eux). **Aucune liste des volcans du monde n'est embarquée** (celle du Smithsonian a une licence floue) : l'Etna n'est nommé que tant
  que GDACS ou l'USGS le signalent.
- **Les points que vous surveillez ne sont jamais dans `near_places`.** Les dangers sont partagés avec tous les clients, un site public compris, et un point
  surveillé est personnel (un domicile, par exemple) : son nom fuirait, et quiconque pourrait écrire un point ferait apparaître n'importe quel texte dans les
  dangers de tout le monde. Un point surveillé est listé par `/v1/watched-points` et son statut, et nulle part ailleurs.
- **Les rayons sont des réglages, pas des faits publiés par une source** (`VIGIE_NEAR_*` plus bas).
- **Une faille n'est nommée que si la base la nomme.** Plus de la moitié de ses traces n'ont pas de nom, et ce sont celles de la Méditerranée et de l'Asie :
  la faille nord-anatolienne n'est pas nommée dans le fichier. La mention est la distance à la *trace de surface cartographiée*, pas l'affirmation que le séisme
  a eu lieu sur cette faille.
- **Les failles sont sous CC BY-SA 4.0** (Styron et Pagani, 2020), dans un fichier à part avec sa notice : [`app/data/README.md`](app/data/README.md),
  [`SOURCES.md`](SOURCES.md). Le crédit est dans `attribution` du danger qui en nomme une.

```
POST /v1/watched-points   {"name": "Etna", "kind": "volcano", "lat": 37.751, "lon": 14.994, "radius_km": 30}
GET  /v1/watched-points/status?days=7
```

`status` liste, pour chaque point, les événements dans son rayon sur les derniers jours (un séisme à l'heure où il a eu lieu, tout autre danger à la dernière
fois que l'API l'a vu actif) et un `state` : `events` ; `quiet` (rien ne s'est passé, **et chaque source de dangers a répondu et a été lue sans trou depuis
`quiet_since_utc`**) ; ou `unknown`, avec `unreachable_sources`. L'API ne sait que ce qu'elle a vu : une instance démarrée il y a une heure le dit
(`window_covered: false`) au lieu de « rien depuis une semaine ».

Comme `/v1/zones`, les routes des points surveillés n'ont aucune authentification, et la liste et le statut sont lisibles par quiconque atteint l'API : ne
surveillez pas votre domicile sur une instance que d'autres peuvent lire. **`POST` et `DELETE` sont désactivés sauf `VIGIE_ENABLE_WATCHED_POINTS_WRITE=1`**
(403 avec un message sinon ; les lectures marchent toujours), et sur une instance publique ils doivent être bloqués par un proxy inverse, comme `/v1/zones`
et `/v1/simulate`. Détails, chiffres et ce qui n'est pas vérifié : [`docs/data-sources.md`](docs/data-sources.md) (en anglais) et
[ADR 0008](docs/decisions/0008-sensitive-points.md).

## Home Assistant

`custom_components/vigie` est une intégration Home Assistant qui lit ces vigilances pour un lieu et donne un capteur
de niveau maximal, un capteur binaire « vigilance active » et une entité événement (`new`, `upgraded`, `ended`) pour
déclencher des automatisations. Il lui faut une instance de cette API que Home Assistant peut joindre, et rien d'autre.
Installation, options, la règle « inconnu n'est pas aucune », un exemple d'automatisation et les limites :
[`docs/home-assistant.md`](docs/home-assistant.md) (en anglais). Elle est fournie telle quelle, comme le reste du
projet, et n'est pas non plus un service d'alerte officiel.

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
est désactivé dans l'image ; ajoutez `-e VIGIE_ENABLE_RAINVIEWER=1` pour une instance personnelle. NASA FIRMS
(les feux actifs) est activé, aussi dans l'image : il télécharge environ 56 Mo par jour quand quelqu'un demande les routes
des feux, et `-e VIGIE_ENABLE_FIRMS=0` le désactive. Le
workflow de publication pousse `ghcr.io/axellum/vigie-api` quand une étiquette de version (`v*`) est créée ;
tant qu'il n'y en a pas, construisez l'image vous-même.

### Configuration

Tous les réglages sont des variables d'environnement facultatives.

| Variable | Valeur par défaut | Rôle |
| --- | --- | --- |
| `VIGIE_DB_PATH` | `vigie.db` à la racine du dépôt (ignoré par git) ; `/data/vigie.db` dans l'image Docker | Fichier SQLite (zones surveillées, historique, dernier instantané). |
| `VIGIE_SCHEDULER` | `0` | `1` démarre le planificateur en arrière-plan (rafraîchissement toutes les 90 s, envoi par WebSocket). |
| `VIGIE_ENABLE_SIMULATION` | `0` | `1` active `POST /v1/simulate` (et les boutons du tableau de bord). La route n'a pas d'authentification et écrit les alertes simulées dans l'historique des dangers : ne l'activez que sur une instance que seuls des clients de confiance peuvent atteindre. |
| `VIGIE_ENABLE_WATCHED_POINTS_WRITE` | `0` | `1` active `POST` et `DELETE` de `/v1/watched-points` (403 sinon ; les lectures marchent toujours). Les routes n'ont pas d'authentification et un point surveillé est personnel : ne l'activez que sur une instance que seuls des clients de confiance peuvent atteindre, et bloquez ces deux routes dans le proxy inverse d'une instance publique. |
| `VIGIE_ENABLE_RAINVIEWER` | `1` ; `0` dans l'image Docker | `0` cesse d'interroger RainViewer (les champs radar de `/v1/point` restent inconnus). Son API gratuite est réservée à un usage personnel et pédagogique : mettez `0` sur une instance qui sert d'autres personnes. |
| `VIGIE_ENABLE_METSERVICE` | `1` (aussi dans l'image Docker) | `0` cesse d'interroger MetService (Nouvelle-Zélande) : une position là-bas n'a alors aucune source, et la réponse dit que la source est désactivée. Son flux indique CC BY 4.0 mais les conditions du site auxquelles il renvoie n'autorisent qu'un usage personnel et interdisent la rediffusion, et on n'a pas vérifié laquelle prévaut : mettez `0` sur une instance qui sert d'autres personnes, sauf accord de MetService. |
| `VIGIE_ADMIN_TOKEN` | aucun | Quand il est défini, toute requête qui n'est pas une lecture (`POST`, `PUT`, `PATCH`, `DELETE`) doit porter `Authorization: Bearer <jeton>`, sinon la réponse est 401 ; les lectures n'en ont jamais besoin. Non défini ou vide : rien ne change (le réseau est de confiance). Il s'ajoute aux deux interrupteurs ci-dessus : une route désactivée reste en 403. Comparé en temps constant ; il n'apparaît dans aucun journal ni aucune réponse (`GET /v1/health` dit seulement `admin_token_set`). Un secret partagé dans un en-tête : mettez TLS devant toute instance qui n'est pas sur un réseau de confiance. |
| `VIGIE_CONTACT` | aucune | Adresse e-mail ou URL ajoutée au `User-Agent` envoyé aux sources. MET Norway et NOAA exigent un contact identifiable : renseignez-la. |
| `VIGIE_CAP_SENDER` | `vigie-api` | `sender` des alertes CAP. |
| `VIGIE_GRID_FORECAST_DEG` | `0.05` | Grille (en degrés, environ 5 km) sur laquelle la position est arrondie avant d'interroger la prévision Open-Meteo et MET Norway. |
| `VIGIE_GRID_AIR_QUALITY_DEG` | `0.1` | Idem pour Open-Meteo Air Quality (environ 11 km). |
| `VIGIE_GRID_FLOOD_DEG` | `0.1` | Idem pour Open-Meteo Flood (GloFAS). |
| `VIGIE_GRID_MARINE_DEG` | `0.1` | Idem pour Open-Meteo Marine. |
| `VIGIE_EMMA_GEOMETRIES` | aucune | Chemin d'un GeoJSON compressé des formes MeteoAlarm `EMMA_ID` que vous avez fabriqué vous-même (`python tools/build_geometries.py --emma` ; par défaut `app/data/local/emma_id.geojson.gz` est lu s'il existe). Il permet de rattacher à un point les vigilances qui ne nomment leur zone que par des codes `EMMA_ID`. Non incluses dans le dépôt : voir [`SOURCES.md`](SOURCES.md). |
| `VIGIE_WMO_SOURCES` | `verified` | Quels services nationaux du registre de l'OMM sont lus pour les pays qui n'ont pas de source propre : `verified` (seulement ceux dont les conditions propres ont été lues et permettent de les relayer), `all` (tous ceux qui ne sont pas exclus, pour une instance que son propriétaire est seul à utiliser) ou `off`. Une valeur inconnue vaut `verified`. Voir « Plus de pays, par le registre de l'OMM ». |
| `VIGIE_NEAR_FAULT_KM` | `10` | Un séisme ou un événement volcanique à moins de cette distance (km) de la trace cartographiée d'une faille active nommée en est proche (0,1 à 100). Un réglage, pas un fait publié. |
| `VIGIE_NEAR_FAULT_MAX_DEPTH_KM` | `70` | Un séisme plus profond (km) n'est pas comparé aux failles (1 à 1000). |
| `VIGIE_NEAR_VOLCANO_KM` | `20` | Un séisme à moins de cette distance (km) d'un volcan qu'une source de dangers signale en est proche (0,1 à 500). |
| `VIGIE_NEAR_MAX_PLACES` | `5` | Au plus ce nombre de lieux sous un danger, du plus proche au plus lointain (1 à 20). |
| `VIGIE_WATCHED_POINTS_MAX` | `50` | Le nombre maximal de points surveillés que l'instance garde (1 à 500). Un de plus que la limite reçoit 409. |
| `VIGIE_QUIET_MAX_GAP_S` | `900` | Une source de dangers non lue depuis plus longtemps (secondes, 60 à 86 400) rompt la série de lectures sur laquelle repose le `quiet` d'un point surveillé. |
| `VIGIE_BUDGET_FRACTION` | `0.9` | Part du quota publié de chaque source (Open-Meteo 10 000 appels par jour, MET Norway, RainViewer) que l'API laisse passer. Au-delà, le cache est servi, marqué périmé. |
| `VIGIE_ENABLE_FIRMS` | `1` | `0` désactive les feux actifs : `GET /v1/fires` et `GET /v1/fires/grid` répondent 404, `fires_nearby` de `/v1/point` est nul et `GET /v1/health` liste la source dans `disabled_sources`. Activé par défaut : l'instance télécharge alors environ 56 Mo par jour quand quelqu'un demande les routes des feux (`docs/data-sources.md`). |
| `VIGIE_FIRMS_MAP_KEY` | aucune | Facultative. La `MAP_KEY` gratuite de [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/api/map_key/). Les fichiers statiques n'en demandent pas, donc sans elle rien ne manque ; avec elle, une source dont le fichier n'a pas pu être lu est lue par l'API de zone à la place (aujourd'hui et hier, 72 des 5 000 transactions que la clé permet en 10 minutes). La clé est dans l'URL de chaque requête de ce genre : l'API ne l'écrit jamais (erreurs, `last_error`, journaux), mais gardez-la hors du dépôt, de l'historique de votre shell et des commandes que vous partagez. Une valeur qui n'est pas faite de lettres et de chiffres n'est pas utilisée, et le journal le dit. |
| `VIGIE_FIRMS_SOURCES` | `VIIRS_NOAA20_NRT,VIIRS_NOAA21_NRT` | Les produits FIRMS lus : un seul, ou les deux (un seul divise le volume par deux). Le reste est ignoré (Suomi NPP s'arrête le 2026-11-02, MODIS a une autre structure). |
| `VIGIE_FIRMS_WINDOW_HOURS` | `48` | Le fichier lu : `24` ou `48` heures. C'est aussi jusqu'où `hours` peut remonter sur cette instance (au-delà, 422). Les fichiers de 24 heures coûtent environ les deux tiers des octets (36 Mo par jour contre 56). Toute autre valeur est ignorée. |
| `VIGIE_PREALERT_TR` | `0` | `1` lance le **pilote de pré-alerte non officielle pour la Turquie** ([ADR 0011](docs/decisions/0011-unofficial-pre-alert-turkey-shadow-pilot.md) et son avenant) : chaque nouveau run des données ouvertes de l'ECMWF (2 par jour, 144 requêtes au miroir de l'ECMWF sur AWS, environ 110 Mo) devient un niveau par province, enregistré dans la base et servi par la route **expérimentale** `GET /v1/experimental/prealert?lat=&lon=` (un essai pour des testeurs, **pas une vigilance officielle** ; 404 si coupé). Coupé, rien n'est téléchargé. Demande `eccodes`, facultatif (dans l'image Docker ; sinon `pip install .[prealert]`). |
| `VIGIE_FIRMS_REFRESH_MINUTES` | `60` | Durée pendant laquelle une lecture des feux est gardée avant la vérification suivante, de 10 à 360. FIRMS écrit ses fichiers une fois par heure : 30 minutes coûtent plus de requêtes (134 par jour contre 86) et pas un octet de plus (`docs/data-sources.md`). Une fois la lecture du démarrage faite, si personne ne demande, rien n'est lu. |

Aucune source ne demande de clé d'API (NASA FIRMS en accepte une, facultative : `VIGIE_FIRMS_MAP_KEY`). Une valeur de grille hors de 0,0001 à 10 degrés est ignorée (la
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
| tsunami.gov (liste des bulletins, requête conditionnelle) | sans objet | 1 393 (330 ko) |
| NOAA National Hurricane Center (tempêtes actives, requête conditionnelle) | sans objet | 286 (140 ko) |
| Volcans USGS | 212 | 144 |
| Flux MeteoAlarm (France et Islande : les pays de deux des cinq zones) | sans objet | 640 |
| Documents CAP de MeteoAlarm | sans objet | 0 |
| Vigicrues (Paris est en France) : lectures du flux, 2,2 Mo chacune, seulement quand une publication l'a changé (c'était 138, 310 Mo, tant qu'un tronçon est en orange ; 46, 103 Mo, un jour calme) | sans objet | 4 (9,1 Mo) |
| Vigicrues : coups d'œil à l'image de la publication, environ 330 octets chacun (un tronçon en orange : toutes les 5 minutes ; calme : 15) | sans objet | 240 (88 un jour calme) |
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
lecteurs des vigilances, une source n'est interrogée qu'une fois par durée de cache au plus (720, 288, 288 et 288
par jour pour le DWD, ECCC, MetService et Vigicrues, dont les requêtes sont des coups d'œil de 330 octets à une
image). `tests/test_national_sobriety.py` joue ces journées.

**Vigicrues** est la source lourde : son flux pèse 2,2 Mo, n'est pas compressé, n'a pas de validateur, ignore
`Range` et n'accepte aucun filtre, et aucun point d'accès plus léger ne porte le niveau. Le collecteur demande donc
(`HEAD`) l'image de la carte nationale que la même publication régénère, toutes les 15 minutes (5 tant qu'un tronçon
est orange ou rouge), et ne lit le flux que si l'image a changé, et au plus tard après 12 heures. Sur la journée
simulée des cinq zones par défaut, cela fait 4 lectures et 9,1 Mo (9,0 Mo un jour calme ; 6,8 Mo par jour une fois
lancé : les deux publications, à 10 h et à 16 h, et une lecture de sécurité) contre 138 lectures et 310 Mo (46 et
103 Mo en jour calme) avant. Une crue publiée à 16 h est servie au coup d'œil suivant : 16,5 minutes plus tard au
plus quand c'est calme, 6 en crue. [`docs/data-sources.md`](docs/data-sources.md) donne les mesures.

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
python tools/fake_instance.py               # une fausse instance dont vous choisissez les vigilances (pour essayer l'intégration Home Assistant)
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
  interroge chaque vraie source une fois (MeteoAlarm, les vigilances du NWS, le DWD, ECCC, MetService, Vigicrues, le
NHC et les centres d'alerte aux tsunamis compris). Une exécution rouge
  veut dire qu'une source a changé de format, retiré un point d'accès, ou envoyé un mot de niveau ou de
  phénomène que les tables ne connaissent pas.
- `home-assistant.yml` : l'intégration Home Assistant (`custom_components/vigie`), seulement quand elle change, et
  chaque semaine : ses tests (`tests_ha/`, dans un environnement à part : Python 3.14 et la version de Home Assistant
  qu'ils fixent), hassfest et la validation HACS.
- `publish-image.yml` : pousse l'image Docker vers `ghcr.io/axellum/vigie-api` quand une étiquette `v*` est
  poussée.

## Sources de données

| Domaine | Source |
| --- | --- |
| Séismes | USGS, EMSC / SeismicPortal, GDACS |
| Catastrophes | GDACS, NOAA NWS, USGS Volcano Hazards Program, NOAA National Hurricane Center (cyclones tropicaux), NOAA U.S. Tsunami Warning Centers (messages de tsunami) |
| Vigilances météo officielles | MeteoAlarm (EUMETNET, 39 pays d'Europe), Deutscher Wetterdienst (Allemagne), Environnement et Changement climatique Canada, MetService (Nouvelle-Zélande), NOAA NWS (États-Unis), Vigicrues (crues des cours d'eau de France), les services nationaux du registre du Severe Weather Information Centre de l'OMM (7 lus par défaut : Algérie, Argentine, Barbade, Jamaïque, Mexique, Philippines, Espagne) |
| Failles actives (`near_places`) | GEM Global Active Faults Database, les traces qui ont un nom (CC BY-SA 4.0, embarquée) |
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
- [`docs/home-assistant.md`](docs/home-assistant.md) : l'intégration Home Assistant.
- [`docs/decisions/`](docs/decisions) : décisions d'architecture.
- [`CHANGELOG.md`](CHANGELOG.md), [`AGENTS.md`](AGENTS.md) (guide pour les agents de code IA).
