"""Check that the real providers still answer in the format the parsers understand.

Each source is asked **once**, with the real collector (its URL, its query, its parser), and the answer
must be understood: at least one item parsed, or a valid empty answer (GDACS answers HTTP 204 when no
event matches; a feed can be legitimately empty). It is NOT part of ``pytest`` (the tests never touch the
network); the weekly workflow ``.github/workflows/live-feeds.yml`` runs it, and so can you::

    python tools/check_live_feeds.py
    python tools/check_live_feeds.py --only usgs_eq open_meteo_hd

Outcomes, one line per source:

- ``ok``: the answer was understood.
- ``empty``: a valid empty answer (nothing to parse, nothing that could have been missed).
- ``FAIL``: the provider answered but the parser cannot read it (a format change, a removed endpoint,
  HTTP 400 / 404 / 410, items the parser drops although the answer has some). The exit code is 1.
- ``unreachable``: no answer (timeout, connection error, HTTP 403 / 429 / 5xx). Reported, but it says nothing
  about the format, so it does not fail the run (use ``--strict`` to make it fail).
- ``skipped``: nothing was asked, because the source is turned off on this machine (NASA FIRMS: ``VIGIE_ENABLE_FIRMS=0``).
  It never fails the run, not even with ``--strict``. NASA FIRMS needs no key: the tool reads one static file of one source (8 to 15 MB);
  with ``VIGIE_FIRMS_MAP_KEY`` set it also reads one world day of the area API (36 of the 5,000 transactions that FIRMS allows in
  10 minutes), the way a source whose file cannot be read is read.

It sends one request per source and waits between them (MeteoAlarm: the feed of France, then one CAP
document it links to). Set ``VIGIE_CONTACT`` (an e-mail or a URL): MET Norway and NOAA require an identifying
contact.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Optional

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app import clock  # noqa: E402
from app.collectors.aurora import (  # noqa: E402
    SWPCAlertsCollector,
    SWPCKpForecastCollector,
    SWPCOvationCollector,
    SWPCSolarWindCollector,
)
from app.collectors import health as health_feeds  # noqa: E402
from app.collectors import travel as travel_feeds  # noqa: E402
from app.collectors import travel_codes  # noqa: E402
from app.collectors.base import build_user_agent  # noqa: E402
from app.collectors.disasters import GDACSCollector, NOAANWSCollector  # noqa: E402
from app.collectors.dwd import DWDCollector  # noqa: E402
from app.collectors.eccc import ITEMS_URL, ECCCCollector, parse_items  # noqa: E402
from app.collectors.eccc import LEVELS as ECCC_LEVELS  # noqa: E402
from app.collectors.eccc import STATUSES as ECCC_STATUSES  # noqa: E402
from app.collectors.eccc import is_ended as eccc_is_ended  # noqa: E402
from app.collectors.firms import FIRMSCollector, FIRMSError, parse_csv, utc_dates  # noqa: E402
from app.collectors.firms import load_settings as load_firms_settings  # noqa: E402
from app.collectors.flood_air import (  # noqa: E402
    OpenMeteoAirQualityCollector,
    OpenMeteoFloodCollector,
    USGSVolcanoCollector,
)
from app.collectors.marine import OpenMeteoMarineCollector  # noqa: E402
from app.collectors.meteoalarm import (  # noqa: E402
    ATOM_ACCEPT,
    CAP_ACCEPT,
    COUNTRY_FEEDS,
    FEED_URL,
    MeteoAlarmCapCollector,
    MeteoAlarmCollector,
    MeteoAlarmFeed,
    parse_cap,
    parse_feed,
)
from app.collectors.metservice import COLOUR_PARAMETER as METSERVICE_COLOUR  # noqa: E402
from app.collectors.metservice import EVENTS as METSERVICE_EVENTS  # noqa: E402
from app.collectors.metservice import MetServiceCollector  # noqa: E402
from app.collectors.nhc import NHCCollector  # noqa: E402
from app.collectors.seismic import EMSCCollector, USGSCollector  # noqa: E402
from app.collectors.space_weather import NOAASWPCCollector  # noqa: E402
from app.collectors.swic import SwicCollector  # noqa: E402
from app.collectors.swic_registry import SwicRegistryCollector, bundled_registry  # noqa: E402
from app.collectors.swic_terms import TERMS as WMO_TERMS  # noqa: E402
from app.collectors.swic_terms import is_eligible, terms_of  # noqa: E402
from app.collectors.tsunami import TsunamiCollector, parse_message  # noqa: E402
from app.collectors.vigicrues import SIGNAL_URL as VIGICRUES_SIGNAL_URL  # noqa: E402
from app.collectors.vigicrues import VigicruesCollector  # noqa: E402
from app.collectors.weather import MetNorwayCollector, OpenMeteoCollector, RainViewerCollector  # noqa: E402
from app.services.geoindex import default_countries, default_geometries, localization_of  # noqa: E402

# Fixed points: Paris for the land sources, the Bay of Biscay for the sea.
LAND = (48.8566, 2.3522)
SEA = (46.0, -3.0)

# MeteoAlarm: France, whose warnings all carry a code the bundled shapes know (and which has warnings most days).
WARNING_COUNTRY = "FR"

# An HTTP status that means "the endpoint or the query is wrong now", not "the provider is busy".
FORMAT_STATUSES = {400, 404, 405, 410, 422}


@dataclass(frozen=True)
class Result:
    """The outcome of one check."""

    source: str
    outcome: str  # "ok", "empty", "FAIL", "unreachable" or "skipped"
    detail: str


Check = Callable[[httpx.AsyncClient], Awaitable[Result]]


def _features(payload: Any) -> list[Any]:
    """The ``features`` list of a GeoJSON answer; raises ``ValueError`` when the answer is not one."""
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        raise ValueError("the answer has no 'features' list")
    return features


async def check_usgs(client: httpx.AsyncClient) -> Result:
    """USGS earthquakes: when the feed lists events, the parser must keep some."""
    collector = USGSCollector()
    resp = await collector._get(client, collector.FEED_URL)
    resp.raise_for_status()
    payload = resp.json()
    raw, parsed = len(_features(payload)), len(collector.parse_geojson(payload))
    if raw == 0:
        return Result("usgs_eq", "empty", "the feed lists no event")
    if parsed == 0:
        return Result("usgs_eq", "FAIL", f"{raw} events in the feed, none understood")
    return Result("usgs_eq", "ok", f"{parsed} of {raw} events parsed")


async def check_emsc(client: httpx.AsyncClient) -> Result:
    """EMSC earthquakes: when the service lists events, the parser must keep some."""
    collector = EMSCCollector()
    resp = await collector._get(client, collector.FEED_URL)
    resp.raise_for_status()
    payload = resp.json()
    raw, parsed = len(_features(payload)), len(collector.parse_geojson(payload))
    if raw == 0:
        return Result("emsc_seismic", "empty", "the service lists no event")
    if parsed == 0:
        return Result("emsc_seismic", "FAIL", f"{raw} events in the answer, none understood")
    return Result("emsc_seismic", "ok", f"{parsed} of {raw} events parsed")


async def check_gdacs(client: httpx.AsyncClient) -> Result:
    """GDACS: HTTP 204 (or an empty body) is a valid answer; otherwise the current events must be understood."""
    collector = GDACSCollector()
    resp = await collector._get(client, collector.FEED_URL, params=collector.search_params(clock.wall()))
    resp.raise_for_status()
    if resp.status_code == 204 or not resp.content.strip():
        return Result("gdacs_global", "empty", f"HTTP {resp.status_code}: no event matches")
    payload = resp.json()
    features = _features(payload)
    parsed = collector.parse_geojson(payload)
    current = [
        f for f in features if str(((f or {}).get("properties") or {}).get("iscurrent", "true")).strip().lower() != "false"
    ]
    if current and not parsed:
        return Result("gdacs_global", "FAIL", f"{len(current)} current events in the answer, none understood")
    if not current:
        return Result("gdacs_global", "empty", f"{len(features)} events, none current")
    return Result("gdacs_global", "ok", f"{len(parsed)} of {len(current)} current events parsed")


async def check_noaa_nws(client: httpx.AsyncClient) -> Result:
    """NOAA NWS alerts: the parser keeps only some event types, so the check is on the fields it reads."""
    collector = NOAANWSCollector()
    resp = await collector._get(client, collector.FEED_URL)
    resp.raise_for_status()
    payload = resp.json()
    features = _features(payload)
    if not features:
        return Result("noaa_nws", "empty", "no severe or extreme alert is active")
    needed = ("event", "severity", "sent", "areaDesc")
    for feature in features[:10]:
        props = (feature or {}).get("properties")
        missing = [name for name in needed if not isinstance(props, dict) or name not in props]
        if missing:
            return Result("noaa_nws", "FAIL", f"an alert has no {', '.join(missing)}")
    parsed = len(collector.parse_alerts(payload))
    vigilances = collector.parse_vigilances(payload)
    if not vigilances:
        return Result("noaa_nws", "FAIL", f"{len(features)} alerts, none read as an official warning")
    return Result(
        "noaa_nws",
        "ok",
        f"{len(features)} alerts, {parsed} of a kind the API reports, {len(vigilances)} read as official warnings",
    )


async def check_usgs_volcano(client: httpx.AsyncClient) -> Result:
    """USGS volcanoes: when the feed lists volcanoes, the parser must keep some."""
    collector = USGSVolcanoCollector()
    resp = await collector._get(client, collector.FEED_URL)
    resp.raise_for_status()
    payload = resp.json()
    raw = len(payload) if isinstance(payload, list) else len((payload or {}).get("volcanoes") or [])
    parsed = len(collector.parse_volcanoes(payload))
    if raw == 0:
        return Result("usgs_volcano_gvp", "empty", "no volcano under an elevated alert")
    if parsed == 0:
        return Result("usgs_volcano_gvp", "FAIL", f"{raw} volcanoes in the feed, none understood")
    return Result("usgs_volcano_gvp", "ok", f"{parsed} of {raw} volcanoes parsed")


async def check_swpc(client: httpx.AsyncClient) -> Result:
    """NOAA SWPC: the latest Kp index must be readable (the parser raises otherwise)."""
    status = await NOAASWPCCollector()._collect(client)
    if status.solar_kp is None:
        return Result("noaa_swpc", "FAIL", "no Kp value")
    return Result("noaa_swpc", "ok", f"Kp {status.solar_kp}")


def _utc_text(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


async def check_swpc_ovation(client: httpx.AsyncClient) -> Result:
    """NOAA SWPC OVATION: the grid and its two times must be readable (the parser raises otherwise)."""
    grid = (await SWPCOvationCollector()._collect(client)).value
    return Result("noaa_swpc_ovation", "ok", f"forecast for {_utc_text(grid.forecast_utc)}, highest value {grid.max_value}")


async def check_swpc_kp_forecast(client: httpx.AsyncClient) -> Result:
    """NOAA SWPC Kp forecast: the intervals must be readable, and some of them predicted."""
    intervals = (await SWPCKpForecastCollector()._collect(client)).value
    predicted = [i for i in intervals if i.kind == "predicted"]
    if not predicted:
        return Result("noaa_swpc_kp_forecast", "FAIL", f"{len(intervals)} intervals, none predicted")
    return Result("noaa_swpc_kp_forecast", "ok", f"{len(intervals)} intervals, {len(predicted)} predicted (highest Kp {max(i.kp for i in predicted)})")


async def check_swpc_solar_wind(client: httpx.AsyncClient) -> Result:
    """NOAA SWPC solar wind: the latest row must have a time and a speed."""
    wind = (await SWPCSolarWindCollector()._collect(client)).value
    return Result("noaa_swpc_solar_wind", "ok", f"{wind.speed_km_s} km/s at {_utc_text(wind.measured_utc)}")


async def check_swpc_alerts(client: httpx.AsyncClient) -> Result:
    """NOAA SWPC alerts: the messages of the file must be understood (an empty file is possible on a quiet stretch)."""
    collector = SWPCAlertsCollector()
    resp = await client.get(collector.URL)
    resp.raise_for_status()
    payload = resp.json()
    raw = len(payload) if isinstance(payload, list) else 0
    messages = collector.parse(payload).messages
    if raw == 0:
        return Result("noaa_swpc_alerts", "empty", "no message")
    if not messages:
        return Result("noaa_swpc_alerts", "FAIL", f"{raw} messages in the file, none understood")
    geomagnetic = sum(1 for m in messages if "geomagnetic" in (m.headline or "").lower())
    return Result("noaa_swpc_alerts", "ok", f"{len(messages)} of {raw} messages read, {geomagnetic} geomagnetic")


async def check_open_meteo(client: httpx.AsyncClient) -> Result:
    """Open-Meteo forecast: the current conditions and at least one forecast day, both requests."""
    collector = OpenMeteoCollector()
    now = await collector._collect(client, lat=LAND[0], lon=LAND[1], part="now")
    if now["current"].temp_c is None:
        return Result("open_meteo_hd", "FAIL", "no current temperature")
    await asyncio.sleep(0.5)
    daily = await collector._collect(client, lat=LAND[0], lon=LAND[1], part="daily")
    days = daily["daily_10d"]
    if not days or all(d.tmin is None and d.tmax is None for d in days):
        return Result("open_meteo_hd", "FAIL", "no daily forecast")
    steps = now["rain_next_60m"]
    return Result("open_meteo_hd", "ok", f"{now['current'].temp_c} C now, {len(days)} days, 15-minute rain {'known' if steps else 'unknown'}")


async def check_marine(client: httpx.AsyncClient) -> Result:
    """Open-Meteo Marine: at least one wave, swell or current value (the parser raises otherwise)."""
    marine = await OpenMeteoMarineCollector()._collect(client, lat=SEA[0], lon=SEA[1])
    return Result("open_meteo_marine", "ok", f"waves {marine.wave_height_m} m, swell {marine.swell_wave_height_m} m")


async def check_flood(client: httpx.AsyncClient) -> Result:
    """Open-Meteo Flood (GloFAS): a river discharge (the parser raises otherwise)."""
    flood = await OpenMeteoFloodCollector()._collect(client, lat=LAND[0], lon=LAND[1])
    return Result("open_meteo_flood", "ok", f"discharge {flood.river_discharge_m3s} m3/s")


async def check_air_quality(client: httpx.AsyncClient) -> Result:
    """Open-Meteo Air Quality: at least one air quality value (the parser raises otherwise)."""
    air = await OpenMeteoAirQualityCollector()._collect(client, lat=LAND[0], lon=LAND[1])
    return Result("open_meteo_aqi", "ok", f"EAQI {air.european_aqi}, PM10 {air.pm10_ugm3}")


async def check_met_norway(client: httpx.AsyncClient) -> Result:
    """MET Norway: a current temperature, and the cache headers the terms make us respect."""
    collected = await MetNorwayCollector()._collect(client, lat=LAND[0], lon=LAND[1], previous=None, utc_offset_s=None)
    temp = collected.value["current"].temp_c
    if temp is None:
        return Result("met_norway_fallback", "FAIL", "no current temperature")
    if collected.fresh_for_s is None or not collected.validators:
        return Result("met_norway_fallback", "FAIL", "the answer has no Expires or no Last-Modified header any more")
    return Result("met_norway_fallback", "ok", f"{temp} C now, valid for {collected.fresh_for_s:.0f} s")


async def check_rainviewer(client: httpx.AsyncClient) -> Result:
    """RainViewer: a past radar frame (the parser raises otherwise)."""
    meta = await RainViewerCollector()._collect(client)
    return Result("rainviewer_radar", "ok", f"{meta['past_frames_count']} past frames")


_FEEDS: "weakref.WeakKeyDictionary[httpx.AsyncClient, MeteoAlarmFeed]" = weakref.WeakKeyDictionary()


async def _meteoalarm_feed(client: httpx.AsyncClient) -> MeteoAlarmFeed:
    """The Atom feed of ``WARNING_COUNTRY``, asked once per client (the CAP check reuses the answer)."""
    if client not in _FEEDS:
        collector = MeteoAlarmCollector()
        resp = await collector._get(
            client,
            FEED_URL.format(slug=COUNTRY_FEEDS[WARNING_COUNTRY]),
            headers={"Accept": ATOM_ACCEPT},
        )
        resp.raise_for_status()
        _FEEDS[client] = parse_feed(resp.content, WARNING_COUNTRY)
    return _FEEDS[client]


async def check_meteoalarm_atom(client: httpx.AsyncClient) -> Result:
    """MeteoAlarm Atom feed: the warnings must be read, with a level and a phenomenon from the tables and a code with a shape."""
    feed = await _meteoalarm_feed(client)
    if not feed.vigilances:
        return Result("meteoalarm_atom", "empty", f"the feed of {WARNING_COUNTRY} lists no warning ({feed.skipped} entries skipped)")
    unknown_level = sorted({str(v.level_original) for v in feed.vigilances if v.level is None})
    unknown_type = sorted({str(v.phenomenon_original) for v in feed.vigilances if v.phenomenon is None})
    if unknown_level or unknown_type:
        words = ", ".join(unknown_level + unknown_type)
        return Result("meteoalarm_atom", "FAIL", f"words not in the tables of app/collectors/meteoalarm.py: {words}")
    geometries = default_geometries()
    unmatched = [v for v in feed.vigilances if localization_of(v, geometries) == "none"]
    if unmatched:
        codes = ", ".join(sorted({f"{g.scheme}:{g.value}" for v in unmatched for g in v.area.geocodes}))
        return Result("meteoalarm_atom", "FAIL", f"{len(unmatched)} warnings of {WARNING_COUNTRY} without a known shape: {codes}")
    return Result("meteoalarm_atom", "ok", f"{len(feed.vigilances)} warnings of {WARNING_COUNTRY} read, all with a shape")


async def check_meteoalarm_cap(client: httpx.AsyncClient) -> Result:
    """MeteoAlarm CAP document: the first warning of the feed links to one; its words must be read (a 404 fails: France has them)."""
    feed = await _meteoalarm_feed(client)
    link = next((v.link for v in feed.vigilances if v.link), None)
    if link is None:
        return Result("meteoalarm_cap", "empty", f"no warning of {WARNING_COUNTRY} links to a CAP document")
    collector = MeteoAlarmCapCollector()
    resp = await collector._get(client, link, headers={"Accept": CAP_ACCEPT})
    resp.raise_for_status()
    document = parse_cap(resp.content)
    if not document.texts:
        return Result("meteoalarm_cap", "FAIL", "the CAP document has no text any more")
    languages = ", ".join(sorted(text.language or "?" for text in document.texts))
    return Result("meteoalarm_cap", "ok", f"words in {len(document.texts)} language(s) ({languages}), issued by {document.issuer}")


# A flow of fewer sections than this is a truncated or a changed answer (the real one has about 337).
VIGICRUES_MIN_SECTIONS = 100
# The image of the publication is regenerated just after the flow (21 seconds on 2026-10-20): a gap beyond these means it does not follow it.
VIGICRUES_IMAGE_BEFORE_S = 300
VIGICRUES_IMAGE_AFTER_S = 1800

# The kinds of ECCC alert that carry a colour (a statement or an ended alert does not).
ECCC_COLOURED_KINDS = {"warning", "advisory", "watch"}


async def check_dwd(client: httpx.AsyncClient) -> Result:
    """DWD status of Germany: the warnings must be read, with a level and a phenomenon from the tables and a polygon."""
    status = (await DWDCollector()._collect(client)).value
    if not status.vigilances:
        if status.skipped:
            return Result("dwd_cap", "FAIL", f"{status.skipped} documents in the status, none read as a warning")
        return Result("dwd_cap", "empty", "the status lists no warning")
    unknown_level = sorted({str(v.level_original) for v in status.vigilances if v.level is None})
    unknown_type = sorted(
        {
            next((p.value for p in v.texts[0].parameters if p.name == "eventCode:II"), "?")
            for v in status.vigilances
            if v.phenomenon is None and v.texts
        }
    )
    if unknown_level or unknown_type:
        words = ", ".join([f"severity {w}" for w in unknown_level] + [f"event number {w}" for w in unknown_type])
        return Result("dwd_cap", "FAIL", f"words not in the tables of app/collectors/dwd.py: {words}")
    without_polygon = [v for v in status.vigilances if not v.area.has_polygon]
    if without_polygon:
        return Result("dwd_cap", "FAIL", f"{len(without_polygon)} areas without a polygon (the district product always has them)")
    documents = len({v.identifier for v in status.vigilances})
    return Result("dwd_cap", "ok", f"{len(status.vigilances)} areas of {documents} documents read, all with a polygon")


async def check_eccc(client: httpx.AsyncClient) -> Result:
    """ECCC alerts (GeoMet): a warning, advisory or watch must get a level; the words of status must be in the table."""
    resp = await ECCCCollector()._get(
        client,
        ITEMS_URL,
        params={"f": "json", "limit": "1000"},
        headers={"Accept": "application/geo+json, application/json;q=0.9"},
    )
    resp.raise_for_status()
    payload = resp.json()
    features = _features(payload)
    if not features:
        return Result("eccc_alerts", "empty", "no alert is in force in Canada")
    alerts = parse_items(payload)
    if not alerts.vigilances:
        return Result("eccc_alerts", "FAIL", f"{len(features)} alerts in the answer, none understood")
    props = [f.get("properties") or {} for f in features if isinstance(f, dict)]
    in_force = [p for p in props if not eccc_is_ended(p)]
    colours = sorted(
        {
            str(p.get("risk_colour_en"))
            for p in in_force
            if str(p.get("alert_type") or "").lower() in ECCC_COLOURED_KINDS
            and str(p.get("risk_colour_en") or "").strip().lower() not in ECCC_LEVELS
        }
    )
    if colours:
        return Result("eccc_alerts", "FAIL", f"colours not in the table of app/collectors/eccc.py: {', '.join(colours)}")
    statuses = sorted({str(p.get("status_en")) for p in in_force if str(p.get("status_en") or "").strip().lower() not in ECCC_STATUSES})
    if statuses:
        return Result("eccc_alerts", "FAIL", f"status words not in the table of app/collectors/eccc.py: {', '.join(statuses)}")
    without_polygon = [v for v in alerts.vigilances if not v.area.has_polygon]
    if without_polygon:
        return Result("eccc_alerts", "FAIL", f"{len(without_polygon)} alerts without a polygon")
    return Result("eccc_alerts", "ok", f"{len(alerts.vigilances)} of {len(features)} alerts read, all with a polygon")


async def check_metservice(client: httpx.AsyncClient) -> Result:
    """MetService CAP feed: an empty feed is a valid answer; an item must be read the way the issuer's guide says."""
    feed = (await MetServiceCollector()._collect(client)).value
    if feed.items == 0:
        return Result("metservice_cap", "empty", "the feed lists no warning (no real item has been read yet)")
    if feed.unreadable:
        return Result("metservice_cap", "FAIL", f"{feed.unreadable} of {feed.items} items could not be read as a CAP alert")
    colourless, unknown_event, no_level, no_polygon = set(), set(), set(), 0
    for warning in feed.vigilances:
        parameters = {p.name for text in warning.texts for p in text.parameters}
        if METSERVICE_COLOUR not in parameters:
            colourless.add(str(warning.event))
        if (warning.event or "").lower() not in METSERVICE_EVENTS:
            unknown_event.add(str(warning.event))
        if warning.level is None:
            no_level.add(f"{warning.event}/{warning.level_original}")
        if not warning.area.has_polygon:
            no_polygon += 1
    if no_level:
        words = ", ".join(sorted(no_level))
        return Result("metservice_cap", "FAIL", f"event/colour that give no level (derive_level in app/collectors/metservice.py): {words}")
    if colourless or unknown_event or no_polygon:
        found = [f"no {METSERVICE_COLOUR} on {sorted(colourless)}"] if colourless else []
        found += [f"event words the guide does not list: {sorted(unknown_event)}"] if unknown_event else []
        found += [f"{no_polygon} warnings without a polygon"] if no_polygon else []
        return Result("metservice_cap", "FAIL", "not as the MetService CAP Feed User Guide says: " + "; ".join(found))
    return Result("metservice_cap", "ok", f"{len(feed.vigilances)} warnings from {feed.items} items read")


