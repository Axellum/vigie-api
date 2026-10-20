"""The National Hurricane Center collector: the active storms, their cone and track, and what is asked of the server.

The data is the NHC's own, recorded: the file of the active storms of 2026-10-09 23:58 UTC (Hurricane Isaias over the Florida
Panhandle, and two Pacific storms), the ZIP of shapefiles of Isaias's advisory 13A, and the bundle of a storm on the date
line (``tests/fixtures/nhc``). Nothing touches the network: the server is an ``httpx.MockTransport``.
"""

from __future__ import annotations

import copy
import io
import json
import zipfile
from typing import Any, Callable, Optional

import httpx
import pytest
from shapely.geometry import Point, shape

from app.collectors.base import CollectorUnavailableError
from app.collectors.nhc import (
    NHCCollector,
    cyclone_key,
    hazard_of,
    level_of,
    parse_current_storms,
    parse_forecast_bundle,
)
from app.models.schemas import HazardCategory, SeverityLevel
from tests import storm_samples as S
from tests.helpers import SimClock

ISAIAS_ZIP = "https://www.nhc.noaa.gov/gis/forecast/archive/al092026_5day_013A.zip"
ISSUED = 1791590400  # 2026-10-10 00:00 UTC, the advisory 13A


def isaias() -> Any:
    return parse_current_storms(S.current_storms()).storms[0]


# ------------------------------------------------------------------------------------------------ the status file


def test_the_status_file_of_three_storms_is_read() -> None:
    state = parse_current_storms(S.current_storms())
    assert state.unreadable == 0
    assert [(s.id, s.name, s.classification, s.intensity_kt, s.lat, s.lon) for s in state.storms] == [
        ("al092026", "Isaias", "HU", 95.0, 30.1, -86.6),
        ("ep182026", "Rachel", "TS", 40.0, 23.6, -123.7),
        ("ep202026", "Simon", "HU", 95.0, 16.4, -104.6),
    ]
    first = state.storms[0]
    assert (first.pressure_hpa, first.movement_dir_deg, first.movement_speed_mph) == (967.0, 10.0, 18.0)
    assert first.advisory == "013a" and first.advisory_issued_utc == ISSUED == first.last_update_utc
    assert first.advisory_url == "https://www.nhc.noaa.gov/text/MIATCPAT4.shtml"
    assert first.bundle_url == ISAIAS_ZIP


def test_a_file_without_storm_is_an_empty_list_not_an_error() -> None:
    assert parse_current_storms(json.loads(S.nhc_fixture("CurrentStorms_empty.json"))).storms == ()


@pytest.mark.parametrize("payload", [{}, [], {"activeStorms": "none"}, {"activeStorms": None}, "<html>maintenance</html>", None])
def test_what_is_not_the_status_file_is_refused(payload: object) -> None:
    with pytest.raises(ValueError, match="CurrentStorms"):
        parse_current_storms(payload)


def test_numbers_are_read_whether_the_nhc_writes_them_as_numbers_or_as_text() -> None:
    """The real file writes the intensity and the pressure as strings and the position both ways."""
    payload = S.current_storms("CurrentStorms_2026-10-20-0324.json")
    storm = payload["activeStorms"][0]
    assert isinstance(storm["intensity"], str)
    assert parse_current_storms(payload).storms[0].intensity_kt == 30.0
    storm["intensity"], storm["pressure"] = 35, 1004
    del storm["latitudeNumeric"], storm["longitudeNumeric"]  # only "19.5N" and "108.0W" are left
    read = parse_current_storms(payload).storms[0]
    assert (read.intensity_kt, read.pressure_hpa, read.lat, read.lon) == (35.0, 1004.0, 19.5, -108.0)


