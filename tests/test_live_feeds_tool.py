"""The weekly live-feeds check (``tools/check_live_feeds.py``), played against simulated providers.

The tool itself reaches the real providers and runs outside pytest (``.github/workflows/live-feeds.yml``).
Here every provider is simulated (``httpx.MockTransport``), so these tests check the *judgement* of the tool:
a good answer passes, a valid empty answer is not a failure, and an answer the parsers cannot read is.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest

from tests import firms_samples, samples, storm_samples, swic_samples
from tests import vigilance_samples as national
from tests.helpers import SimClock
from tests.test_dwd import archive, cap_document
from tests.test_metservice import BASE, synthetic_cap, synthetic_feed
from tests.test_tsunami import TsunamiServer
from tests.test_vigicrues import recorded_image_headers
from tools import check_live_feeds as tool

FIRMS_HOST = "firms.modaps.eosdis.nasa.gov"

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


def vigicrues_host(flow: bytes, image: Optional[dict[str, str]] = None, image_status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    """Vigicrues as recorded: the flow, and the head of the publication image (stamped 21 seconds after the recorded flow)."""

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ftp/img_france.png":
            return httpx.Response(image_status, headers=recorded_image_headers() if image is None else image)
        return httpx.Response(200, content=flow)

    return answer


def meteoalarm_answer(request: httpx.Request) -> httpx.Response:
    """The recorded Atom feed of France, and its recorded CAP document for any document asked."""
    if request.url.path.startswith("/api/v1/warnings/"):
        return httpx.Response(200, content=(FIXTURES / "cap_france_b77d3bc0.xml").read_bytes())
    return httpx.Response(200, content=(FIXTURES / "atom_france.xml").read_bytes())


def tsunami_host() -> Callable[[httpx.Request], httpx.Response]:
    """tsunami.gov as recorded: the list of 40 bulletins, and (the newest being an information statement) the recorded Alaska one for its CAP."""

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == storm_samples.TSUNAMI_LISTING_PATH:
            return httpx.Response(200, content=storm_samples.tsunami_fixture("previous.js"))
        return httpx.Response(200, content=storm_samples.tsunami_document(request.url.path) or storm_samples.tsunami_fixture("PAAQCAP_information_alaska.xml"))

    return answer


def nhc_host(status: bytes | None = None, bundle: bytes | int | None = None) -> Callable[[httpx.Request], httpx.Response]:
    """The NHC as recorded: Hurricane Isaias of 2026-10-09 and the ZIP of its advisory 13A (or the answer a test puts in their place)."""
    files = storm_samples.isaias_only() if status is None else status
    zipped = storm_samples.nhc_fixture("al092026_5day_013A.zip") if bundle is None else bundle

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/CurrentStorms.json":
            return httpx.Response(200, content=files)
        return httpx.Response(zipped) if isinstance(zipped, int) else httpx.Response(200, content=zipped)

    return answer


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
        "www.vigicrues.gouv.fr": vigicrues_host(full_vigicrues_flow()),
        "www.tsunami.gov": tsunami_host(),
        "www.nhc.noaa.gov": nhc_host(),
        FIRMS_HOST: firms_host()[0],
        NOMADS_HOST: nomads_answer,
        **swic_samples.wmo_hosts(),
    }


NOMADS_HOST = "nomads.ncep.noaa.gov"


def nomads_answer(request: httpx.Request) -> httpx.Response:
    """NOMADS as recorded on 2026-10-25: every run is complete, and the filter answers lead 3 of the run of 00 UTC cut to Turkey."""
    if request.method == "HEAD":
        return httpx.Response(200)
    fixture = Path(__file__).resolve().parent / "fixtures" / "gfs_nomads" / "gfs.t00z.20261025.f003.grib2"
    return httpx.Response(200, content=fixture.read_bytes())


async def run(overrides: dict[str, Callable[[httpx.Request], httpx.Response]] | None = None, **kwargs: Any) -> dict[str, tool.Result]:
    answers = {**good_answers(), **(overrides or {})}

    def handler(request: httpx.Request) -> httpx.Response:
        return answers[request.url.host](request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await tool.run_checks(client, pause_s=0.0, **kwargs)
    return {r.source: r for r in results}


@pytest.fixture(autouse=True)
def firms_as_an_instance_with_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The machine that runs the tests may hold a real key: these tests never use it. FIRMS is on (the application of the tests turns it off), and its
    floor of rows is the recording's, not the real file's (the tests that want the real one say so)."""
    monkeypatch.delenv("VIGIE_FIRMS_MAP_KEY", raising=False)
    monkeypatch.setenv("VIGIE_ENABLE_FIRMS", "1")
    monkeypatch.setattr(tool, "FIRMS_MIN_ROWS", 200)