async def vigicrues_image_problem(client: httpx.AsyncClient, flow_updated: Optional[int]) -> Optional[str]:
    """Why the publication image no longer tells when the flow changes (``None`` if it still does).

    The collector reads the 2.2 MB flow only when the image of the national map changed (``app/collectors/vigicrues.py``).
    That holds while the image has a validator and is regenerated with the flow: on 2026-10-20 the flow was published at
    07:55:15 UTC and the image carried ``Last-Modified: 07:55:36``.
    """
    resp = await client.head(VIGICRUES_SIGNAL_URL)
    if resp.status_code != 200:
        return f"the publication image answers HTTP {resp.status_code}"
    modified, tag = resp.headers.get("Last-Modified"), resp.headers.get("ETag")
    if not (modified and tag):
        return "the publication image has no Last-Modified or no ETag"
    try:
        stamp = parsedate_to_datetime(modified).timestamp()
    except (TypeError, ValueError):
        return f"the publication image has a Last-Modified that is not a date ({modified})"
    if flow_updated is None:
        return "the flow carries no date to compare the image with"
    gap = stamp - flow_updated
    if not -VIGICRUES_IMAGE_BEFORE_S <= gap <= VIGICRUES_IMAGE_AFTER_S:
        return f"the publication image is dated {gap / 60:+.0f} minutes from the flow (it was +0.4 on 2026-10-20)"
    return None


