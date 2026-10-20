"""The weekly live-feeds check (``tools/check_live_feeds.py``), played against simulated providers.

The tool itself reaches the real providers and runs outside pytest (``.github/workflows/live-feeds.yml``).
Here every provider is simulated (``httpx.MockTransport``), so these tests check the *judgement* of the tool:
a good answer passes, a valid empty answer is not a failure, and an answer the parsers cannot read is.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from tests import samples
from tools import check_live_feeds as tool

GOOD_USGS = {
    "features": [
        {
            "id": "us1",
            "properties": {"mag": 5.1, "place": "Somewhere", "time": 1792334500000},
            "geometry": {"coordinates": [10.0, 20.0, 15.0]},
        }
    ]
}
GOOD_EMSC = {
    "features": [
        {
            "id": "e1",
            "properties": {"mag": 5.0, "flynn_region": "SOMEWHERE", "time": "2026-10-18T12:00:00Z"},
            "geometry": {"coordinates": [10.0, 20.0, -15.0]},
        }
    ]
}
GOOD_GDACS = {
    "features": [
        {
            "properties": {"eventtype": "TC", "eventid": "1", "alertlevel": "Red", "name": "Cyclone X",
                           "fromdate": "2026-10-18T10:00:00Z", "iscurrent": "true"},
            "geometry": {"coordinates": [-82.5, 27.5]},
        }
    ]
}
GOOD_NWS = {
    "features": [
        {
            "id": "urn:1",
            "properties": {"event": "Tornado Warning", "severity": "Extreme", "areaDesc": "A County",
                           "sent": "2026-10-18T13:00:00+00:00"},
            "geometry": {"type": "Point", "coordinates": [-97.5, 35.5]},
        }
    ]
}
GOOD_VOLCANO = [
    {"vName": "Great Sitkin", "vnum": "1", "lat": 52.0, "long": -176.1, "colorCode": "ORANGE", "sentUtc": "2026-10-19 20:01:42"}
]
GOOD_KP = [{"time_tag": "2026-10-20T00:00:00", "Kp": 2.33}]
GOOD_FLOOD = {"daily": {"river_discharge": [10.0], "river_discharge_mean": [9.0], "river_discharge_max": [12.0]}}
GOOD_AIR = {"current": {"european_aqi": 31, "pm10": 14.0}}
GOOD_RADAR = {"host": "https://tilecache.example", "radar": {"past": [{"time": 1792330000, "path": "/v2/radar/1792330000"}]}}
METNO_HEADERS = {
    "Expires": "Tue, 20 Oct 2026 12:00:00 GMT",
    "Date": "Tue, 20 Oct 2026 11:30:00 GMT",
    "Last-Modified": "Tue, 20 Oct 2026 11:25:00 GMT",
}


def good_answers() -> dict[str, Callable[[httpx.Request], httpx.Response]]:
    """What each provider answers when nothing changed, keyed by host."""
    marine = samples.marine_payload()
    return {
        "earthquake.usgs.gov": lambda r: httpx.Response(200, json=GOOD_USGS),
        "www.seismicportal.eu": lambda r: httpx.Response(200, json=GOOD_EMSC),
        "www.gdacs.org": lambda r: httpx.Response(200, json=GOOD_GDACS),
        "api.weather.gov": lambda r: httpx.Response(200, json=GOOD_NWS),
        "volcanoes.usgs.gov": lambda r: httpx.Response(200, json=GOOD_VOLCANO),
        "services.swpc.noaa.gov": lambda r: httpx.Response(200, json=GOOD_KP),
        "api.open-meteo.com": lambda r: httpx.Response(200, json=samples.open_meteo_payload()),
        "marine-api.open-meteo.com": lambda r: httpx.Response(200, json=marine),
        "flood-api.open-meteo.com": lambda r: httpx.Response(200, json=GOOD_FLOOD),
        "air-quality-api.open-meteo.com": lambda r: httpx.Response(200, json=GOOD_AIR),
        "api.met.no": lambda r: httpx.Response(200, json=samples.metno_payload(), headers=METNO_HEADERS),
        "api.rainviewer.com": lambda r: httpx.Response(200, json=GOOD_RADAR),
    }


async def run(overrides: dict[str, Callable[[httpx.Request], httpx.Response]] | None = None, **kwargs: Any) -> dict[str, tool.Result]:
    answers = {**good_answers(), **(overrides or {})}

    def handler(request: httpx.Request) -> httpx.Response:
        return answers[request.url.host](request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await tool.run_checks(client, pause_s=0.0, **kwargs)
    return {r.source: r for r in results}


def test_there_is_a_check_for_each_collector() -> None:
    from app.main import aggregator

    assert set(tool.CHECKS) == {c.provider_id for c in aggregator.get_all_collectors()}


@pytest.mark.asyncio
async def test_every_source_passes_when_nothing_changed() -> None:
    results = await run()
    assert {r.outcome for r in results.values()} == {"ok"}, {k: v for k, v in results.items() if v.outcome != "ok"}
    assert tool.exit_code(list(results.values())) == 0


@pytest.mark.asyncio
async def test_a_valid_empty_answer_is_not_a_failure() -> None:
    results = await run(
        {
            "www.gdacs.org": lambda r: httpx.Response(204),  # GDACS: no event matches
            "api.weather.gov": lambda r: httpx.Response(200, json={"features": []}),
            "earthquake.usgs.gov": lambda r: httpx.Response(200, json={"features": []}),
        }
    )
    assert results["gdacs_global"].outcome == "empty" and "204" in results["gdacs_global"].detail
    assert results["noaa_nws"].outcome == "empty"
    assert results["usgs_eq"].outcome == "empty"
    assert tool.exit_code(list(results.values())) == 0


@pytest.mark.asyncio
async def test_an_answer_the_parser_drops_entirely_is_a_failure() -> None:
    renamed = {"features": [{"id": "x", "properties": {"magnitude": 5.1}, "geometry": {"coordinates": [1, 2, 3]}}]}
    results = await run({"earthquake.usgs.gov": lambda r: httpx.Response(200, json=renamed)})
    assert results["usgs_eq"].outcome == "FAIL" and "none understood" in results["usgs_eq"].detail
    assert tool.exit_code(list(results.values())) == 1


@pytest.mark.asyncio
async def test_a_changed_format_is_a_failure_for_each_kind_of_source() -> None:
    results = await run(
        {
            "www.gdacs.org": lambda r: httpx.Response(200, json={"features": [{"properties": {"type": "TC"}, "geometry": {}}]}),
            "api.weather.gov": lambda r: httpx.Response(200, json={"features": [{"properties": {"headline": "x"}}]}),
            "volcanoes.usgs.gov": lambda r: httpx.Response(200, json=[{"name": "x", "level": "RED"}]),
            "services.swpc.noaa.gov": lambda r: httpx.Response(200, json=[{"planetary_k": 2}]),
            "api.open-meteo.com": lambda r: httpx.Response(200, json={"current": {"temp": 3}}),
            "marine-api.open-meteo.com": lambda r: httpx.Response(200, json={"current": {}}),
            "flood-api.open-meteo.com": lambda r: httpx.Response(200, json={"daily": {}}),
            "air-quality-api.open-meteo.com": lambda r: httpx.Response(200, json={"current": {}}),
            "api.rainviewer.com": lambda r: httpx.Response(200, json={"host": "h", "radar": {"nowcast": [{"time": 1, "path": "/p"}]}}),
        }
    )
    failed = {name for name, r in results.items() if r.outcome == "FAIL"}
    assert failed == {
        "gdacs_global",
        "noaa_nws",
        "usgs_volcano_gvp",
        "noaa_swpc",
        "open_meteo_hd",
        "open_meteo_marine",
        "open_meteo_flood",
        "open_meteo_aqi",
        "rainviewer_radar",
    }


@pytest.mark.asyncio
async def test_met_norway_without_cache_headers_is_a_failure() -> None:
    """The terms make us use Expires and Last-Modified: an answer without them needs a change of the collector."""
    results = await run({"api.met.no": lambda r: httpx.Response(200, json=samples.metno_payload())})
    assert results["met_norway_fallback"].outcome == "FAIL"
    assert "Expires" in results["met_norway_fallback"].detail


@pytest.mark.asyncio
async def test_a_removed_endpoint_fails_but_a_busy_provider_is_only_reported() -> None:
    results = await run(
        {
            "www.gdacs.org": lambda r: httpx.Response(400, text="Eventtype is required."),  # what GDACS once answered
            "api.open-meteo.com": lambda r: httpx.Response(429),
            "api.met.no": lambda r: httpx.Response(503),
            "api.weather.gov": lambda r: httpx.Response(403),
            "api.rainviewer.com": lambda r: (_ for _ in ()).throw(httpx.ConnectError("no route", request=r)),
        }
    )
    assert results["gdacs_global"].outcome == "FAIL" and "HTTP 400" in results["gdacs_global"].detail
    for source in ("open_meteo_hd", "met_norway_fallback", "noaa_nws", "rainviewer_radar"):
        assert results[source].outcome == "unreachable", source
    assert tool.exit_code(list(results.values())) == 1  # because of GDACS only
    only_busy = [r for r in results.values() if r.outcome == "unreachable"]
    assert tool.exit_code(only_busy) == 0
    assert tool.exit_code(only_busy, strict=True) == 1


@pytest.mark.asyncio
async def test_only_runs_the_chosen_sources() -> None:
    results = await run(only=["usgs_eq", "noaa_swpc"])
    assert set(results) == {"usgs_eq", "noaa_swpc"}


def test_the_table_is_markdown() -> None:
    table = tool.render([tool.Result("usgs_eq", "ok", "25 of 25 events parsed")])
    assert table.splitlines()[0] == "| Source | Outcome | Detail |"
    assert "| `usgs_eq` | ok | 25 of 25 events parsed |" in table
