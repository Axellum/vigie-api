"""The storms and the tsunamis through the HTTP API: ``/v1/geojson/hazards``, the forecast route and the tsunami vigilances.

The two collectors run for real behind a simulated server (``httpx.MockTransport``) that serves the recordings: the NHC's file of
2026-10-09 with the bundle of Hurricane Isaias's advisory 13A, and tsunami.gov during the Panama earthquake of the same day,
at 18:55 UTC (the third message of the Pacific centre, 1-3 m waves forecast, four arrival times). The rest of the application is
the one of the other tests (``world``: every other provider answers nothing).
"""

from __future__ import annotations

import json
from typing import Any, Iterator
from unittest.mock import AsyncMock

import httpx
import pytest
from shapely.geometry import shape

from app.collectors import tsunami as T
from app.collectors.base import Fetched
from app.collectors.disasters import NOAANWSCollector
from app.collectors.nhc import NHCCollector
from app.collectors.tsunami import TsunamiCollector
from app.main import aggregator
from tests import storm_samples as S
from tests import vigilance_samples as national
from tests.conftest import World
from tests.helpers import SimClock
from tests.test_nhc import Server as NhcServer
from tests.test_storm_dedup import nws_tsunami_warning
from tests.test_tsunami import TsunamiServer, haida_gwaii

FORECAST_BUDGET_BYTES = 200 * 1024  # the most a forecast may weigh on the wire (the site's limit)