async def check_vigicrues(client: httpx.AsyncClient) -> Result:
    """Vigicrues flow: the whole flow must be there, every section above green read, and the publication image still follow it."""
    flow = (await VigicruesCollector()._collect(client)).value
    if flow.sections < VIGICRUES_MIN_SECTIONS:
        return Result("vigicrues_flow", "FAIL", f"only {flow.sections} sections in the flow (it has had about 337)")
    if flow.skipped:
        return Result("vigicrues_flow", "FAIL", f"{flow.skipped} sections above green could not be read")
    problem = await vigicrues_image_problem(client, flow.updated_utc)
    if problem:
        return Result("vigicrues_flow", "FAIL", f"{problem}: the collector would read the whole flow every hour instead of at each publication")
    if not flow.vigilances:
        return Result("vigicrues_flow", "empty", f"all {flow.sections} sections are green")
    counts = {name: sum(1 for v in flow.vigilances if v.level is not None and v.level.value == name) for name in ("YELLOW", "ORANGE", "RED")}
    detail = ", ".join(f"{n} {name.lower()}" for name, n in counts.items() if n)
    return Result("vigicrues_flow", "ok", f"{len(flow.vigilances)} of {flow.sections} sections above green ({detail})")


async def check_tsunami(client: httpx.AsyncClient) -> Result:
    """Tsunami warning centres: the list of the latest bulletins and the CAP of the newest one must be read, and so must the events in force."""
    collector = TsunamiCollector()
    await collector._collect(client)
    listing, state = collector._listing, collector.last_state
    if listing is None or not listing.bulletins:
        return Result("tsunami_warning_centers", "FAIL", "the list of the latest bulletins holds none that could be read (it lists about forty)")
    if listing.unreadable:
        return Result("tsunami_warning_centers", "FAIL", f"{listing.unreadable} of {listing.unreadable + len(listing.bulletins)} entries of the list could not be read")
    if state.unreadable:
        return Result("tsunami_warning_centers", "FAIL", f"{state.unreadable} messages of the events in force could not be read")
    newest = max(listing.bulletins, key=lambda b: b.issued_utc)
    message = collector._messages.get(newest.cap_url)
    if message is None:  # a quiet week: the newest bulletin is an information statement, the CAP the collector does not read
        response = await collector._get(client, newest.cap_url, headers={"Accept": "application/xml, text/xml;q=0.9, */*;q=0.5"})
        response.raise_for_status()
        message = parse_message(response.content, newest)
    if not message.identifier or message.sent_utc is None:
        return Result("tsunami_warning_centers", "FAIL", f"the CAP message {newest.cap_url} has no identifier or no time of issue")
    where = f"{len(listing.bulletins)} bulletins listed, the CAP of the newest ({newest.twc} {newest.wmo_id}) read"
    if state.events:
        names = ", ".join(f"{event.twc} {event.hazard_id.rsplit('_', 1)[-1]} ({event.level.value})" for event in state.events)
        return Result("tsunami_warning_centers", "ok", f"{where}; {len(state.events)} event(s) in force: {names}")
    return Result("tsunami_warning_centers", "ok", f"{where}; no event in force")