def test_there_is_a_check_for_each_collector() -> None:
    from app.main import aggregator

    # NASA FIRMS has a check although the application of the tests has no collector for it (it is turned off there): the check says "skipped" then.
    # NOAA GFS through NOMADS is the pre-alert pilot (ADR 0011), not a collector of the aggregator: it has its check too.
    assert set(tool.CHECKS) == {c.provider_id for c in aggregator.get_all_collectors()} | {"nasa_firms", "noaa_gfs_nomads"}


@pytest.mark.asyncio
async def test_every_source_passes_when_nothing_changed() -> None:
    results = await run()
    # MetService's feed was empty the day it was recorded: a valid answer, not yet a proof that its items are read.
    # FIRMS needs no key: its static file is read like any other source (the weekly workflow has none).
    assert {k for k, v in results.items() if v.outcome != "ok"} == {"metservice_cap"}
    assert results["metservice_cap"].outcome == "empty"
    assert results["nasa_firms"].outcome == "ok"
    assert results["noaa_gfs_nomads"].outcome == "ok" and "the six fields" in results["noaa_gfs_nomads"].detail
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
            "www.tsunami.gov": lambda r: httpx.Response(200, text="<html><body>maintenance</body></html>"),
            "www.nhc.noaa.gov": lambda r: httpx.Response(200, json={"error": "maintenance"}),
        }
    )
    failed = {name for name, r in results.items() if r.outcome == "FAIL"}
    assert failed == {
        "tsunami_warning_centers",
        "nhc_storms",
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
async def test_the_hurricane_center_with_a_storm_must_give_its_cone() -> None:
    results = await run(only=["nhc_storms"])
    assert results["nhc_storms"].outcome == "ok" and "Isaias (al092026)" in results["nhc_storms"].detail


@pytest.mark.asyncio
async def test_the_hurricane_center_with_no_storm_is_a_valid_empty_answer() -> None:
    results = await run({"www.nhc.noaa.gov": nhc_host(status=storm_samples.nhc_fixture("CurrentStorms_empty.json"))}, only=["nhc_storms"])
    assert results["nhc_storms"].outcome == "empty" and "no active storm" in results["nhc_storms"].detail
    assert tool.exit_code(list(results.values())) == 0


@pytest.mark.asyncio
async def test_the_hurricane_center_fails_when_a_storm_has_no_bundle_or_no_cone_or_the_file_changed() -> None:
    no_bundle = await run({"www.nhc.noaa.gov": nhc_host(bundle=404)}, only=["nhc_storms"])
    assert no_bundle["nhc_storms"].outcome == "FAIL" and "Isaias: its forecast bundle could not be read" in no_bundle["nhc_storms"].detail
    renamed_layers = await run({"www.nhc.noaa.gov": nhc_host(bundle=storm_samples.nhc_fixture("cp012026_5day_064.zip").replace(b"5day_pgn", b"5day_xxx"))}, only=["nhc_storms"])
    assert renamed_layers["nhc_storms"].outcome == "FAIL"
    not_the_file = await run({"www.nhc.noaa.gov": nhc_host(status=b'{"storms": []}')}, only=["nhc_storms"])
    assert not_the_file["nhc_storms"].outcome == "FAIL" and "CurrentStorms" in not_the_file["nhc_storms"].detail
    garbled = await run({"www.nhc.noaa.gov": nhc_host(status=b"<html>maintenance</html>")}, only=["nhc_storms"])
    assert garbled["nhc_storms"].outcome == "FAIL"
    busy = await run({"www.nhc.noaa.gov": lambda r: httpx.Response(503)}, only=["nhc_storms"])
    assert busy["nhc_storms"].outcome == "unreachable"


@pytest.mark.asyncio
async def test_the_tsunami_centres_pass_with_a_quiet_list_and_read_the_cap_of_the_newest_bulletin() -> None:
    results = await run(only=["tsunami_warning_centers"])
    assert results["tsunami_warning_centers"].outcome == "ok"
    assert results["tsunami_warning_centers"].detail.startswith("40 bulletins listed, the CAP of the newest (PAAQ WEAK53) read")


@pytest.mark.asyncio
async def test_the_tsunami_centres_pass_with_an_event_in_force_and_name_it(sim_clock: SimClock) -> None:
    sim_clock.EPOCH = storm_samples.at("2026-10-09", "18:55")
    sim_clock.now = 0.0
    results = await run({"www.tsunami.gov": TsunamiServer(sim_clock)}, only=["tsunami_warning_centers"])
    assert results["tsunami_warning_centers"].outcome == "ok"
    assert "1 event(s) in force: PHEB 26282000 (ORANGE)" in results["tsunami_warning_centers"].detail


@pytest.mark.asyncio
async def test_the_tsunami_centres_fail_on_a_list_or_a_message_they_cannot_read() -> None:
    changed = storm_samples.tsunami_fixture("previous.js").replace(b"'TWCID'", b"'CENTRE'")
    unreadable_list = await run({"www.tsunami.gov": lambda r: httpx.Response(200, content=changed)}, only=["tsunami_warning_centers"])
    assert unreadable_list["tsunami_warning_centers"].outcome == "FAIL"
    empty_list = await run({"www.tsunami.gov": lambda r: httpx.Response(200, text="var prevEvents = {'event' : []};")}, only=["tsunami_warning_centers"])
    assert empty_list["tsunami_warning_centers"].outcome == "FAIL" and "none that could be read" in empty_list["tsunami_warning_centers"].detail

    def html_messages(request: httpx.Request) -> httpx.Response:
        if request.url.path == storm_samples.TSUNAMI_LISTING_PATH:
            return httpx.Response(200, content=storm_samples.tsunami_fixture("previous.js"))
        return httpx.Response(200, text="<html><body>maintenance</body></html>")

    not_cap = await run({"www.tsunami.gov": html_messages}, only=["tsunami_warning_centers"])
    assert not_cap["tsunami_warning_centers"].outcome == "FAIL"
    busy = await run({"www.tsunami.gov": lambda r: httpx.Response(429)}, only=["tsunami_warning_centers"])
    assert busy["tsunami_warning_centers"].outcome == "unreachable"


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
    quiet = await national_run("www.vigicrues.gouv.fr", vigicrues_host(json.dumps(calm).encode()), "vigicrues_flow")
    assert quiet["vigicrues_flow"].outcome == "empty" and "all 341 sections are green" in quiet["vigicrues_flow"].detail

    short = await national_run(
        "www.vigicrues.gouv.fr",
        vigicrues_host(national.national_fixture("vigicrues", "InfoVigiCru_subset.geojson")),
        "vigicrues_flow",
    )
    assert short["vigicrues_flow"].outcome == "FAIL" and "only 31 sections" in short["vigicrues_flow"].detail

    broken = json.loads(full_vigicrues_flow())
    broken["features"][0]["geometry"] = None
    unreadable = await national_run("www.vigicrues.gouv.fr", vigicrues_host(json.dumps(broken).encode()), "vigicrues_flow")
    assert unreadable["vigicrues_flow"].outcome == "FAIL" and "could not be read" in unreadable["vigicrues_flow"].detail


@pytest.mark.asyncio
async def test_vigicrues_the_publication_image_must_still_follow_the_flow() -> None:
    """The collector reads the 2.2 MB flow only when this image changed: the weekly run tells the day it stops being a signal."""
    flow = full_vigicrues_flow()
    kept = await national_run("www.vigicrues.gouv.fr", vigicrues_host(flow), "vigicrues_flow")
    assert kept["vigicrues_flow"].outcome == "ok"  # 21 seconds after the flow, as recorded

    for label, host, expected in (
        ("gone", vigicrues_host(flow, image_status=404), "HTTP 404"),
        ("no validator", vigicrues_host(flow, image={"Last-Modified": "Tue, 20 Oct 2026 07:55:36 GMT"}), "no Last-Modified or no ETag"),
        ("not a date", vigicrues_host(flow, image={"Last-Modified": "yesterday", "ETag": '"x"'}), "not a date"),
        (
            "a day late",
            vigicrues_host(flow, image={"Last-Modified": "Wed, 21 Oct 2026 07:55:36 GMT", "ETag": '"x"'}),
            "+1440 minutes",
        ),
        (
            "long before",
            vigicrues_host(flow, image={"Last-Modified": "Tue, 20 Oct 2026 07:00:00 GMT", "ETag": '"x"'}),
            "-55 minutes",
        ),
    ):
        result = await national_run("www.vigicrues.gouv.fr", host, "vigicrues_flow")
        assert result["vigicrues_flow"].outcome == "FAIL" and expected in result["vigicrues_flow"].detail, label
        assert "every hour" in result["vigicrues_flow"].detail, label


# ---------------------------------------------------------------- the WMO registry and its services


@pytest.fixture(autouse=True)
def _no_pause_between_wmo_services(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool, "WMO_SAMPLE_PAUSE_S", 0.0)


@pytest.mark.asyncio
async def test_the_wmo_registry_passes_and_says_what_the_snapshot_does_not_know() -> None:
    results = await run(only=["wmo_swic_registry"])
    assert results["wmo_swic_registry"].outcome == "ok"
    assert results["wmo_swic_registry"].detail.startswith("140 services (0 the bundled snapshot does not know, 0 it knows that are gone)")
    newcomer = {"sourceId": "zz-new-en", "authorityCountry": "ES", "capAlertFeed": "https://example.org/rss.xml", "byLanguage": [{"name": "New"}]}
    changed = await run(swic_samples.wmo_hosts(registry=swic_samples.registry_json(extra=[newcomer])), only=["wmo_swic_registry"])
    assert changed["wmo_swic_registry"].outcome == "ok" and "1 the bundled snapshot does not know" in changed["wmo_swic_registry"].detail
    assert "zz-new-en" in changed["wmo_swic_registry"].detail


@pytest.mark.asyncio
async def test_the_wmo_registry_fails_when_it_is_truncated_or_a_verified_service_moved_or_a_country_has_no_border() -> None:
    few = await run(swic_samples.wmo_hosts(registry=swic_samples.registry_json(drop={s.source_id for s in swic_samples.bundled()[20:]})), only=["wmo_swic_registry"])
    assert few["wmo_swic_registry"].outcome == "FAIL" and "truncated" in few["wmo_swic_registry"].detail
    moved = await run(
        swic_samples.wmo_hosts(registry=swic_samples.registry_json(change={"mx-smn-es": {"capAlertFeed": "https://example.org/new.xml"}})),
        only=["wmo_swic_registry"],
    )
    assert moved["wmo_swic_registry"].outcome == "FAIL" and "mx-smn-es" in moved["wmo_swic_registry"].detail
    # Bonaire is the one place of the registry's countries that Natural Earth draws inside another country: it has no border of its own.
    bonaire = {"sourceId": "bq-met-en", "authorityCountry": "BQ", "capAlertFeed": "https://example.org/bq.xml", "byLanguage": [{"name": "Meteo Bonaire"}]}
    no_border = await run(swic_samples.wmo_hosts(registry=swic_samples.registry_json(extra=[bonaire])), only=["wmo_swic_registry"])
    assert no_border["wmo_swic_registry"].outcome == "FAIL" and "BQ" in no_border["wmo_swic_registry"].detail
    assert "--countries-only" in no_border["wmo_swic_registry"].detail
    not_a_registry = await run({"severeweather.wmo.int": lambda r: httpx.Response(200, json={"error": "maintenance"})}, only=["wmo_swic_registry"])
    assert not_a_registry["wmo_swic_registry"].outcome == "FAIL"
    busy = await run({"severeweather.wmo.int": lambda r: httpx.Response(503)}, only=["wmo_swic_registry"])
    assert busy["wmo_swic_registry"].outcome == "unreachable"


@pytest.mark.asyncio
async def test_the_verified_wmo_services_and_the_sample_are_read(sim_clock: SimClock) -> None:
    sim_clock.advance(swic_samples.at(swic_samples.RECORDED_AT))
    results = await run(only=["wmo_swic"])
    result = results["wmo_swic"]
    assert result.outcome == "ok", result.detail
    for sid in ("ar-smn-es", "bb-dem-xx", "dz-onm-fr", "es-aemet-es", "jm-jms-en", "mx-smn-es", "ph-pagasa-en", "ru-roshydromet-en", "in-ndma-xx"):
        assert sid in result.detail, sid
    assert "Minor" in result.detail  # the Philippines' tropical cyclone signal: a level word in no table, said, not a failure


@pytest.mark.asyncio
async def test_a_wmo_service_fails_when_its_licence_line_is_gone_or_nothing_it_lists_is_cap(sim_clock: SimClock) -> None:
    sim_clock.advance(swic_samples.at(swic_samples.RECORDED_AT))
    url = swic_samples.index_url("index_ar_smn.xml")
    server = swic_samples.FixtureServer(*swic_samples.MANIFEST["indexes"])
    server.bodies[url] = server.bodies[url].replace(b"CC BY 4.0", b"All rights reserved")
    no_licence = await run(swic_samples.wmo_hosts(server), only=["wmo_swic"])
    assert no_licence["wmo_swic"].outcome == "FAIL" and "ar-smn-es" in no_licence["wmo_swic"].detail and "licence line" in no_licence["wmo_swic"].detail
    broken = swic_samples.FixtureServer(*swic_samples.MANIFEST["indexes"])
    for document in broken.documents:
        if "conagua" in document:
            broken.documents[document] = b"<html><body>maintenance</body></html>"
    not_cap = await run(swic_samples.wmo_hosts(broken), only=["wmo_swic"])
    assert not_cap["wmo_swic"].outcome == "FAIL" and "mx-smn-es" in not_cap["wmo_swic"].detail
    assert tool.exit_code(list(not_cap.values())) == 1


@pytest.mark.asyncio
async def test_wmo_services_that_do_not_answer_are_reported_not_failed() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated", request=request)

    hosts = {host: down for host in swic_samples.wmo_hosts()}
    results = await run(hosts, only=["wmo_swic"])
    assert results["wmo_swic"].outcome == "unreachable"
    assert tool.exit_code(list(results.values())) == 0


# ------------------------------------------------------------------------------------------------ NASA FIRMS


def firms_host(
    static: Optional[firms_samples.FirmsServer] = None, area: Optional[firms_samples.AreaServer] = None
) -> tuple[Callable[[httpx.Request], httpx.Response], list[httpx.Request]]:
    """A simulated FIRMS (the static files and the area API) and the requests it received."""
    static = static or firms_samples.FirmsServer(latency_ticks=0)
    area = area or firms_samples.AreaServer(latency_ticks=0)
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return (area if request.url.path.startswith("/api/") else static)._respond(request)

    return answer, seen


async def firms_result(answer: Callable[[httpx.Request], httpx.Response]) -> tool.Result:
    return (await run({FIRMS_HOST: answer}, only=["nasa_firms"]))["nasa_firms"]


@pytest.mark.asyncio
async def test_firms_is_skipped_when_it_is_turned_off_and_nothing_is_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIGIE_ENABLE_FIRMS", "0")
    answer, seen = firms_host()
    result = await firms_result(answer)
    assert result.outcome == "skipped" and "VIGIE_ENABLE_FIRMS=0" in result.detail
    assert seen == []
    assert tool.exit_code([result]) == 0 and tool.exit_code([result], strict=True) == 0  # not a failure, and not even a "not reachable"
    assert "| `nasa_firms` | skipped |" in tool.render([result])


@pytest.mark.asyncio
async def test_firms_reads_the_static_file_of_one_source_with_no_key_and_describes_it() -> None:
    answer, seen = firms_host()
    result = await firms_result(answer)
    assert result.outcome == "ok", result.detail
    assert len(seen) == 1 and seen[0].url.path == "/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_48h.csv"  # one request, the file of the instance's window
    rows = len(firms_samples.static_rows(firms_samples.N20))
    assert f"{rows} detections" in result.detail and "VIIRS_NOAA20_NRT 48 h file" in result.detail and "satellites: N20" in result.detail
    assert "written Tue, 20 Oct 2026 19:40:30 GMT" in result.detail and "area API" not in result.detail
    assert tool.exit_code([result], strict=True) == 0


@pytest.mark.asyncio
async def test_firms_with_a_key_reads_one_world_day_of_the_area_api_too(monkeypatch: pytest.MonkeyPatch, sim_clock: SimClock) -> None:
    sim_clock.EPOCH = firms_samples.NOW
    monkeypatch.setenv("VIGIE_FIRMS_MAP_KEY", firms_samples.KEY)
    answer, seen = firms_host()
    result = await firms_result(answer)
    assert result.outcome == "ok", result.detail
    assert [request.url.path.split("/")[1] for request in seen] == ["data", "api"]
    assert seen[1].url.path.split("/")[5:] == ["VIIRS_NOAA20_NRT", "world", "1", "2026-10-19"]  # 36 of the 5,000 transactions; yesterday's file
    assert f"VIIRS_NOAA20_NRT 2026-10-19, area API: {len(firms_samples.area_rows(firms_samples.N20))} detections" in result.detail
    assert firms_samples.KEY not in result.detail and firms_samples.KEY not in tool.render([result])


@pytest.mark.asyncio
async def test_firms_a_cut_file_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool, "FIRMS_MIN_ROWS", 5_000)  # the recording is shorter than a real file: the real floor refuses it
    answer, _ = firms_host()
    result = await firms_result(answer)
    assert result.outcome == "FAIL" and "cut or changed" in result.detail
    assert tool.exit_code([result]) == 1