def test_an_entry_that_cannot_be_read_is_counted_and_never_guessed() -> None:
    payload = S.current_storms()
    broken = copy.deepcopy(payload["activeStorms"][0])
    del broken["latitudeNumeric"], broken["latitude"]  # no position
    wrong_id = copy.deepcopy(payload["activeStorms"][1])
    wrong_id["id"] = "storm-7"
    payload["activeStorms"] = [broken, wrong_id, "not an object", payload["activeStorms"][2]]
    state = parse_current_storms(payload)
    assert [s.name for s in state.storms] == ["Simon"]
    assert state.unreadable == 3


def test_a_storm_with_no_intensity_keeps_its_hazard_without_a_value() -> None:
    payload = S.current_storms("CurrentStorms_2026-10-20-0324.json")
    payload["activeStorms"][0]["intensity"] = ""
    storm = parse_current_storms(payload).storms[0]
    assert storm.intensity_kt is None
    hazard = hazard_of(storm)
    assert hazard.value_primary is None and hazard.severity == SeverityLevel.GREEN


def test_only_an_https_zip_of_the_nhc_own_server_is_followed() -> None:
    for url in (
        "http://www.nhc.noaa.gov/gis/forecast/archive/al092026_5day_013A.zip",  # not https
        "https://evil.example/gis/al092026_5day_013A.zip",  # not the NHC's server
        "https://www.nhc.noaa.gov/gis/forecast/archive/al092026_5day_013A.exe",  # not a ZIP
    ):
        payload = S.current_storms()
        payload["activeStorms"][0]["forecastTrack"]["zipFile"] = url
        assert parse_current_storms(payload).storms[0].bundle_url is None, url


# ------------------------------------------------------------------------------------------------ the hazard


def test_a_hurricane_is_a_hazard_at_its_position_with_the_wind_in_kmh() -> None:
    hazard = hazard_of(isaias())
    assert (hazard.id, hazard.category, hazard.title) == ("nhc_al092026", HazardCategory.HURRICANE, "Hurricane Isaias")
    assert (hazard.lat, hazard.lon) == (30.1, -86.6)
    assert (hazard.value_primary, hazard.value_unit) == (pytest.approx(175.9), "km/h")  # 95 knots
    assert hazard.severity == SeverityLevel.ORANGE
    assert hazard.source == "NHC" and hazard.sources == ["NHC"] and hazard.timestamp_utc == ISSUED
    assert hazard.attribution is not None and "National Hurricane Center" in hazard.attribution
    assert hazard.event_key == cyclone_key("Isaias", 2026) == "cyclone:isaias:2026"


@pytest.mark.parametrize(
    ("knots", "level"),
    [(None, SeverityLevel.GREEN), (25, SeverityLevel.GREEN), (33, SeverityLevel.GREEN), (34, SeverityLevel.YELLOW), (63, SeverityLevel.YELLOW),
     (64, SeverityLevel.ORANGE), (95, SeverityLevel.ORANGE), (96, SeverityLevel.RED), (140, SeverityLevel.RED)],
)
def test_our_level_follows_the_saffir_simpson_thresholds(knots: Optional[float], level: SeverityLevel) -> None:
    """Tropical storm from 34 kt, hurricane from 64, major hurricane (category 3) from 96: the NHC's own thresholds."""
    assert level_of(knots) == level


@pytest.mark.parametrize(
    ("classification", "title"),
    [("TS", "Tropical Storm Rachel"), ("HU", "Hurricane Rachel"), ("TD", "Tropical Depression Rachel"), ("PTC", "Post-Tropical Cyclone Rachel"),
     ("XX", "Rachel"), (None, "Rachel")],
)
def test_the_title_names_the_classification_when_it_is_known(classification: Optional[str], title: str) -> None:
    payload = S.current_storms()
    payload["activeStorms"][1]["classification"] = classification
    assert hazard_of(parse_current_storms(payload).storms[1]).title == title


def test_the_same_storm_has_the_same_key_whoever_names_it() -> None:
    assert cyclone_key("Isaias", 2026) == cyclone_key("ISAIAS", 2026) == cyclone_key("Isaias ", 2026)
    assert cyclone_key("Isaias", 2026) != cyclone_key("Isaias", 2020)
    assert cyclone_key("Ana-Maria", 2026) == "cyclone:ana-maria:2026"