async def check_nhc(client: httpx.AsyncClient) -> Result:
    """National Hurricane Center: the file of active storms must be the one the parser knows; a storm's bundle must give a cone."""
    collector = NHCCollector()
    await collector._collect(client)
    state = collector.last_state
    if state.unreadable:
        return Result("nhc_storms", "FAIL", f"{state.unreadable} storms of the file could not be read")
    if not state.storms:
        return Result("nhc_storms", "empty", "no active storm (the file is read; the forecast bundles are checked when a storm is active)")
    problems = [
        f"{storm.name}: " + ("its forecast bundle could not be read" if storm.forecast is None else "its bundle has no cone")
        for storm in state.storms
        if storm.bundle_url and (storm.forecast is None or not storm.forecast.cones)
    ]
    if problems:
        return Result("nhc_storms", "FAIL", "; ".join(problems))
    names = ", ".join(f"{storm.name} ({storm.id})" for storm in state.storms)
    return Result("nhc_storms", "ok", f"{len(state.storms)} active storm(s) with their cone: {names}")


# The file of one VIIRS satellite holds about 100,000 detections for 24 hours and 150,000 for 48 (102,540 and 149,942 on 2026-10-20): fewer than this is a
# cut or a changed file.
FIRMS_MIN_ROWS = 5_000
# The share of rows that may be unreadable (no position or time), without a confidence the parser knows or without a power: one in a hundred.
FIRMS_MAX_ODD_SHARE = 0.01
# Statuses that say "busy, or not for this client" and not "the format changed" (the module docstring: 403 / 429 / 5xx).
FIRMS_BUSY_STATUSES = {403, 429}


