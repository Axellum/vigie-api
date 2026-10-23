# 0009. Every country has a border and a reason; an optional admin token for every write

- Status: accepted (five points are questions for the owner: see "Open points")
- Date: 2026-10-23

## Context

Two follow-ups of the lots that opened the API to more countries and to the public, in one change because both were left over by the
same review.

**Borders.** `app/data/countries.geojson.gz` held the 135 countries a source answers for ([ADR 0006](0006-wmo-registry-services.md), point
10). A position in Japan, Australia or China fell in no border and got `countries: []` and one generic sentence ("No official warning source
covers this point"), with no name of country and no reason; the website that shows the answer could not say "Japan: the JMA is not read
because...". Worse, land near the border of a country that had a border was given to it when it was within about 25 km (Tashkent was read as
Kazakhstan, the "nearest country at sea" rule applying on land).

**Writes.** Six routes change something: `POST /v1/zones`, `DELETE /v1/zones/{id}`, `POST /v1/scheduler/trigger`, `POST` and
`DELETE /v1/watched-points`, `POST /v1/simulate`. They have no authentication; two are off by default
([ADR 0008](0008-sensitive-points.md), point 7) and the documents say to keep the instance off the Internet or behind a reverse proxy. The
repository is public, and instances will be put where others can reach them. ADR 0008 left "a key (authentication of the API)" to
whoever runs one.

Questions: how big is a border for every country and what does it cost; what is said for a country that is not read, and how is a
reason kept from being a guess; how does a client get the list without breaking those that read it; how can every write be asked for a
secret without a route being able to escape, and without changing anything for the instances that rely on their network.

## Decision

