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
from pathlib import Path
from typing import Any, Optional

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app import clock  # noqa: E402
from app.collectors.base import build_user_agent  # noqa: E402
from app.collectors.disasters import GDACSCollector, NOAANWSCollector  # noqa: E402
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
from app.collectors.seismic import EMSCCollector, USGSCollector  # noqa: E402
from app.collectors.space_weather import NOAASWPCCollector  # noqa: E402
from app.collectors.weather import MetNorwayCollector, OpenMeteoCollector, RainViewerCollector  # noqa: E402
from app.services.geoindex import default_geometries, localization_of  # noqa: E402

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
    outcome: str  # "ok", "empty", "FAIL" or "unreachable"
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


CHECKS: dict[str, Check] = {
    "usgs_eq": check_usgs,
    "emsc_seismic": check_emsc,
    "gdacs_global": check_gdacs,
    "noaa_nws": check_noaa_nws,
    "usgs_volcano_gvp": check_usgs_volcano,
    "noaa_swpc": check_swpc,
    "open_meteo_hd": check_open_meteo,
    "open_meteo_marine": check_marine,
    "open_meteo_flood": check_flood,
    "open_meteo_aqi": check_air_quality,
    "met_norway_fallback": check_met_norway,
    "rainviewer_radar": check_rainviewer,
    "meteoalarm_atom": check_meteoalarm_atom,
    "meteoalarm_cap": check_meteoalarm_cap,
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
    counts = {o: sum(1 for r in results if r.outcome == o) for o in ("ok", "empty", "unreachable", "FAIL")}
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