def _firms_problem(label: str, table: Any, skipped: int) -> Optional[Result]:
    """``FAIL`` when what was read is not the file the parser knows: rows that cannot be read, too few of them, columns that are empty."""
    rows = len(table)
    seen = rows + skipped
    if skipped > FIRMS_MAX_ODD_SHARE * seen:
        return Result("nasa_firms", "FAIL", f"{label}; {skipped:,} rows of {seen:,} could not be read")
    if rows < FIRMS_MIN_ROWS:
        return Result("nasa_firms", "FAIL", f"{label} (tens of thousands expected): the file is cut or changed")
    no_confidence = sum(1 for code in table.conf if code == 0)
    no_power = sum(1 for value in table.frp if value != value)
    if no_confidence > FIRMS_MAX_ODD_SHARE * rows:
        return Result("nasa_firms", "FAIL", f"{label}; {no_confidence:,} rows without a confidence the parser knows (low, nominal, high)")
    if no_power > FIRMS_MAX_ODD_SHARE * rows:
        return Result("nasa_firms", "FAIL", f"{label}; {no_power:,} rows without a fire radiative power")
    return None


def _firms_unreachable_or_fail(exc: FIRMSError) -> Result:
    busy = exc.transport or (exc.status is not None and (exc.status in FIRMS_BUSY_STATUSES or exc.status >= 500))
    return Result("nasa_firms", "unreachable" if busy else "FAIL", str(exc))  # its message never holds the key


