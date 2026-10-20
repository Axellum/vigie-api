"""The weekly live-feeds check (``tools/check_live_feeds.py``), played against simulated providers.

The tool itself reaches the real providers and runs outside pytest (``.github/workflows/live-feeds.yml``).
Here every provider is simulated (``httpx.MockTransport``), so these tests check the *judgement* of the tool:
a good answer passes, a valid empty answer is not a failure, and an answer the parsers cannot read is.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests import samples
from tests import vigilance_samples as national
from tests.test_dwd import archive, cap_document
from tests.test_metservice import BASE, synthetic_cap, synthetic_feed
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
                           "status": "Actual", "messageType": "Alert", "sent": "2026-10-18T13:00:00+00:00"},
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
FIXTURES = Path(__file__).parent / "fixtures" / "meteoalarm"
METNO_HEADERS = {
    "Expires": "Tue, 20 Oct 2026 12:00:00 GMT",
    "Date": "Tue, 20 Oct 2026 11:30:00 GMT",
    "Last-Modified": "Tue, 20 Oct 2026 11:25:00 GMT",
}


def full_vigicrues_flow() -> bytes:
    """The recorded subset (31 sections) repeated up to the size of the real flow (341 sections), under other codes."""
    payload = json.loads(national.national_fixture("vigicrues", "InfoVigiCru_subset.geojson"))
    features = payload["features"]
    copies = [json.loads(json.dumps(f)) for _ in range(10) for f in features]
    for index, feature in enumerate(copies):
        feature["properties"]["CdEntCru"] = f"{feature['properties']['CdEntCru']}-{index}"
    payload["features"] = features + copies
    return json.dumps(payload).encode()


def meteoalarm_answer(request: httpx.Request) -> httpx.Response:
    """The recorded Atom feed of France, and its recorded CAP document for any document asked."""
    if request.url.path.startswith("/api/v1/warnings/"):
        return httpx.Response(200, content=(FIXTURES / "cap_france_b77d3bc0.xml").read_bytes())
    return httpx.Response(200, content=(FIXTURES / "atom_france.xml").read_bytes())


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
        "feeds.meteoalarm.org": meteoalarm_answer,
        "opendata.dwd.de": lambda r: httpx.Response(200, content=national.national_fixture("dwd", "status_district_mul.zip")),
        "api.weather.gc.ca": lambda r: httpx.Response(200, content=national.national_fixture("eccc", "weather_alerts_items.json")),
        "alerts.metservice.com": lambda r: httpx.Response(200, content=national.national_fixture("metservice", "rss_empty.xml")),
        "www.vigicrues.gouv.fr": lambda r: httpx.Response(200, content=full_vigicrues_flow()),
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
    # MetService's feed was empty the day it was recorded: a valid answer, not yet a proof that its items are read.
    assert {k for k, v in results.items() if v.outcome != "ok"} == {"metservice_cap"}
    assert results["metservice_cap"].outcome == "empty"
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
            "feeds.meteoalarm.org": lambda r: httpx.Response(200, text="<html><body>maintenance</body></html>"),
            "opendata.dwd.de": lambda r: httpx.Response(200, text="<html><body>maintenance</body></html>"),
            "api.weather.gc.ca": lambda r: httpx.Response(200, json={"error": "maintenance"}),
            "alerts.metservice.com": lambda r: httpx.Response(200, text="<html><body>maintenance</body></html>"),
            "www.vigicrues.gouv.fr": lambda r: httpx.Response(200, json={"error": "maintenance"}),
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
        "meteoalarm_atom",
        "meteoalarm_cap",
        "dwd_cap",
        "eccc_alerts",
        "metservice_cap",
        "vigicrues_flow",
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


EMPTY_FEED = (FIXTURES / "atom_empty.xml").read_bytes()


@pytest.mark.asyncio
async def test_a_meteoalarm_feed_with_no_warning_is_not_a_failure() -> None:
    results = await run({"feeds.meteoalarm.org": lambda r: httpx.Response(200, content=EMPTY_FEED)})
    assert results["meteoalarm_atom"].outcome == "empty"
    assert results["meteoalarm_cap"].outcome == "empty"
    assert tool.exit_code(list(results.values())) == 0


@pytest.mark.asyncio
async def test_a_level_or_phenomenon_the_tables_do_not_know_is_a_failure() -> None:
    """A new awareness word must make the weekly run red, so that the tables of the collector are extended."""
    feed = (FIXTURES / "atom_france.xml").read_text(encoding="utf-8").replace("Yellow Flooding Warning", "Mauve Tsunamis Warning")
    results = await run({"feeds.meteoalarm.org": lambda r: httpx.Response(200, content=feed.encode())})
    assert results["meteoalarm_atom"].outcome == "FAIL"
    assert "Mauve" in results["meteoalarm_atom"].detail and "Tsunamis" in results["meteoalarm_atom"].detail


@pytest.mark.asyncio
async def test_a_french_code_without_a_shape_is_a_failure() -> None:
    feed = (FIXTURES / "atom_france.xml").read_text(encoding="utf-8").replace("<value>FR252</value>", "<value>FR999</value>")
    assert feed != (FIXTURES / "atom_france.xml").read_text(encoding="utf-8"), "the fixture no longer has FR252"
    results = await run({"feeds.meteoalarm.org": lambda r: httpx.Response(200, content=feed.encode())})
    assert results["meteoalarm_atom"].outcome == "FAIL"
    assert "FR999" in results["meteoalarm_atom"].detail


@pytest.mark.asyncio
async def test_a_missing_french_cap_document_is_a_failure_but_not_a_server_error() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v1/warnings/"):
            return httpx.Response(404)
        return meteoalarm_answer(request)

    results = await run({"feeds.meteoalarm.org": answer})
    assert results["meteoalarm_atom"].outcome == "ok"
    assert results["meteoalarm_cap"].outcome == "FAIL" and "HTTP 404" in results["meteoalarm_cap"].detail

    def busy(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v1/warnings/"):
            return httpx.Response(503)
        return meteoalarm_answer(request)

    results = await run({"feeds.meteoalarm.org": busy})
    assert results["meteoalarm_cap"].outcome == "unreachable"


@pytest.mark.asyncio
async def test_the_french_feed_is_asked_once_for_both_meteoalarm_checks() -> None:
    paths: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return meteoalarm_answer(request)

    await run({"feeds.meteoalarm.org": answer}, only=["meteoalarm_atom", "meteoalarm_cap"])
    assert len([p for p in paths if p.startswith("/feeds/")]) == 1
    assert len([p for p in paths if p.startswith("/api/v1/warnings/")]) == 1


# ---------------------------------------------------------------- the national services


@pytest.mark.asyncio
async def test_the_national_checks_describe_what_they_read() -> None:
    results = await run(only=["dwd_cap", "eccc_alerts", "vigicrues_flow"])
    assert results["dwd_cap"].detail == "7 areas of 4 documents read, all with a polygon"
    assert results["eccc_alerts"].detail == "36 of 36 alerts read, all with a polygon"
    assert results["vigicrues_flow"].detail == "209 of 341 sections above green (187 yellow, 22 orange)"


def national_run(host: str, answer: Callable[[httpx.Request], httpx.Response], source: str) -> Any:
    return run({host: answer}, only=[source])


@pytest.mark.asyncio
async def test_dwd_a_quiet_status_is_empty_and_an_unknown_word_is_a_failure() -> None:
    empty = national.national_fixture("dwd", "difference_district_mul.zip")
    quiet = await national_run("opendata.dwd.de", lambda r: httpx.Response(200, content=empty), "dwd_cap")
    assert quiet["dwd_cap"].outcome == "empty"

    for content, word in ((archive(cap_document(severity="Catastrophic")), "Catastrophic"), (archive(cap_document(ii="777")), "777")):
        results = await national_run("opendata.dwd.de", lambda r, c=content: httpx.Response(200, content=c), "dwd_cap")
        assert results["dwd_cap"].outcome == "FAIL" and word in results["dwd_cap"].detail


@pytest.mark.asyncio
async def test_dwd_an_area_without_a_polygon_is_a_failure() -> None:
    content = archive(cap_document(areas=[{"name": "Ohne Form", "cell": "811000005", "polygons": []}]))
    results = await national_run("opendata.dwd.de", lambda r: httpx.Response(200, content=content), "dwd_cap")
    assert results["dwd_cap"].outcome == "FAIL" and "without a polygon" in results["dwd_cap"].detail


@pytest.mark.asyncio
async def test_dwd_a_busy_server_is_only_reported() -> None:
    results = await national_run("opendata.dwd.de", lambda r: httpx.Response(503), "dwd_cap")
    assert results["dwd_cap"].outcome == "unreachable"


@pytest.mark.asyncio
async def test_eccc_no_alert_is_empty_and_an_unknown_colour_or_status_is_a_failure() -> None:
    quiet = await national_run("api.weather.gc.ca", lambda r: httpx.Response(200, json={"features": []}), "eccc_alerts")
    assert quiet["eccc_alerts"].outcome == "empty"

    def changed(**properties: str) -> Callable[[httpx.Request], httpx.Response]:
        payload = json.loads(national.national_fixture("eccc", "weather_alerts_items.json"))
        payload["features"][0]["properties"].update(properties)
        return lambda r: httpx.Response(200, json=payload)

    violet = await national_run("api.weather.gc.ca", changed(risk_colour_en="violet"), "eccc_alerts")
    assert violet["eccc_alerts"].outcome == "FAIL" and "violet" in violet["eccc_alerts"].detail
    upgraded = await national_run("api.weather.gc.ca", changed(status_en="upgraded"), "eccc_alerts")
    assert upgraded["eccc_alerts"].outcome == "FAIL" and "upgraded" in upgraded["eccc_alerts"].detail
    statement = await national_run("api.weather.gc.ca", changed(alert_type="statement", risk_colour_en="grey"), "eccc_alerts")
    assert statement["eccc_alerts"].outcome == "ok"  # a statement has none of the three colours: not a failure

    # REAL: GeoMet keeps listing an alert for a while after it ended, with status_en "ended" (seen live on 2026-10-20). Not a failure.
    payload = json.loads(national.national_fixture("eccc", "weather_alerts_items.json"))
    payload["features"].append(json.loads(national.national_fixture("eccc", "ended_item.json")))
    ended = await national_run("api.weather.gc.ca", lambda r: httpx.Response(200, json=payload), "eccc_alerts")
    assert ended["eccc_alerts"].outcome == "ok" and ended["eccc_alerts"].detail == "36 of 37 alerts read, all with a polygon"


@pytest.mark.asyncio
async def test_eccc_a_removed_endpoint_is_a_failure() -> None:
    results = await national_run("api.weather.gc.ca", lambda r: httpx.Response(404), "eccc_alerts")
    assert results["eccc_alerts"].outcome == "FAIL" and "HTTP 404" in results["eccc_alerts"].detail


@pytest.mark.asyncio
async def test_metservice_a_readable_item_is_ok_and_an_unreadable_one_fails() -> None:
    """The item and the document are SYNTHETIC (tests/test_metservice.py): MetService had no warning the day it was recorded."""
    address = BASE + "wellington.cap"

    def serve(feed: bytes, documents: dict[str, bytes]) -> Callable[[httpx.Request], httpx.Response]:
        def answer(request: httpx.Request) -> httpx.Response:
            content = feed if str(request.url).endswith("/cap/rss") else documents.get(str(request.url))
            return httpx.Response(404) if content is None else httpx.Response(200, content=content)

        return answer

    ok = await national_run("alerts.metservice.com", serve(synthetic_feed(address), {address: synthetic_cap("w")}), "metservice_cap")
    assert ok["metservice_cap"].outcome == "ok" and "1 warnings from 1 items" in ok["metservice_cap"].detail
    broken = await national_run("alerts.metservice.com", serve(synthetic_feed(address), {address: b"<html/>"}), "metservice_cap")
    assert broken["metservice_cap"].outcome == "FAIL" and "1 of 1 items" in broken["metservice_cap"].detail
    levelless = synthetic_cap("w", headline="Heavy Rain Warning", colour=None)  # neither a ColourCode nor a colour word
    unknown = await national_run("alerts.metservice.com", serve(synthetic_feed(address), {address: levelless}), "metservice_cap")
    assert unknown["metservice_cap"].outcome == "FAIL" and "derive_level" in unknown["metservice_cap"].detail
    # What the guide says is always there: the ColourCode (here missing, the headline still gives a level), a listed event word, a polygon.
    for document, expected in (
        (synthetic_cap("w", colour=None), "no ColourCode"),
        (synthetic_cap("w", event="Heavy Rain Warning"), "event words the guide does not list"),
        (synthetic_cap("w", polygon=None), "1 warnings without a polygon"),
    ):
        outcome = await national_run("alerts.metservice.com", serve(synthetic_feed(address), {address: document}), "metservice_cap")
        assert outcome["metservice_cap"].outcome == "FAIL" and expected in outcome["metservice_cap"].detail


@pytest.mark.asyncio
async def test_vigicrues_a_calm_day_is_empty_a_short_flow_or_an_unreadable_section_is_a_failure() -> None:
    calm = json.loads(full_vigicrues_flow())
    for feature in calm["features"]:
        feature["properties"]["NivInfViCr"] = 1
    quiet = await national_run("www.vigicrues.gouv.fr", lambda r: httpx.Response(200, json=calm), "vigicrues_flow")
    assert quiet["vigicrues_flow"].outcome == "empty" and "all 341 sections are green" in quiet["vigicrues_flow"].detail

    short = await national_run(
        "www.vigicrues.gouv.fr",
        lambda r: httpx.Response(200, content=national.national_fixture("vigicrues", "InfoVigiCru_subset.geojson")),
        "vigicrues_flow",
    )
    assert short["vigicrues_flow"].outcome == "FAIL" and "only 31 sections" in short["vigicrues_flow"].detail

    broken = json.loads(full_vigicrues_flow())
    broken["features"][0]["geometry"] = None
    unreadable = await national_run("www.vigicrues.gouv.fr", lambda r: httpx.Response(200, json=broken), "vigicrues_flow")
    assert unreadable["vigicrues_flow"].outcome == "FAIL" and "could not be read" in unreadable["vigicrues_flow"].detail
