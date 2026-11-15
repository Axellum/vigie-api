# 0024. Expanded coastal spot catalog, 360-degree geometric shelter, SHOM 1/50 000 seafloor categories, and Open-Meteo grid-cell deduplication

- Status: accepted
- Date: 2026-11-15
- Number: 0024

## Context

The initial `app/data/surf-spots.json` catalog listed 51 spots along the Atlantic and Channel coasts (`facing_deg` only, `bottom=null`, every spot queried independently to Open-Meteo). Three concrete limitations emerged:

1. **Missing coastal shelter geometry**: `facing_deg` describes only the local normal vector over 400 m on each side of the beach. It cannot see islands, peninsulas, rocky headlands or harbour breakwaters blocking incoming swells (e.g., Cézembre island and Pointe du Décollé off Saint-Malo Plage de Rochebonne, or narrow estuary approaches in Charente-Maritime and Gironde).
2. **Under-covered beaches and missing open seabed sediment**: Long French beaches (`natural=beach` polygons) whose centre fell more than 1 km from an OSM surf school were dropped even when a surf club sat right at one end; distinct named beaches closer than 500 m were collapsed into one entry; oceanic supervised beaches without an OSM `sport=surfing` tag in Nouvelle-Aquitaine were omitted despite open data from GIP Littoral; and `bottom` stayed `null` everywhere even though SHOM publishes *Natures de fond au 1/50 000* under Licence Ouverte 2.0. Meanwhile, no open source provides curated `best_tide` or `best_swell_from_deg` windows.
3. **Nearshore wave resolution vs Open-Meteo call budget**: Expanding from 51 to 171 spots would otherwise triple upstream Open-Meteo calls during `GET /v1/surf/overview` and hourly spot refreshes, even though neighbouring beaches inside the ~8 km MFWAM grid share the exact same `(latitude, longitude)` grid cell returned by Open-Meteo.

## Decision

1. **Expand `tools/build_spots.py` with strictly open, commercially reusable datasets**:
   - Measure surf-school proximity against Overpass beach polygon/way boundary geometry (`out center geom tags`), not just the polygon centroid, while anchoring each spot point closest to the school so `facing_deg` hits the right section of beach.
   - Lower `MERGE_DISTINCT_KM` to `0.25` km while preserving every legacy `id` from prior `surf-spots.json` exports (so existing bookmarks, links and all 20 pairs in `app/data/surf-calibration.json` stay bit-for-bit stable).
   - Integrate **GIP Littoral — *Plages de Nouvelle-Aquitaine*** (`nature_baignade=Océanique`, Licence Ouverte 2.0): enriches `beach_surface` and sets `lifeguarded` on matched spots, adds missing oceanic supervised beaches with `evidence[].kind="beach"`, and filters new `beach`-only entries against coastline facing + non-zero open-water sectors aligned within 90° of `facing_deg`. Meanwhile, OSM stays strict (`EVIDENCE_KM = 1.0`, no speculative expansion in Normandy/Hauts-de-France beyond mapped `sport=surfing` evidence).
   - Integrate **Shom — *Natures de fond au 1/50 000*** (`HOM_GEOL_NATURES_FOND_50`, Licence Ouverte 2.0, WFS `NDF_BDD_WLD_WGS84G_WFS:natures_fond_50000`) queried 80 m offshore along `facing_deg` (within 350 m) to populate `bottom` (`sand`, `rock`, `gravel`, `mixed`, `mud`) and raw `seafloor_shom`. Unmapped polygons stay `null`.
   - Keep `best_tide` and `best_swell_from_deg` as `null` (never scraped or inferred).
2. **Compute 360-degree straight-line coastal shelter (`open_water_sectors`, `open_water_width_deg`) and per-hour `swell_sheltered`**:
   - At build time, cast rays every 5° out to 20 km from `OFFSHORE_PROBE_KM` (80 m offshore, falling back to 150 m in enclosed coves) against OSM coastlines (including cross-border Basque/Channel coasts and islands, ignoring rocks under `0.15 km`) and harbour breakwaters/groins (`man_made=breakwater|groin|groyne`).
   - At run time (`app/services/surf.py`), flag `SurfHour.swell_sheltered` (`true` when primary swell arrives outside `open_water_sectors`, `false` inside, `null` when either is missing), without silencing or altering the raw MFWAM swell numbers or existing ratings.
3. **Deduplicate Open-Meteo requests by observed `(model_lat, model_lon)` grid cell (`app/collectors/surf.py`)**:
   - Record `(model_lat, model_lon)` returned by Open-Meteo (`parse_swell` and `parse_wind`) per spot id.
   - During `SurfForecastCollector.fetch_all()`, send one coordinate per distinct known grid cell and distribute the parsed series to all spots mapped to that cell (while keeping `swell_cell` untouched so `surf-calibration.json` still distinguishes Ondres/Biarritz from Guéthary/Saint-Jean-de-Luz as ADR 0023 requires). If a cell coordinate changes for a representative, invalidate peers of the old cell so the next refresh re-probes them individually.
   - During single-spot `fetch_swell()` / `fetch_wind()`, reuse fresh cache entries from any peer sharing the same grid cell (`_from_peer_cell`).

## Measured outcomes (2026-11-15)

- **Spots**: 51 → **171** (`app/data/surf-spots.json`). Every legacy ID and all 20 calibration spots/grid cells in `app/data/surf-calibration.json` remain unchanged and matched.
- **Seafloor (`bottom`)**: 136/171 spots mapped from SHOM 1/50 000 (`mixed`: 76, `sand`: 56, `rock`: 3, `gravel`: 1); 35 spots outside polygons stay `null`.
- **Supervised Nouvelle-Aquitaine beaches (`lifeguarded`)**: 140/171 spots carry `lifeguarded: true` (and `beach_surface` where missing); 31 outside GIP coverage stay `null`.
- **Saint-Malo Rochebonne verification**: `facing_deg=293`, `open_water_sectors=[[265, 275], [285, 0]]`, `open_water_width_deg=95` (Cézembre island shadow captured between 275° and 285°).
- **Live Open-Meteo grid compression over all 171 spots**: `meteofrance_wave` collapses **171 → 78 unique grid cells (-54.4%)**; `forecast` wind collapses **171 → 144 unique grid cells (-15.8%)**. At steady state (8 refreshes/day × 222 cells = 1,776 weight calls/day), total overview + individual traffic stays far below the 10,000/day Open-Meteo free quota.
- **Nearest coastal weather stations**: rebuilding `app/data/meteofrance-coastal-stations.json` against the 171 spots adds 3 stations (**77 → 80**), keeping every spot within 35.35 km of a coastal Météo-France station (worst case `plage-de-contis` to `40134001` `LABOUHEYRE DFCI`).

## Not measured / not claimed

- `open_water_sectors` and `swell_sheltered` are straight-line geometric line-of-sight visibility at 5° / 20 km against OSM vectors — **not** a shallow-water SWAN/WW3 wave refraction, diffraction, or shoaling model.
- SHOM `1/50 000` sediment polygons characterize regional geology (`sand`/`rock`/`mixed`), not micro-reef sandbank shifts. Not for navigation.
- Northern France (Normandy, Hauts-de-France) coverage remains limited by sparse `sport=surfing` OSM elements and absence of a GIP Littoral equivalent there.
- Open-Meteo free API commercial restriction remains unchanged (`SURF` commercial status stays `forbidden` until Open-Meteo is replaced or licensed commercially), though the new static layers (`osm_spots`, `gip_littoral_plages`, `shom_ndf`) are all `allowed`.