async def check_firms(client: httpx.AsyncClient) -> Result:
    """NASA FIRMS: the static file of one source must be the CSV the parser knows, full-sized, with its columns filled. With a key, one world day of the area API too."""
    settings = load_firms_settings()
    if not settings.enabled:
        return Result("nasa_firms", "skipped", f"{settings.off_reason}: nothing was asked")
    collector = FIRMSCollector(settings.key, settings.sources, settings.refresh_s, settings.window_hours)
    source = settings.sources[0]
    now = clock.wall()
    try:
        reading = await collector._read_static(client, source, collector._states[source], now)
    except FIRMSError as exc:
        return _firms_unreachable_or_fail(exc)
    label = f"{source} {settings.window_hours} h file: {len(reading.table):,} detections, {reading.bytes_read / 1_000_000:.1f} MB"
    problem = _firms_problem(label, reading.table, reading.skipped)
    if problem is not None:
        return problem
    names = ", ".join(reading.table.satellites) or "no satellite named"
    detail = f"{label}, {reading.skipped} skipped; satellites: {names}; written {reading.validators.last_modified if reading.validators else 'at an unknown time'}"
    if settings.key is not None:  # the second way to the same data: one world day of the area API (yesterday: the file of today is still short in the first hours)
        date = utc_dates(now)[1]
        try:
            content = await collector._read_day(client, source, date)
            table, stats = parse_csv(content, date)
        except FIRMSError as exc:
            return _firms_unreachable_or_fail(exc)
        except ValueError as exc:
            snippet = " ".join(content[:100].decode("ascii", errors="replace").split())
            return Result("nasa_firms", "FAIL", collector.redact(f"{source} {date}, area API: {exc} ({snippet!r})"))
        area_label = f"{source} {date}, area API: {len(table):,} detections, {len(content) / 1_000_000:.1f} MB"
        problem = _firms_problem(area_label, table, stats.skipped)
        if problem is not None:
            return problem
        detail += f"; {area_label}"
    return Result("nasa_firms", "ok", detail)


# The registry of the WMO services: fewer than this is a truncated or a changed answer (140 on 2026-10-20).
WMO_REGISTRY_MIN_SERVICES = 100
# Seconds between two services of the WMO sample: each is its own host.
WMO_SAMPLE_PAUSE_S = 1.0
# The services read every week besides the verified ones: one of each family the verified ones do not cover (one document per
# language in two feeds, codes only with no polygon).
WMO_SAMPLE_EXTRA = ("ru-roshydromet-en", "in-ndma-xx")
# What is not a format change: a server that does not answer in time, a bot check, a refusal to talk to this client.
WMO_UNREACHABLE_WORDS = ("Timeout", "ConnectError", "ReadError", "RemoteProtocolError", "Server error", "403", "429", "SSL")


async def check_wmo_swic_registry(client: httpx.AsyncClient) -> Result:
    """The registry of the WMO services: still a registry, still the addresses the verified services were read at, a border for each country."""
    live = (await SwicRegistryCollector()._collect(client)).value
    if len(live) < WMO_REGISTRY_MIN_SERVICES:
        return Result("wmo_swic_registry", "FAIL", f"{len(live)} services (about 140 expected): the answer is truncated or changed")
    known = {source.source_id: source for source in bundled_registry()}
    now = {source.source_id: source for source in live}
    new, gone = sorted(set(now) - set(known)), sorted(set(known) - set(now))
    moved = sorted(
        sid
        for sid in (t.source_id for t in WMO_TERMS if t.status == "verified")
        if sid in known and (sid not in now or now[sid].feeds != known[sid].feeds or now[sid].country != known[sid].country)
    )
    if moved:
        return Result("wmo_swic_registry", "FAIL", f"the address or the country of a verified service changed, its terms were read for the old one: {', '.join(moved)}")
    borders = set(default_countries().countries())
    unlocated = sorted({s.country for s in live if s.country and is_eligible(s, "all") and s.country not in borders})
    if unlocated:
        return Result("wmo_swic_registry", "FAIL", f"countries with a service and no border (python tools/build_geometries.py --countries-only): {', '.join(unlocated)}")
    detail = f"{len(live)} services ({len(new)} the bundled snapshot does not know, {len(gone)} it knows that are gone)"
    if new or gone:
        detail += f"; python tools/build_swic_snapshot.py rewrites it: new {', '.join(new[:8])}{'...' if len(new) > 8 else ''}"
    return Result("wmo_swic_registry", "ok", detail)


async def check_wmo_swic(client: httpx.AsyncClient) -> Result:
    """The verified services of the WMO registry and a few others: the feeds and documents are read, the licence line is still in the feed."""
    collector = SwicCollector()
    by_id = {source.source_id: source for source in bundled_registry()}
    sample = [t.source_id for t in WMO_TERMS if t.status == "verified"] + [sid for sid in WMO_SAMPLE_EXTRA if sid in by_id]
    problems: list[str] = []
    unreachable: list[str] = []
    words: set[str] = set()
    read: list[str] = []
    listed = 0
    for index, sid in enumerate(sample):
        if index:
            await asyncio.sleep(WMO_SAMPLE_PAUSE_S)
        source = by_id[sid]
        try:
            feed = await collector._read(client, source)
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            (unreachable if any(word in text for word in WMO_UNREACHABLE_WORDS) else problems).append(f"{sid} ({text[:90]})")
            continue
        if feed.documents and feed.unreadable == feed.documents:
            problems.append(f"{sid}: none of its {feed.documents} documents could be read as a CAP alert")
        listed += feed.documents + feed.old_items
        words.update(f"{v.level_original}" for v in feed.vigilances if v.level is None and v.level_original)
        terms = terms_of(sid)
        if terms is not None and terms.status == "verified" and terms.feed_marker:
            response = await collector._get_capped(client, source.feeds[0], max_bytes=2_000_000, headers={"Accept": "application/xml, */*;q=0.5"})
            if terms.feed_marker not in response.text.lower():
                problems.append(f"{sid}: the licence line ({terms.feed_marker!r}) is not in its feed any more, re-read its terms before serving it")
        read.append(f"{sid} {len(feed.vigilances)}/{feed.documents}/{feed.documents + feed.old_items}")
    if problems:
        return Result("wmo_swic", "FAIL", "; ".join(problems))
    if not read:
        return Result("wmo_swic", "unreachable", "; ".join(unreachable))
    detail = f"{len(read)} services read ({', '.join(read)}: warnings in force/documents read/entries listed)"
    if words:
        detail += f"; level words in no table (null): {', '.join(sorted(words))}"
    if unreachable:
        detail += f"; not reachable now: {', '.join(unreachable)}"
    return Result("wmo_swic", "ok" if listed else "empty", detail)