# ------------------------------------------------------------------------------------------------ the forecast bundle


def isaias_forecast() -> Any:
    return parse_forecast_bundle(S.nhc_fixture("al092026_5day_013A.zip"), ISSUED, "013a")


def test_the_advisory_13a_gives_its_cone_track_positions_and_warnings() -> None:
    forecast = isaias_forecast()
    assert (forecast.advisory, forecast.issued_utc) == ("13A", ISSUED)
    assert (len(forecast.cones), len(forecast.tracks), len(forecast.points), len(forecast.watches_warnings)) == (1, 1, 5, 3)
    assert [p.tau_h for p in forecast.points] == [0, 12, 24, 36, 48]
    assert [(p.wind_kt, p.gust_kt, p.category) for p in forecast.points[:2]] == [(95.0, 130.0, 2), (75.0, 95.0, 1)]
    assert [p.stage for p in forecast.points[:3]] == ["Hurricane", "Hurricane", "Subtropical Storm"]


def test_the_nhc_9999_is_not_a_pressure() -> None:
    """The bundle writes 9999 where it has no value: only the first position has a pressure, the others have none."""
    assert [p.pressure_hpa for p in isaias_forecast().points] == [967.0, None, None, None, None]


def test_the_valid_times_are_read_in_the_right_month() -> None:
    """``VALIDTIME`` is ``dd/hhmm``: the first position is the 21 UTC advisory of the 9th, the second the 6 UTC of the 10th."""
    points = isaias_forecast().points
    assert points[0].valid_utc == 1791579600  # 2026-10-09 21:00 UTC
    assert points[1].valid_utc == 1791612000  # 2026-10-10 06:00 UTC
    assert [b.valid_utc - a.valid_utc for a, b in zip(points, points[1:], strict=False)] == [32_400, 43_200, 43_200, 43_200]


def test_the_cone_is_on_the_map_where_the_storm_goes() -> None:
    """The cone of Isaias's advisory 13A: from the Florida Panhandle (30.1 N, 86.6 W) north across Alabama and Tennessee."""
    forecast = isaias_forecast()
    cone = shape(forecast.cones[0])
    assert forecast.cones[0]["type"] == "Polygon" and cone.is_valid
    west, south, east, north = cone.bounds
    assert (west, south, east, north) == pytest.approx((-87.986, 29.831, -81.195, 39.134))
    assert cone.contains(Point(-86.80, 33.52))  # Birmingham, Alabama, under the forecast track at 24 hours
    assert cone.contains(Point(-86.78, 36.16))  # Nashville, Tennessee
    assert not cone.contains(Point(-84.39, 33.75))  # Atlanta is east of the cone
    assert not cone.contains(Point(-80.19, 25.76))  # Miami is far south-east of it
    # Every forecast position is on the cone (the track is its spine), and the track passes through the positions in order.
    for point in forecast.points:
        assert cone.buffer(0.001).contains(Point(*point.geometry["coordinates"]))
    track = forecast.tracks[0]
    assert track["type"] == "LineString"
    assert track["coordinates"] == [point.geometry["coordinates"] for point in forecast.points]
    assert track["coordinates"][0] == [-86.6, 30.1] and track["coordinates"][-1] == [-82.5, 38.1]


def test_the_warnings_are_the_coast_lines_with_their_nhc_code() -> None:
    warnings = isaias_forecast().watches_warnings
    assert sorted((w.code, w.label) for w in warnings) == [
        ("HWR", "Hurricane Warning"),
        ("TWR", "Tropical Storm Warning"),
        ("TWR", "Tropical Storm Warning"),
    ]
    hurricane = next(w for w in warnings if w.code == "HWR")
    assert hurricane.geometry["type"] == "LineString"
    assert hurricane.geometry["coordinates"][0] == [-88.82, 30.4] and hurricane.geometry["coordinates"][-1] == [-85.39, 29.93]  # Mississippi to Apalachicola