@pytest.mark.asyncio
async def test_firms_a_file_that_stops_before_its_end_is_not_a_good_file() -> None:
    """The announced size is checked by the collector: a file cut at a line break would be a valid CSV."""
    data = firms_samples.static_csv(firms_samples.N20)
    cut = b"\n".join(data.split(b"\n")[:50]) + b"\n"
    result = await firms_result(lambda request: httpx.Response(200, headers={"Content-Length": str(len(data))}, content=cut))
    assert result.outcome == "FAIL" and "announced" in result.detail


@pytest.mark.asyncio
async def test_firms_a_changed_format_is_a_failure() -> None:
    server = firms_samples.FirmsServer(latency_ticks=0)
    recorded = firms_samples.static_text(firms_samples.N20)
    lines = recorded.splitlines()
    without_power = [lines[0]] + [",".join(line.split(",")[:11] + ["", line.split(",")[-1]]) for line in lines[1:]]
    cases = {
        "renamed columns": recorded.replace("latitude,", "lat,", 1).encode("utf-8"),
        "not a CSV": b"<html><body>Service unavailable</body></html>",
        "empty": b"",
        "confidence in numbers": recorded.replace(",low,", ",10,").replace(",nominal,", ",50,").replace(",high,", ",90,").encode("utf-8"),
        "no power": ("\n".join(without_power) + "\n").encode("utf-8"),
    }
    for name, body in cases.items():
        server.answers[(firms_samples.N20, 48)] = body
        answer, _ = firms_host(server)
        result = await firms_result(answer)
        assert result.outcome == "FAIL", (name, result)


