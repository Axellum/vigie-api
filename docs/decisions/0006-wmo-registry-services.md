# 0006. More countries from the WMO registry: one source a country, only the terms that were read

- Status: accepted (the terms of the 21 services marked "unverified" are questions for the owner: see "Open points")
- Date: 2026-10-20

## Context

[ADR 0003](0003-national-source-preferred-rivers-are-lines.md) fixed the rule for the countries that already had a source:
MeteoAlarm for 39 European countries, the NWS, and four national collectors (Germany, Canada, New Zealand, France's rivers).
The World Meteorological Organization keeps a registry of the national services that publish CAP warnings, the Severe Weather
Information Centre (SWIC, <https://severeweather.wmo.int>). Read on 2026-10-20, its `sources.json` listed **140 services in 135
countries**, all marked `operating` and all `official`. The status says nothing about whether a feed answers: of the 101 services
whose feeds were asked that day (all but the 39 records of MeteoAlarm, the United States, Canada, Germany and New Zealand), **88**
answered with at least one feed and 13 did not.

The services write CAP in their own ways (one document per language or several in one, a colour in a parameter or only the
CAP severity, a polygon, a circle or only codes, `Update` and `Cancel` that name their predecessors). The WMO says the warnings
it lists are free to reuse citing the issuer, and that each authority keeps its rights
(<https://severeweather.wmo.int/note.html>): the terms of **each service** are what count.

Questions: which source answers for a country that has several; how is a service that does not answer kept from looking like
"nothing in force"; what may an instance read by default given the terms; what does it cost the 100 or so servers behind the
feeds; how is a warning that names only codes kept from being drawn where it is not.

## Decision

1. **The registry is the list of services.** It is read once a day with `If-Modified-Since`
   (`app/collectors/swic_registry.py`), and a snapshot of it is bundled (`app/data/swic_registry.json.gz`, 140 services, without
   the names and e-mail addresses of the people in the records, rewritten by `tools/build_swic_snapshot.py`) so that the
   countries an instance serves are known before the first read and when the WMO does not answer.
2. **One source a country, as in ADR 0003.** A country that has a collector (the 39 MeteoAlarm countries, the United States,
   Germany, Canada, New Zealand) keeps it: the registry never replaces it and its services for those countries are not asked
   (43 of the 140 records). A registry service answers for a country that has none: **95 services in 94 countries** are
   readable. A country with two services (India) gets one `coverage[]` line for each.
3. **One exception: Spain.** MeteoAlarm's feed for Spain, read on 2026-10-20 at 16:51 UTC, had **216 entries, none with a
   polygon**, all naming `EMMA_ID` codes whose shapes are not bundled (see `SOURCES.md`): none of them can be matched to a
   point. AEMET's own feed, the same day, had **34 items with polygons**. So Spain is answered by AEMET's feed and MeteoAlarm
   stands in, flagged as a stand-in, only if AEMET gives no answer (the same mechanism as Germany and the DWD). The other
   MeteoAlarm countries that have a registry record were looked at and keep MeteoAlarm: Norway (the MeteoAlarm feed already
   carries polygons; the two Norwegian services are not needed), Iceland (the national feed answered HTTP 204 with no body), the
   United Kingdom (the record is the Department of Disaster Management of the British Virgin Islands, not a UK service), Germany
   (its collector). The exception is a
   table, `COUNTRY_OVERRIDES` in `app/collectors/swic_terms.py`; a second one needs the same kind of evidence.
4. **The issuer's feed first, the WMO's copy to complete it.** Each service is read from the addresses of its `capAlertFeed`
   (one per language for 51 services), then, **for a service on a national host only**, from the WMO's own copy
   (`severeweather.wmo.int/v2/cap-alerts/<id>/rss.xml`) to add what the feed lacks. The copies exist for 1 of the 260 addresses
   asked on 2026-10-20 (the other 259 answered HTTP 404), so a miss is remembered for 24 hours and three misses in a row pause the
   copy for an hour. The two are merged by CAP `sender` and `identifier`; `references` are followed (an `Update` or a `Cancel`
   removes what it replaces); a CAP document is read **once**, whatever feed lists it. Links are followed only on the site of the
   feed (never to an address or to `localhost`). The XML is parsed without a DTD or entity.
5. **A service that does not answer is not "nothing in force".** Each service has its own `coverage[]` line
   (`source: swic`, `source_id`): `ok`; `stale` (the last good answer is served and marked); `unavailable` (nothing was ever read).
   A service that is `unavailable` is **silent** for the push of changes: what it said last still stands, nothing is announced
   as ended. A circuit per service (three failures: asked again after 60 s, then twice as long up to an hour; HTTP 4xx: after an
   hour) keeps one bad feed from closing the others.
6. **Placed warnings and counted ones.** A warning with a polygon (or a circle, drawn as a 48-point polygon, cut at the date
   line) is matched to a position. A warning that names only codes (the Philippines' SAME codes, India's district codes) is
   listed by `country=` and **counted in `warnings_not_matchable`**, never drawn where it is not; the answer for a position says
   so. The shape of `/v1/vigilances.geojson` is unchanged: the identifier and the geocodes are the issuer's.
7. **Fields of the API are derived, and say so.** `status` from `msgType`; `level` from a colour the issuer writes in its own
   parameter (English and Spanish colour words, one colour exactly), else from the CAP severity (`Moderate` yellow, `Severe`
   orange, `Extreme` red; `Minor` and `Unknown` give **no level**: the services do not agree on what a minor alert is);
   `phenomenon` from the `event` words (English, Spanish, Portuguese, French; every language of the alert must agree). **A
   word in no table gives `null`, never a guess.** `derived` lists the fields that were computed. The issuer's words (`event`,
   `texts` in every language, `level_original`) stay intact. A `Test` status, a scope other than `Public`, an urgency `Past`
   and a certainty `Unlikely` are not warnings.
8. **Licences: only what was read.** A service is **verified** when its own terms page allows the reuse, or when its own feed
   names an open licence (CC BY 4.0) on a host the issuer runs, and no restriction was found in its terms. Everything else is
   **unverified**: silence, a page that could not be read, terms that restrict, and the bare line "public domain". That line is
   the WMO's own feed template and the Alert Hub's sample: **15 of the 25** services whose feed is outside the WMO's bucket and
   answered carry it, among them four whose issuer's site terms restrict copying. Of the 28 services whose terms were looked at,
   **7 are verified** (Argentina, Barbados, Algeria, Spain, Jamaica, Mexico, the Philippines). `VIGIE_WMO_SOURCES=verified` is the
   default (these 7), `all` reads every service that is not excluded (for an instance only its owner uses), `off` none. An unknown
   value means the default. **Hong Kong and China (CMA) are never read**, whatever the setting: the owner's decision for Hong Kong's
   contradictory terms, and a feed of 1,000 items (1 MB, about 89 KiB a document) with no terms found for China. Every record is a row of
   `SOURCES.md` with the address that was read; the credit of a warning names the issuer and the SWIC, and says "reuse terms not
   verified" for an unverified service.
9. **Sobriety.** A service is asked at most once every 5 minutes (`SOURCE_TTL_S`; the scheduler's cycle is 90 s, so in practice every 6 minutes: 240 requests a day). Of 149 feeds asked for their
   headers on 2026-10-20, 17 sent a `Cache-Control` and 10 an `Expires`; the shortest lifetime announced was 60 s (Spain, Kuwait,
   Singapore, Thailand, Uruguay, Argentina 60 to 180 s), so 5 minutes is five times the shortest and no feed asked for more
   often; the others announced nothing, `no-store`, or a lifetime that is a server default (Mexico: one year). A feed that cannot be asked conditionally (12 of 150 answered with
   no validator) waits 5 minutes per 64 KiB (rounded up) that it weighs on the wire, up to 30 minutes. A feed with a validator is asked with `If-None-Match`
   / `If-Modified-Since` (a `304` costs a few hundred bytes). Items older than 14 days are not read; at most 60 new documents per
   read of a service; indexes are capped at 2 MB and 1,500 items, documents at 1 MB; error pages are not read. A ceiling of
   **50,000 requests a day for the whole set** (`wmo_swic` in `app/collectors/budget.py`) is ours, not published by anyone: it
   is there so that a loop cannot hammer a hundred servers. Measured by `tests/test_swic_sobriety.py` on the recorded profile of
   every feed: **36,924 requests and 51 MB a day** for one followed point in every one of the 94 countries (`all`, the worst
   case); **2,010 requests and 10.6 MB a day** for the 7 verified services. Neither depends on the number of clients.
10. **Countries are listed and bordered.** `GET /v1/vigilances/countries` lists the countries the instance serves with its
    current settings (the existing ones included, the tsunami source under the United States and Canada), their sources, and
    whether their licence is verified. Every country a service answers for has a border in `app/data/countries.geojson.gz`
    (Natural Earth, public domain; 135 countries), checked by a test: a country without a border could never be asked by a position.

## Consequences

- Six countries more by default (Argentina, Barbados, Algeria, Jamaica, Mexico, the Philippines) and Spain with polygons; 87 more
  with `VIGIE_WMO_SOURCES=all`. A country is added by asking its authority, reading its terms and adding a record
  (`app/collectors/swic_terms.py`): the code does not change.
- The tables of levels and phenomena cover English, Spanish, Portuguese and French. Services that write their events in other
  languages (Thai, Russian, Arabic, Kazakh...) give a level only when they set a CAP severity or a colour in a parameter, and
  no phenomenon: the words stay in `event`.
- The weekly check (`tools/check_live_feeds.py`) fails when the registry loses services, when a verified service's feed or country
  moved, when its licence line is gone from its feed, or when a sample of services cannot be read.
- 21 services are read only by an instance whose owner turns `all` on; the answers say which.

## Open points

- Terms of the 21 unverified services: ask the authorities (Singapore, Thailand, Kuwait and Trinidad and Tobago publish site terms
  that restrict copying; the others publish nothing found). No message was sent.
- A public instance with `all` would redistribute warnings whose terms are not known: keep the default.
- The 50,000 requests a day ceiling is a choice; the real usage of an instance is far below it.
- Spain's MeteoAlarm copies are in the answer only as a fallback; the choice is easy to revert (`COUNTRY_OVERRIDES`).