@pytest.fixture()
def storms(world: World, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    """Isaias is active and the Pacific centre is on its third message about Panama (2026-10-09 18:55 UTC)."""
    sim_clock.EPOCH = S.at("2026-10-09", "18:55")
    nhc_server, tsunami_server = NhcServer(), TsunamiServer(sim_clock)
    nhc = NHCCollector(http_client=httpx.AsyncClient(transport=httpx.MockTransport(nhc_server)))
    tsunami = TsunamiCollector(http_client=httpx.AsyncClient(transport=httpx.MockTransport(tsunami_server)))
    monkeypatch.setattr(aggregator, "nhc", nhc)
    monkeypatch.setattr(aggregator, "tsunami", tsunami)
    monkeypatch.setattr(aggregator.vigilances, "tsunami", tsunami)
    world.nhc_server = nhc_server  # type: ignore[attr-defined]
    world.tsunami_server = tsunami_server  # type: ignore[attr-defined]
    yield world


def hazard_features(world: World) -> dict[str, dict[str, Any]]:
    body = world.client.get("/v1/geojson/hazards").json()
    return {feature["id"]: feature for feature in body["features"]}


def kinds(forecast: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for feature in forecast["features"]:
        counts[feature["properties"]["kind"]] = counts.get(feature["properties"]["kind"], 0) + 1
    return counts


# ------------------------------------------------------------------------------------------------ the hazards


def test_a_storm_and_a_tsunami_are_points_with_the_usual_fields_and_say_they_have_a_forecast(storms: World) -> None:
    features = hazard_features(storms)
    assert set(features) == {"nhc_al092026", "tsunami_pheb_26282000"}
    storm = features["nhc_al092026"]
    assert storm["geometry"] == {"type": "Point", "coordinates": [-86.6, 30.1]}
    assert storm["properties"]["category"] == "HURRICANE" and storm["properties"]["severity"] == "ORANGE"
    assert storm["properties"]["title"] == "Hurricane Isaias" and storm["properties"]["timestamp_utc"] == 1791590400
    assert (storm["properties"]["value_primary"], storm["properties"]["value_unit"]) == (pytest.approx(175.9), "km/h")
    assert storm["properties"]["has_forecast"] is True and "National Hurricane Center" in storm["properties"]["attribution"]
    tsunami = features["tsunami_pheb_26282000"]
    assert tsunami["geometry"] == {"type": "Point", "coordinates": [-80.75, 7.54]}
    assert tsunami["properties"]["category"] == "TSUNAMI" and tsunami["properties"]["severity"] == "ORANGE"
    assert tsunami["properties"]["title"] == "Tsunami 1-3 meters - PANAMA" and tsunami["properties"]["source"] == "PTWC"
    assert tsunami["properties"]["has_forecast"] is True and "Tsunami Warning Centers" in tsunami["properties"]["attribution"]
    assert all(feature["geometry"]["type"] == "Point" for feature in features.values())  # the site keeps only points


def test_the_other_hazards_say_they_have_no_forecast(storms: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests import samples

    monkeypatch.setattr(aggregator.usgs, "fetch", AsyncMock(return_value=[samples.earthquake()]))
    features = hazard_features(storms)
    assert features["usgs_us7000test"]["properties"]["has_forecast"] is False
    assert features["nhc_al092026"]["properties"]["has_forecast"] is True


def test_a_storm_whose_bundle_could_not_be_read_is_a_hazard_that_says_it_has_no_forecast(storms: World) -> None:
    storms.nhc_server.bundle_status = 503  # type: ignore[attr-defined]
    features = hazard_features(storms)
    assert features["nhc_al092026"]["properties"]["has_forecast"] is False
    forecast = storms.client.get("/v1/geojson/hazards/nhc_al092026/forecast")
    assert forecast.status_code == 200
    body = forecast.json()
    assert body["features"] == [] and body["hazard_id"] == "nhc_al092026" and "could not be read" in body["reason"]


def test_the_frame_of_the_displays_carries_them_in_its_unchanged_format(storms: World) -> None:
    """The HAZ line keeps its 12 fields (docs/embedded-frame.md): the new hazards are lines like the others, and the CRC holds."""
    from app.services.embedded_frame import verify_embedded_frame_crc

    frame = storms.client.get("/v1/embedded/frame").text
    assert verify_embedded_frame_crc(frame)
    lines = {line.split("|")[1]: line.split("|") for line in frame.splitlines() if line.startswith("HAZ|")}
    assert set(lines) == {"nhc_al092026", "tsunami_pheb_26282000"}
    assert all(len(fields) == 12 and len(fields[1]) <= 28 for fields in lines.values())
    # HAZ|id|category|severity|lat|lon|value|depth_km|tsunami_0_1|timestamp_utc|sources|title
    assert lines["nhc_al092026"] == ["HAZ", "nhc_al092026", "HURRICANE", "ORANGE", "30.1000", "-86.6000", "175.9", "", "0", "1791590400", "NHC", "Hurricane Isaias"]
    assert lines["tsunami_pheb_26282000"][2:4] == ["TSUNAMI", "ORANGE"]
    assert lines["tsunami_pheb_26282000"][6:9] == ["", "", "1"]  # no value, no depth, the tsunami flag
    assert lines["tsunami_pheb_26282000"][10:] == ["PTWC", "Tsunami 1-3 meters - PANAMA"]


# ------------------------------------------------------------------------------------------------ the forecast of a storm


def test_the_forecast_of_a_storm_is_a_feature_collection_with_cone_track_positions_and_warnings(storms: World) -> None:
    response = storms.client.get("/v1/geojson/hazards/nhc_al092026/forecast")
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "FeatureCollection" and body["hazard_id"] == "nhc_al092026" and body["title"] == "Hurricane Isaias"
    assert (body["kind"], body["source"], body["advisory"], body["issued_utc"]) == ("tropical_cyclone", "NHC", "13A", 1791590400)
    assert body["advisory_url"] == "https://www.nhc.noaa.gov/text/MIATCPAT4.shtml"
    assert "National Hurricane Center" in body["attribution"] and body["reason"] is None
    assert kinds(body) == {"cone": 1, "track": 1, "position": 5, "watch_warning": 3}
    assert body["bbox"] == pytest.approx([-89.98, 29.46, -81.195, 39.134])

    by_kind = {feature["properties"]["kind"]: feature for feature in body["features"]}
    assert by_kind["cone"]["geometry"]["type"] == "Polygon" and shape(by_kind["cone"]["geometry"]).is_valid
    assert by_kind["track"]["geometry"]["type"] == "LineString"
    positions = [f for f in body["features"] if f["properties"]["kind"] == "position"]
    assert [p["properties"]["tau_h"] for p in positions] == [0, 12, 24, 36, 48]
    first = positions[0]
    assert first["geometry"] == {"type": "Point", "coordinates": [-86.6, 30.1]}
    assert (first["properties"]["valid_utc"], first["properties"]["wind_kt"], first["properties"]["pressure_hpa"]) == (1791579600, 95.0, 967.0)
    assert first["properties"]["wind_kmh"] == pytest.approx(175.9) and first["properties"]["gust_kt"] == 130.0
    assert positions[1]["properties"]["pressure_hpa"] is None  # the NHC wrote no pressure there: not made up
    assert all(feature["id"].startswith("nhc_al092026:") for feature in body["features"])
    assert {f["properties"]["code"] for f in body["features"] if f["properties"]["kind"] == "watch_warning"} == {"HWR", "TWR"}


def test_the_cone_is_on_the_map_in_the_api_answer_too(storms: World) -> None:
    body = storms.client.get("/v1/geojson/hazards/nhc_al092026/forecast").json()
    cone = shape(next(f["geometry"] for f in body["features"] if f["properties"]["kind"] == "cone"))
    from shapely.geometry import Point

    hazards = hazard_features(storms)["nhc_al092026"]["geometry"]["coordinates"]
    assert cone.buffer(0.001).contains(Point(*hazards))  # the storm's current position is the tip of its own cone
    assert cone.contains(Point(-86.80, 33.52))  # Birmingham, Alabama, where the track goes next day
    assert not cone.contains(Point(-80.19, 25.76))  # Miami is not in it


def test_the_forecast_of_a_storm_weighs_far_less_than_the_limit(storms: World) -> None:
    response = storms.client.get("/v1/geojson/hazards/nhc_al092026/forecast")
    size = len(response.content)
    print(f"\nthe forecast of Hurricane Isaias, advisory 13A: {size} bytes of JSON ({len(response.json()['features'])} features)")
    assert size < FORECAST_BUDGET_BYTES / 4
    assert max(len(str(number).split(".")[-1]) for feature in response.json()["features"] for number in _numbers(feature["geometry"])) <= 3


def _numbers(geometry: dict[str, Any]) -> list[float]:
    found: list[float] = []

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, float):
            found.append(value)

    walk(geometry["coordinates"])
    return found


# ------------------------------------------------------------------------------------------------ the forecast of a tsunami


def test_the_forecast_of_a_tsunami_is_its_epicentre_and_the_arrival_times_of_its_places(storms: World) -> None:
    response = storms.client.get("/v1/geojson/hazards/tsunami_pheb_26282000/forecast")
    assert response.status_code == 200
    body = response.json()
    assert (body["kind"], body["source"], body["advisory"]) == ("tsunami", "PTWC", "PHEB-3-26282000")
    assert body["issued_utc"] == int(S.at("2026-10-09", "18:51:40")) and "Tsunami Warning Centers" in body["attribution"]  # the message of the third bulletin
    assert body["advisory_url"] == "http://www.tsunami.gov/events/PHEB/2026/10/09/26282000/3/WEPA40/WEPA40.txt"
    assert kinds(body) == {"epicentre": 1, "eta": 4}
    epicentre = next(f for f in body["features"] if f["properties"]["kind"] == "epicentre")
    assert epicentre["geometry"] == {"type": "Point", "coordinates": [-80.75, 7.54]}
    assert (epicentre["properties"]["magnitude"], epicentre["properties"]["magnitude_type"], epicentre["properties"]["depth_km"]) == (7.6, "Mwp", 33.0)
    assert epicentre["properties"]["origin_utc"] == int(S.PANAMA_QUAKE_UTC)
    assert sorted(a["level_original"] for a in epicentre["properties"]["alerts"]) == ["Tsunami 1-3 meters"]
    etas = [f for f in body["features"] if f["properties"]["kind"] == "eta"]
    assert [(e["properties"]["place"], e["properties"]["eta_utc"]) for e in etas] == [
        ("PUNTA MALA", int(S.at("2026-10-09", "18:20"))),
        ("PUERTO PINA", int(S.at("2026-10-09", "18:31"))),
        ("PUNTA BURICA", int(S.at("2026-10-09", "18:40"))),
        ("BALBOA HEIGHTS", int(S.at("2026-10-09", "20:41"))),
    ]
    assert etas[0]["geometry"] == {"type": "Point", "coordinates": [-80.0, 7.5]} and etas[0]["properties"]["region"] == "PANAMA"
    assert all(e["properties"]["read_from"] == "text" for e in etas)


# ------------------------------------------------------------------------------------------------ no forecast, no hazard


def test_an_unknown_hazard_is_a_404(storms: World) -> None:
    response = storms.client.get("/v1/geojson/hazards/nhc_xx012026/forecast")
    assert response.status_code == 404 and "nhc_xx012026" in response.json()["detail"]


def test_a_hazard_with_no_forecast_answers_an_empty_collection_and_says_why(storms: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests import samples

    monkeypatch.setattr(aggregator.usgs, "fetch", AsyncMock(return_value=[samples.earthquake()]))
    response = storms.client.get("/v1/geojson/hazards/usgs_us7000test/forecast")
    assert response.status_code == 200
    body = response.json()
    assert body["features"] == [] and body["kind"] is None and body["bbox"] is None
    assert "no forecast" in body["reason"] and "National Hurricane Center" in body["reason"]
    assert body["hazard_id"] == "usgs_us7000test" and body["generated_utc"] > 0


def test_a_storm_that_has_left_the_list_is_a_404_not_an_old_cone(storms: World, sim_clock: SimClock) -> None:
    assert storms.client.get("/v1/geojson/hazards/nhc_al092026/forecast").status_code == 200
    storms.nhc_server.status = S.nhc_fixture("CurrentStorms_empty.json")  # type: ignore[attr-defined]
    sim_clock.advance(400)
    assert storms.client.get("/v1/geojson/hazards/nhc_al092026/forecast").status_code == 404
    assert "nhc_al092026" not in hazard_features(storms)


def test_the_route_is_in_the_openapi_with_its_404(storms: World) -> None:
    from app.main import app

    operation = app.openapi()["paths"]["/v1/geojson/hazards/{hazard_id}/forecast"]["get"]
    assert operation["tags"] == ["Standards"] and "404" in operation["responses"]
    assert "cone" in operation["description"] and "eta" in operation["description"]


# ------------------------------------------------------------------------------------------------ the tsunami vigilances


def test_the_tsunami_messages_are_listed_by_source_with_their_texts_untouched(storms: World) -> None:
    response = storms.client.get("/v1/vigilances", params={"source": "tsunami"})
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == {"lat": None, "lon": None, "country": None, "source": "tsunami", "river_radius_km": None}
    assert [(v["source"], v["level"], v["level_original"], v["country"]) for v in body["vigilances"]] == [("tsunami", "ORANGE", "Tsunami 1-3 meters", None)]
    warning = body["vigilances"][0]
    assert warning["id"].startswith("tsunami:PHEB-3-26282000:") and warning["status"] == "update"
    assert warning["area"]["localization"] == "none" and warning["area"]["has_polygon"] is False
    assert warning["texts"][0]["description"].startswith("PTWC TSUNAMI THREAT MESSAGE")
    assert warning["link"].endswith("/26282000/3/WEPA40/PHEBCAP.xml")
    [coverage] = body["coverage"]
    assert (coverage["source"], coverage["status"], coverage["warnings_in_feed"]) == ("tsunami", "ok", 1)
    assert "information statement is not an alert" in coverage["note"]
    assert "U.S. Tsunami Warning Centers" in body["attribution"] and "Not endorsed" in body["attribution"]


def test_the_tsunami_messages_as_geojson_have_no_geometry_because_no_shape_is_given(storms: World) -> None:
    body = storms.client.get("/v1/vigilances.geojson", params={"source": "tsunami"}).json()
    assert body["type"] == "FeatureCollection" and len(body["features"]) == 1
    assert body["features"][0]["geometry"] is None and body["features"][0]["properties"]["level_original"] == "Tsunami 1-3 meters"
    assert body["metadata"]["query"]["source"] == "tsunami"


def test_the_source_is_asked_alone_and_only_for_tsunami(storms: World) -> None:
    assert storms.client.get("/v1/vigilances", params={"source": "tsunami", "country": "US"}).status_code == 422
    assert storms.client.get("/v1/vigilances", params={"source": "tsunami", "lat": 1, "lon": 2}).status_code == 422
    assert storms.client.get("/v1/vigilances", params={"source": "meteoalarm"}).status_code == 422
    assert storms.client.get("/v1/vigilances", params={}).status_code == 422


def test_when_the_centres_say_nothing_the_answer_says_so_and_is_empty(storms: World, sim_clock: SimClock) -> None:
    sim_clock.advance(3 * 3600 + 2 * 3600)  # the list of the day, long after the last message (the final message of 19:40)
    body = storms.client.get("/v1/vigilances", params={"source": "tsunami"}).json()
    assert body["vigilances"] == [] and body["coverage"][0]["warnings_in_feed"] == 0 and body["coverage"][0]["status"] == "ok"


def test_the_centre_being_down_is_said_not_read_as_calm(storms: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.collectors.base import CollectorUnavailableError

    monkeypatch.setattr(aggregator.tsunami, "fetch_ex", AsyncMock(side_effect=CollectorUnavailableError("down")))
    body = storms.client.get("/v1/vigilances", params={"source": "tsunami"}).json()
    assert body["vigilances"] == [] and body["coverage"][0]["status"] == "unavailable"
    assert body["attribution"] is None  # a source that said nothing is not credited


# ------------------------------------------------------------------------------------------------ the United States and Canada


@pytest.fixture()
def haida(world: World, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> World:
    """Haida Gwaii, 2012-10-28 05:50 UTC: the NTWC's second message (a warning and two advisories) and the NWS alert that relays the first."""
    sim_clock.EPOCH = S.at("2012-10-28", "05:50")
    events, _ = T.events_of([haida_gwaii(6)], sim_clock.EPOCH)
    monkeypatch.setattr(aggregator.tsunami, "fetch_vigilances", AsyncMock(return_value=Fetched(T.vigilances_of(events[0]))))
    tornado = {
        "id": "urn:oid:2.49.0.1.840.0.2012.1028.0540.tornado",
        "geometry": None,
        "properties": {
            "id": "urn:oid:2.49.0.1.840.0.2012.1028.0540.tornado", "event": "Tornado Warning", "severity": "Extreme", "status": "Actual",
            "messageType": "Alert", "sent": "2012-10-28T05:40:00+00:00", "expires": "2012-10-28T06:40:00+00:00", "areaDesc": "Somewhere County",
            "geocode": {"UGC": ["OKC001"], "SAME": []}, "parameters": {},
        },
    }
    relay = nws_tsunami_warning()
    relay["properties"]["expires"] = "2012-10-28T07:44:00+00:00"
    nws = NOAANWSCollector.parse_vigilances({"features": [relay, tornado]})
    assert len(nws) == 2
    monkeypatch.setattr(aggregator.noaa_nws, "fetch_vigilances", AsyncMock(return_value=Fetched(nws)))
    monkeypatch.setattr(aggregator.eccc, "fetch_alerts", AsyncMock(return_value=Fetched(national.eccc_alerts())))
    return world


def test_the_us_zones_of_a_tsunami_message_are_listed_with_the_us_and_the_nws_relay_only_once(haida: World) -> None:
    body = haida.client.get("/v1/vigilances", params={"country": "US"}).json()
    by_source = {}
    for warning in body["vigilances"]:
        by_source.setdefault(warning["source"], []).append((warning["event"], warning["level_original"]))
    assert sorted(by_source["tsunami"]) == [("Tsunami", "Advisory"), ("Tsunami", "Warning")]  # the centre's own words: the event is "Tsunami" in its CAP, the category says which
    assert by_source["noaa_nws"] == [("Tornado Warning", "Extreme")]  # the tsunami warning of the NWS is the same message: not twice
    sources = {item["source"]: item for item in body["coverage"]}
    assert sources["tsunami"]["status"] == "ok" and "1 NWS alert(s)" in sources["tsunami"]["note"]
    assert "Tsunami Warning Centers" in body["attribution"] and "National Weather Service" in body["attribution"]


def test_the_canadian_zones_are_listed_with_canada(haida: World) -> None:
    body = haida.client.get("/v1/vigilances", params={"country": "CA"}).json()
    tsunami = [w for w in body["vigilances"] if w["source"] == "tsunami"]
    assert sorted((w["country"], w["level_original"]) for w in tsunami) == [("CA", "Advisory"), ("CA", "Warning")]
    assert all(g["value"].startswith("BCZ") for w in tsunami for g in w["area"]["geocodes"] if g["scheme"] == "UGC")


def test_the_whole_message_is_listed_by_source_and_a_country_without_one_has_no_tsunami_source(haida: World) -> None:
    all_of_them = haida.client.get("/v1/vigilances", params={"source": "tsunami"}).json()["vigilances"]
    assert len(all_of_them) == 4 and {w["country"] for w in all_of_them} == {"US", "CA"}
    germany = haida.client.get("/v1/vigilances", params={"country": "DE"}).json()
    assert "tsunami" not in {item["source"] for item in germany["coverage"]}


def test_a_point_in_the_us_does_not_get_a_tsunami_it_cannot_be_matched_to(haida: World) -> None:
    """The messages name zones by code, and no shape of a UGC zone is on this instance: they are listed by country, never guessed for a point."""
    body = haida.client.get("/v1/vigilances", params={"lat": 47.6, "lon": -122.3}).json()  # Seattle
    assert [w for w in body["vigilances"] if w["source"] == "tsunami"] == []
    assert any(item["source"] == "tsunami" and item["warnings_not_matchable"] for item in body["coverage"])
    json.dumps(body)