1. **Every country of Natural Earth has a border.** Same tool (`tools/build_geometries.py`), same input (`ne_10m_admin_0_countries`, public
   domain), same simplification (0.01 degree, 3 decimals). 244 borders, **987 KiB gzipped instead of 701 KiB**; read in **about 120 ms at start
   instead of 79 ms** (median of five on the author's PC); a point found in **under 0.2 ms** (Paris 0.16, Tokyo 0.05). Rules of the build, each
   tested on a miniature of the file: a country drawn in several features is one border named after the largest; a territory keeps its own ISO
   code (`PR`, `GL`, `HK`...); the overseas departments of France, which Natural Earth draws inside France while the French feed covers the
   metropole only, are cut off under their own code (`GF` `GP` `MQ` `RE` `YT`, by a bounding box per department); Somaliland and Northern
   Cyprus and the buffer zone of Cyprus (no ISO code in the file) are joined to Somalia and Cyprus; **ten places with no code are not
   written** (the British sovereign base areas, the Guantanamo Bay lease, disputed glaciers, banks and reefs) and the tool names them. The
   name is Natural Earth's `NAME_EN`. The file is byte-for-byte the same on a second build.
2. **A reason, written only when it was read.** `reason_code` is one of seven stable codes a client can translate, `reason` one English
   sentence:
   - `no_source`: no official warning feed is read for the country and nothing more is known. **The default** (104 of the 196 countries an
     instance reads nothing for by default). It says neither that the country has no service nor that its terms forbid anything.
   - `licence_unverified`, `terms_restrict`, `terms_contradictory`: a service of the WMO registry that the instance does not read, from the
     record of its terms in `app/collectors/swic_terms.py` (a new field `restriction`, checked against the record's own words by a test); a
     service that no record looked at is `licence_unverified` and says that its terms were not looked at.
   - `redistribution_forbidden` (Australia), `law_restricts` (Japan): countries that have no service in the registry and were set aside for
     a stated reason, with the evidence in `app/services/uncovered.py` and a row of `SOURCES.md` ("Countries with no source").
   - `source_off`: a source exists and the instance turned it off (MetService, or the whole registry with `VIGIE_WMO_SOURCES=off`).
   Hong Kong (`terms_contradictory`) and China (`licence_unverified`) keep the reasons that exclude them whatever the setting. Korea is
   `licence_unverified`: nobody read its terms (the plan of 2026-10-20 lists it as not verified). A test keeps the table of the excluded
   services on the same ids as `EXCLUDED`, so a new exclusion cannot go without its reason. Measured by default: 196 countries
   without a source, 104 `no_source`, 81 `licence_unverified`, 6 `terms_contradictory`, 3 `terms_restrict`, one each of `redistribution_forbidden`
   and `law_restricts`.
3. **Where it appears.** `GET /v1/vigilances` has `uncovered: [{country, name, covered: false, reason_code, reason}]` for each country of the
   position (or within about 2 km of it, or 25 km at sea) that nothing is read for, also next to countries that are read; the `note` names them
   when nothing at all is read ("Japan (JP): ..."). A position in no country (at sea, further than about 25 km from a coast) says exactly that.
   The same list is in the `metadata` of the GeoJSON, and the 404 of `?country=JP` gives the reason. The sentence of a registry service
   names it and says how it would be read (`VIGIE_WMO_SOURCES=all`); a registry service never counts as a reason for a country that has a
   collector (`_covered`), since it would not be read even with `all`.
4. **The countries route keeps its shape.** `GET /v1/vigilances/countries` is a bare list that clients and a test read field by field.
   Wrapping it in an object, or adding fields to its entries, would have broken them; adding the other countries to the same list would have
   made "the countries this instance serves" mean something else. So: **`?uncovered=true`** (default false) adds every other country to the
   list, sorted by code with the rest; the entries of the countries with a source are **exactly** what they were (`country`, `sources`,
   `licence_verified`, no new field), the others are `{country, name, covered: false, reason_code, reason}`. Without the parameter the answer is
   byte-for-byte the former one. Considered and not chosen: an object `{countries, uncovered}` (breaks every reader of the list); a route of its
   own (the site asked for the same route); `covered: true` on the served entries (changes their shape).
5. **The admin token is a middleware on the method, not a dependency on each route.** `VIGIE_ADMIN_TOKEN` set: every HTTP request whose
   method is **not** `GET`, `HEAD` or `OPTIONS` needs `Authorization: Bearer <token>`, else 401 with `WWW-Authenticate: Bearer` and a message
   that says what to send. Fail-closed on the method (`POST`, `PUT`, `PATCH`, `DELETE`, and anything else), so **a route added tomorrow is covered
   with no line written for it**, a route hidden from the OpenAPI document too, and a write to a path that does not exist answers 401, not
   404, so the routes cannot be listed without the token. A dependency on each route (the pattern of the switches) would have to be remembered by
   whoever adds a route, and it runs only on the routes it was put on, after the JSON body is decoded (measured on a minimal application of this
   FastAPI: a malformed body answers 422 before the dependency is reached, and an unknown path 404). The middleware runs before the body is read:
   a request without the token learns nothing about what a valid body is nor whether an id exists. It sits inside the CORS middleware, so a
   browser can read the 401.
6. **Not set, nothing changes.** Unset or blank (whitespace is trimmed), the API trusts its network as before, and `docker_smoke.sh`, the
   dashboard and the integration need nothing. Read at each request, like the other switches. The token **stacks with the switches** and comes
   first: without it a disabled route says 401 (not that it is off); with it, a disabled route still says 403 (the token does not turn a route on).
7. **The token is a secret and is treated as one.** Compared in constant time (`hmac.compare_digest` on the SHA-256 of each side, so the length
   of the real token is not told either). It is never written: not in a log line, not in an answer (the messages say what to send, never what was
   sent), not in the OpenAPI document, not in `GET /v1/health`, which only has `admin_token_set: true`. A test sends a recognisable token and a
   recognisable wrong one on every kind of request and looks for both in every body, header and captured log. The value comes from the
   environment only; none is in the repository.
8. **What the token does not cover, on purpose.** The reads, `HEAD` and `OPTIONS` (a CORS preflight must pass). The WebSocket `/v1/stream`: a
   client can ask it to follow positions, which lives in memory for the length of the connection and changes nothing on the instance. The Home Assistant
   integration (`custom_components/vigie`) only reads (`GET` routes and that stream) and needs nothing; a test checks that it sends no write
   request, so that this stays true.
9. **A test enumerates the routes and fails if one escapes.** `tests/test_admin_token.py` walks `app.routes`, **into the routers that were
   included** (FastAPI 0.151 keeps each as one lazy route, so a plain walk of `app.routes` would not have seen `/dashboard`, nor any route
   added through a router) and into mounted applications, and for every route with a method other than a read: 401 without the token, 401 with
   a wrong one, 401 with another scheme, and not 401 with the right one. What counts as a read is written in the test, not read from the code under
   test, so the two cannot be loosened together. The same file adds a router with `PUT`, `PATCH`, a prefix and an `include_in_schema=False`
   `DELETE`, and sees them refused. As a control, the middleware was made to let `DELETE` through: the tests of the two `DELETE` routes, of the route added later and of the
   refusal before the body failed.
10. **The documents say it.** The OpenAPI document has the `AdminToken` scheme and, on each write operation, the optional security and the 401
    (`[{}, {AdminToken}]`: not asked on an instance with no token); both READMEs, `docs/architecture.md`, `docs/data-sources.md`, `SOURCES.md` and
    `AGENTS.md` (rules 16 and 17). The dashboard asks for the token once (a `prompt` on the first 401), keeps it in `sessionStorage` (the tab, not
    the disk) and sends it with its two writes.

## Consequences

- The data file is 286 KiB bigger and takes 40 ms more to load once; the cost of a lookup did not change (a bounding box test comes
  first). The 15 borders (of 244) that have a touching self-intersection after the rounding of the coordinates (9 of 135 before) are measured
  on all the same; a test asks for a shape and a point inside it, not for a valid polygon.
- Land near a border is no longer given to the neighbour that had a border (Tashkent: `UZ`, no longer `KZ`), and the two places of the tests that
  said `[]`, Tokyo and Pointe-a-Pitre, now say `JP` and `GP`.
- `GET /v1/vigilances` has one more field, `uncovered`, `[]` by default; `note` is longer for a position nobody reads. The Home Assistant
  integration shows `note` (cut to 500 characters) and ignores the rest.
- An instance with a token needs its clients to send it: `curl -H "Authorization: Bearer $VIGIE_ADMIN_TOKEN" -X POST ...`; in a container or a
  service, the variable is set in the environment of the process (`docker run -e VIGIE_ADMIN_TOKEN`, a systemd `EnvironmentFile`, an `.env`
  that is not committed).
- Two rules for whoever adds code (`AGENTS.md`): a route needs nothing to be covered and none is exempted; a reason is written for a
  country only with what was read.

## Open points

- **Australia.** The Bureau's pages answered HTTP 403 to an automated reader on 2026-10-20; the wording "no redistribution without the paid status of registered user" rests on the research of 2026-10-20 (the plan), not on a page read again. A
  search summary of the Bureau's RSS terms said "personal and non-commercial use only", and of a Data Licence Agreement with fees; neither was
  read. To confirm in a browser before the sentence is quoted anywhere.
- **Japan.** Article 23 of the Meteorological Service Act was read in the English translation: no one but the JMA may give warnings, except in
  cases set by Cabinet Order. The Order was not read, and whether passing on an unchanged JMA warning is "giving" one was not examined: the
  sentence says only what the law reserves and that this instance reads nothing of the JMA. The JMA's own XML (not CAP) is from the research
  of 2026-10-20, not read again.
- **Territories.** Puerto Rico, Guam, French Guiana... are answered on their own and say `no_source`: the source of the country they belong to is
  not asked for them (it never was; they had no border). The NWS publishes for the United States territories and Météo-France for its overseas
  departments, so a position there could be answered with its country's source. That would add warnings where none were served: a decision, not a
  fix.
- **No limit on wrong tokens.** A wrong token is refused in constant time and nothing more: no delay, no lock-out, no count. The strength of the
  token is the operator's (`python -c "import secrets; print(secrets.token_urlsafe(32))"`), and a reverse proxy can limit the rate.
  Behind plain HTTP the token can be read on the network: TLS in front of any instance that is not on a trusted network.
- **The reads.** The token covers writes only. The list and the status of the watched points remain readable by whoever reaches the API
  ([ADR 0008](0008-sensitive-points.md)); protecting some reads would be another decision.