async def check_ecmwf_opendata(client: httpx.AsyncClient) -> Result:
    """ECMWF IFS open data on its AWS mirror (the pre-alert pilot): the newest complete run, its first lead's index and fields, decoded."""
    from datetime import datetime, timezone

    try:
        from app.prealert.gfs import TURKEY_BOX, decode
        from app.prealert.shadow import EcmwfOpenDataCollector, ShadowStore, candidate_runs, file_url, wanted_fields
    except ImportError as error:  # the optional extra 'prealert' is not installed
        return Result("ecmwf_opendata", "skipped", f"optional dependency missing: {error}")
    import tempfile

    collector = EcmwfOpenDataCollector(ShadowStore(Path(tempfile.mkdtemp()) / "check.db"))
    for run in candidate_runs(datetime.now(timezone.utc)):
        head = await collector._head(client, file_url(run, collector.last_lead_h) + ".index")
        if head.status_code == 200:
            break
    else:
        return Result("ecmwf_opendata", "empty", "no complete run in the last 30 hours")
    index = await collector._get(client, file_url(run, 3) + ".index")
    index.raise_for_status()
    names: set[str] = set()
    size = 0
    for _, offset, length in wanted_fields(index.text):
        part = await collector._get(client, file_url(run, 3) + ".grib2", headers={"Range": f"bytes={offset}-{offset + length - 1}"})
        part.raise_for_status()
        size += len(part.content)
        names.update(m.short_name for m in decode(part.content, TURKEY_BOX))
    if names != {"gust", "ptype", "tmax", "tmin", "tp"}:
        return Result("ecmwf_opendata", "FAIL", f"run {run:%Y-%m-%d %H} UTC f003: fields {sorted(names)}")
    return Result("ecmwf_opendata", "ok", f"run {run:%Y-%m-%d %H} UTC f003: {size} bytes, the five fields")


def _travel_result(source: str, feed: travel_feeds.TravelFeed, unknown: list[str], extra: str = "") -> Result:
    """The outcome of a travel advice feed: a destination no table places or an item that cannot be read is a failure (the
    tables of ``app/collectors/travel_codes.py`` or the parser need an update)."""
    if unknown:
        return Result(source, "FAIL", f"destinations no table of app/collectors/travel_codes.py places: {', '.join(sorted(unknown)[:12])}")
    if feed.skipped:
        return Result(source, "FAIL", f"{feed.skipped} destinations could not be read")
    if not feed.advices:
        return Result(source, "FAIL", "no advice in the answer")
    levels = sum(1 for advice in feed.advices if advice.level is not None or advice.common_min is not None)
    return Result(source, "ok", f"{len(feed.advices)} advices, {levels} with a level{extra}")


async def check_travel_us(client: httpx.AsyncClient) -> Result:
    """The Department of State: every advice read, its level in its title, every page placed (or known not to be)."""
    resp = await travel_feeds.USTravelCollector()._get(client, travel_feeds.US_URL)
    resp.raise_for_status()
    payload = resp.json()
    feed = travel_feeds.parse_us(payload, int(clock.wall()))
    pages = [str(item.get("Link", "")).rsplit("/", 1)[-1].removesuffix(".html") for item in payload if isinstance(item, dict)]
    unknown = [page for page in pages if page not in travel_codes.US_PAGES and not travel_codes.us_countries(page)]
    return _travel_result("travel_us", feed, unknown)


async def check_travel_ca(client: httpx.AsyncClient) -> Result:
    """Global Affairs Canada: the index (every destination with a level) and the text of one destination (Mexico)."""
    collector = travel_feeds.CanadaTravelCollector()
    feed = (await collector._collect(client)).value
    unknown = list(feed.unplaced)
    detail = (await collector._collect(client, detail="MX")).value
    if not any(text.language == "en" for text in detail) or not any(text.language == "fr" for text in detail):
        return Result("travel_ca", "FAIL", "the destination Mexico has no 'advisories' text in English and in French")
    return _travel_result("travel_ca", feed, unknown, "; Mexico's risk levels read in English and French")


async def check_travel_au(client: httpx.AsyncClient) -> Result:
    """Smartraveller: every destination placed, every level one of its four."""
    feed = (await travel_feeds.SmartravellerCollector()._collect(client)).value
    words = sorted({words for a in feed.advices if a.level is None for words in a.level_texts.values()})
    if words:
        return Result("travel_au", "FAIL", f"levels not in AU_LEVELS of app/collectors/travel.py: {', '.join(words)}")
    return _travel_result("travel_au", feed, list(feed.unplaced))


async def check_travel_nl(client: httpx.AsyncClient) -> Result:
    """NederlandWereldwijd: every destination placed; the summaries still state the colour (most give one)."""
    feed = await travel_feeds.NetherlandsTravelCollector()._collect(client)
    stated = sum(1 for advice in feed.advices if advice.level is not None)
    if feed.advices and stated < len(feed.advices) // 2:
        return Result("travel_nl", "FAIL", f"only {stated} of {len(feed.advices)} summaries state a single colour: the sentence changed?")
    return _travel_result("travel_nl", feed, list(feed.unplaced))