@pytest.mark.asyncio
async def test_firms_rows_that_cannot_be_read_are_a_failure_beyond_one_in_a_hundred() -> None:
    server = firms_samples.FirmsServer(latency_ticks=0)
    lines = firms_samples.static_text(firms_samples.N20).splitlines()
    broken = [lines[0]] + [line if index % 4 else "x" + line[line.index(","):] for index, line in enumerate(lines[1:])]  # a quarter of the rows lose their position
    server.answers[(firms_samples.N20, 48)] = ("\n".join(broken) + "\n").encode("utf-8")
    answer, _ = firms_host(server)
    result = await firms_result(answer)
    assert result.outcome == "FAIL" and "could not be read" in result.detail


@pytest.mark.asyncio
async def test_firms_a_busy_or_unreachable_server_is_only_reported_and_a_refused_query_fails() -> None:
    for status, outcome in ((503, "unreachable"), (504, "unreachable"), (429, "unreachable"), (403, "unreachable"), (404, "FAIL"), (400, "FAIL")):
        server = firms_samples.FirmsServer(latency_ticks=0)
        server.status[firms_samples.N20] = (status, b"no")
        answer, _ = firms_host(server)
        result = await firms_result(answer)
        assert result.outcome == outcome, (status, result)
    for failing in ({firms_samples.N20}, set()):  # a connection refused, and a server that stalls (what httpx raises after 25 s without a byte)
        server = firms_samples.FirmsServer(latency_ticks=0)
        if failing:
            server.failing = failing
        else:
            server.stall = {firms_samples.N20: 0}
            server.stall_timeout = True
        answer, _ = firms_host(server)
        result = await firms_result(answer)
        assert result.outcome == "unreachable", result
    assert tool.exit_code([result]) == 0 and tool.exit_code([result], strict=True) == 1


@pytest.mark.asyncio
async def test_firms_a_refused_key_is_a_failure_and_the_key_is_not_in_the_report(monkeypatch: pytest.MonkeyPatch, sim_clock: SimClock) -> None:
    sim_clock.EPOCH = firms_samples.NOW
    other = "fedcba9876543210fedcba9876543210"
    monkeypatch.setenv("VIGIE_FIRMS_MAP_KEY", other)
    answer, _ = firms_host()  # the file answers, and the simulated area API says "Invalid MAP_KEY." with a 200, as the real one does
    result = await firms_result(answer)
    assert result.outcome == "FAIL" and "Invalid MAP_KEY" in result.detail
    assert other not in result.detail and other not in tool.render([result])


@pytest.mark.asyncio
async def test_firms_a_key_that_is_not_one_is_not_used_and_never_quoted(monkeypatch: pytest.MonkeyPatch) -> None:
    odd = "not a key / with spaces and a slash"
    monkeypatch.setenv("VIGIE_FIRMS_MAP_KEY", odd)
    answer, seen = firms_host()
    result = await firms_result(answer)
    assert result.outcome == "ok" and len(seen) == 1 and odd not in result.detail  # the file needs none