def test_a_cone_of_a_few_tens_of_kilobytes_not_hundreds() -> None:
    """The bundle is 29,533 bytes; its cone, at three decimals, is about 21 kilobytes of GeoJSON (1,146 positions)."""
    forecast = isaias_forecast()
    size = len(json.dumps(forecast.cones[0], separators=(",", ":")))
    assert size < 30_000
    assert all(len(str(c)) < 20 for ring in forecast.cones[0]["coordinates"] for c in ring[:5])


def test_a_bundle_that_is_not_a_zip_or_holds_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="ZIP"):
        parse_forecast_bundle(b"<html>maintenance</html>")
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("readme.txt", "nothing here")
    with pytest.raises(ValueError, match="none of the cone"):
        parse_forecast_bundle(empty.getvalue())


def test_a_bundle_that_is_larger_than_any_forecast_ever_was_is_refused() -> None:
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("al092026-013A_5day_pgn.shp", bytes(9 * 1024 * 1024))  # 9 MB of zeros, a few kilobytes in the archive
    assert len(bomb.getvalue()) < 100_000
    with pytest.raises(ValueError, match="larger"):
        parse_forecast_bundle(bomb.getvalue())
    crowd = io.BytesIO()
    with zipfile.ZipFile(crowd, "w") as archive:
        for number in range(61):
            archive.writestr(f"f{number}.txt", "x")
    with pytest.raises(ValueError, match="larger"):
        parse_forecast_bundle(crowd.getvalue())


def test_a_layer_that_is_not_in_geographic_coordinates_is_refused() -> None:
    source = zipfile.ZipFile(io.BytesIO(S.nhc_fixture("al092026_5day_013A.zip")))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for info in source.infolist():
            data = source.read(info)
            if info.filename.endswith("5day_pgn.prj"):
                data = b'PROJCS["a projection in metres"]'
            archive.writestr(info.filename, data)
    with pytest.raises(ValueError, match="geographic"):
        parse_forecast_bundle(out.getvalue(), ISSUED)


# ------------------------------------------------------------------------------------------------ the collector


class Server:
    """The NHC server as the collector sees it: the status file (with its ETag), the bundles, and a count of what was asked."""

    def __init__(self) -> None:
        self.status = S.isaias_only()
        self.bundle: Optional[bytes] = S.nhc_fixture("al092026_5day_013A.zip")
        self.bundle_status = 200
        self.requests: list[tuple[str, str, Optional[str]]] = []  # (path, kind, conditional header)

    @property
    def etag(self) -> str:
        return f'"{abs(hash(self.status)) % 10**9}"'

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/CurrentStorms.json":
            tag = request.headers.get("If-None-Match")
            self.requests.append((path, "304" if tag == self.etag else "200", tag))
            if tag == self.etag:
                return httpx.Response(304, headers={"ETag": self.etag})
            return httpx.Response(200, content=self.status, headers={"ETag": self.etag, "Last-Modified": "Sat, 10 Oct 2026 00:00:00 GMT"})
        self.requests.append((path, str(self.bundle_status), None))
        if self.bundle_status != 200 or self.bundle is None:
            return httpx.Response(self.bundle_status)
        return httpx.Response(200, content=self.bundle)

    def count(self, suffix: str) -> int:
        return sum(1 for path, _, _ in self.requests if path.endswith(suffix))


def collector_for(server: Callable[[httpx.Request], httpx.Response]) -> NHCCollector:
    return NHCCollector(http_client=httpx.AsyncClient(transport=httpx.MockTransport(server)))