async def check_travel_gb(client: httpx.AsyncClient) -> Result:
    """The FCDO: the index (every page placed) and one page (France), whose statuses and part "Warnings and insurance" are read.
    The 226 pages are not read here: a page has the same form as the others."""
    collector = travel_feeds.FCDOTravelCollector()
    resp = await collector._get(client, travel_feeds.GB_INDEX_URL)
    resp.raise_for_status()
    entries = travel_feeds.parse_gb_index(resp.json())
    unknown = [entry.page for entry in entries if not travel_codes.gb_countries(entry.page)]
    if unknown:
        return Result("travel_gb", "FAIL", f"pages no table of app/collectors/travel_codes.py places: {', '.join(sorted(unknown)[:12])}")
    page = await collector._read_page(client, "france")
    if any(status not in travel_feeds.GB_STATUSES for status in page.statuses):
        return Result("travel_gb", "FAIL", f"statuses not in GB_STATUSES: {', '.join(page.statuses)}")
    if page.warnings_html is None:
        return Result("travel_gb", "FAIL", "the page of France has no part 'warnings-and-insurance'")
    return Result("travel_gb", "ok", f"{len(entries)} pages in the index, all placed; France read ({len(page.history)} changes in its history)")


async def check_health_ecdc(client: httpx.AsyncClient) -> Result:
    """The ECDC: its three feeds answer, each with items that have a title, a link and a date."""
    collector = health_feeds.ECDCHealthCollector()
    counts: list[str] = []
    for kind, url in health_feeds.ECDC_FEEDS.items():
        resp = await collector._get(client, url)
        resp.raise_for_status()
        items = health_feeds.parse_ecdc(resp.content, kind)
        if not items:
            return Result("health_ecdc", "FAIL", f"the feed of {kind} has no item with a title and a link")
        if any(item.published_utc is None for item in items):
            return Result("health_ecdc", "FAIL", f"an item of {kind} has no date that can be read")
        counts.append(f"{len(items)} {kind.replace('_', ' ')}s")
    return Result("health_ecdc", "ok", ", ".join(counts))


CHECKS: dict[str, Check] = {
    "usgs_eq": check_usgs,
    "emsc_seismic": check_emsc,
    "gdacs_global": check_gdacs,
    "noaa_nws": check_noaa_nws,
    "usgs_volcano_gvp": check_usgs_volcano,
    "noaa_swpc": check_swpc,
    "noaa_swpc_ovation": check_swpc_ovation,
    "noaa_swpc_kp_forecast": check_swpc_kp_forecast,
    "noaa_swpc_solar_wind": check_swpc_solar_wind,
    "noaa_swpc_alerts": check_swpc_alerts,
    "open_meteo_hd": check_open_meteo,
    "open_meteo_marine": check_marine,
    "open_meteo_flood": check_flood,
    "open_meteo_aqi": check_air_quality,
    "met_norway_fallback": check_met_norway,
    "rainviewer_radar": check_rainviewer,
    "meteoalarm_atom": check_meteoalarm_atom,
    "meteoalarm_cap": check_meteoalarm_cap,
    "dwd_cap": check_dwd,
    "eccc_alerts": check_eccc,
    "metservice_cap": check_metservice,
    "vigicrues_flow": check_vigicrues,
    "tsunami_warning_centers": check_tsunami,
    "nhc_storms": check_nhc,
    "wmo_swic_registry": check_wmo_swic_registry,
    "wmo_swic": check_wmo_swic,
    "nasa_firms": check_firms,
    "ecmwf_opendata": check_ecmwf_opendata,
    "travel_gb": check_travel_gb,
    "travel_us": check_travel_us,
    "travel_ca": check_travel_ca,
    "travel_au": check_travel_au,
    "travel_nl": check_travel_nl,
    "health_ecdc": check_health_ecdc,
}


def classify_error(source: str, exc: BaseException) -> Result:
    """Turn an exception into an outcome: a format problem fails, a provider that does not answer is only reported."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in FORMAT_STATUSES:
            return Result(source, "FAIL", f"HTTP {status}: the endpoint or the query is not accepted any more")
        return Result(source, "unreachable", f"HTTP {status}")
    if isinstance(exc, httpx.TransportError):
        return Result(source, "unreachable", f"{type(exc).__name__}: {exc}")
    return Result(source, "FAIL", f"{type(exc).__name__}: {exc}")


async def run_checks(
    client: httpx.AsyncClient, only: Optional[list[str]] = None, pause_s: float = 1.0
) -> list[Result]:
    """Run the checks one after the other (one request per source, ``pause_s`` seconds apart)."""
    results: list[Result] = []
    selected = [name for name in CHECKS if only is None or name in only]
    for index, name in enumerate(selected):
        if index:
            await asyncio.sleep(pause_s)
        try:
            results.append(await CHECKS[name](client))
        except Exception as exc:  # a parser that raises means the answer was not understood
            results.append(classify_error(name, exc))
    return results


def render(results: list[Result]) -> str:
    """The results as a Markdown table (readable in a terminal and in the GitHub job summary)."""
    lines = ["| Source | Outcome | Detail |", "| --- | --- | --- |"]
    lines += [f"| `{r.source}` | {r.outcome} | {r.detail} |" for r in results]
    return "\n".join(lines)


def exit_code(results: list[Result], strict: bool = False) -> int:
    """1 when a source failed (or, with ``strict``, was unreachable); else 0."""
    bad = {"FAIL", "unreachable"} if strict else {"FAIL"}
    return 1 if any(r.outcome in bad for r in results) else 0


async def main_async(args: argparse.Namespace) -> int:
    headers = {"User-Agent": build_user_agent(), "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=20.0, headers=headers, follow_redirects=True) as client:
        results = await run_checks(client, only=args.only, pause_s=args.pause)
    table = render(results)
    print(table)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## Live feeds\n\n" + table + "\n")
    counts = {o: sum(1 for r in results if r.outcome == o) for o in ("ok", "empty", "unreachable", "skipped", "FAIL")}
    print("\n" + ", ".join(f"{n} {o}" for o, n in counts.items()))
    return exit_code(results, strict=args.strict)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Ask each real provider once and check the parsers still read it.")
    parser.add_argument("--only", nargs="+", choices=sorted(CHECKS), help="check only these sources")
    parser.add_argument("--strict", action="store_true", help="an unreachable source also fails the run")
    parser.add_argument("--pause", type=float, default=1.0, help="seconds between two sources (default: 1)")
    args = parser.parse_args(argv)
    if not os.environ.get("VIGIE_CONTACT"):
        print("note: VIGIE_CONTACT is not set; MET Norway and NOAA ask for an identifying contact", file=sys.stderr)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