@pytest.mark.asyncio
async def test_the_collector_reads_the_storm_and_its_bundle_and_then_only_asks_if_it_changed(sim_clock: SimClock) -> None:
    server = Server()
    collector = collector_for(server)
    hazards = await collector.fetch()
    assert [(h.id, h.severity) for h in hazards] == [("nhc_al092026", SeverityLevel.ORANGE)]
    storm = collector.storm_for("nhc_al092026")
    assert storm is not None and storm.forecast is not None and storm.forecast.advisory == "13A"
    assert collector.storm_for("nhc_ep182026") is None and collector.storm_for("something") is None
    assert (server.count("CurrentStorms.json"), server.count(".zip")) == (1, 1)

    sim_clock.advance(299)
    await collector.fetch()  # inside the 300 s of the cache: no request at all
    assert len(server.requests) == 2

    sim_clock.advance(2)
    again = await collector.fetch()
    assert [h.id for h in again] == ["nhc_al092026"]
    assert server.requests[-1][1:] == ("304", server.etag)  # the file did not change: a 304 with no body
    assert server.count(".zip") == 1  # ...and the bundle, which never changes, was not asked for again
    assert collector.last_state.storms[0].forecast is not None


@pytest.mark.asyncio
async def test_a_new_advisory_brings_one_new_bundle_and_the_old_one_is_forgotten(sim_clock: SimClock) -> None:
    server = Server()
    collector = collector_for(server)
    await collector.fetch()
    payload = json.loads(server.status)
    payload["activeStorms"][0]["forecastTrack"]["zipFile"] = ISAIAS_ZIP.replace("013A", "014")
    payload["activeStorms"][0]["publicAdvisory"]["advNum"] = "014"
    server.status = json.dumps(payload).encode()
    sim_clock.advance(301)
    await collector.fetch()
    assert server.requests[-2][1] == "200" and server.count(".zip") == 2
    assert list(collector._bundles) == [ISAIAS_ZIP.replace("013A", "014")]


@pytest.mark.asyncio
async def test_a_storm_that_leaves_the_list_takes_its_hazard_with_it(sim_clock: SimClock) -> None:
    server = Server()
    collector = collector_for(server)
    await collector.fetch()
    server.status = S.nhc_fixture("CurrentStorms_empty.json")
    sim_clock.advance(301)
    assert await collector.fetch() == []
    assert collector.last_state.storms == () and collector._bundles == {}


@pytest.mark.asyncio
async def test_a_bundle_that_cannot_be_read_leaves_the_hazard_and_is_asked_for_again_in_ten_minutes(sim_clock: SimClock) -> None:
    server = Server()
    server.bundle_status = 503
    collector = collector_for(server)
    hazards = await collector.fetch()
    assert [h.id for h in hazards] == ["nhc_al092026"]  # the storm is there, only its forecast is not
    assert collector.last_state.storms[0].forecast is None and collector.last_state.bundles_failed == 1
    assert server.count(".zip") == 1

    # The status file has not changed (304), but the missing forecast must not wait for the next advisory, hours away.
    server.bundle_status = 200
    sim_clock.advance(301)
    await collector.fetch()
    assert server.count(".zip") == 1  # asked for again only after ten minutes: not at every refresh
    assert collector.last_state.storms[0].forecast is None
    sim_clock.advance(301)
    await collector.fetch()
    assert server.count(".zip") == 2
    assert collector.last_state.storms[0].forecast is not None and collector.last_state.bundles_failed == 0
    assert server.requests[-2][1] == "304"  # the file itself was not read again


@pytest.mark.asyncio
async def test_a_bundle_that_is_not_a_zip_is_a_missing_forecast_not_a_failure(sim_clock: SimClock) -> None:
    server = Server()
    server.bundle = b"<html>maintenance</html>"
    collector = collector_for(server)
    hazards = await collector.fetch()
    assert [h.id for h in hazards] == ["nhc_al092026"]
    assert collector.last_state.storms[0].forecast is None


@pytest.mark.asyncio
async def test_a_status_file_that_is_not_json_is_a_failure_of_the_source(sim_clock: SimClock) -> None:
    server = Server()
    server.status = b"<html>maintenance</html>"
    collector = collector_for(server)
    with pytest.raises(CollectorUnavailableError):
        await collector.fetch()
