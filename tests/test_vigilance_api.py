"""``GET /v1/vigilances``, ``GET /v1/vigilances.geojson`` and the follow commands of ``/v1/stream``.

The application runs with the recorded feeds of 2026-10-20 in place of the providers (the clock is set to the moment
they were recorded, so that the warnings are in force). Nothing reaches the network.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.attribution import METEOALARM_DISCLAIMER, credit_text
from app.collectors.base import Fetched
from app.collectors.meteoalarm import COUNTRY_FEEDS
from app.main import MAX_FOLLOWED_POINTS_PER_CONNECTION, app
from app.services.vigilance import SUPPORTED_COUNTRIES
from tests import vigilance_samples as samples
from tests.conftest import World
from tests.helpers import SimClock


@pytest.fixture()
def recorded(world: World, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> World:
    """The application with the recorded MeteoAlarm feeds (and CAP documents) in place of the providers."""
    sim_clock.advance(samples.RECORDED_AT - SimClock.EPOCH)

    async def feed(country: str, **_: Any) -> Fetched[Any]:
        if country in samples.FEED_FILES:
            return Fetched(samples.feed(country))
        return Fetched(samples.feed("DK").__class__(country=country, updated_utc=None, vigilances=()))

    async def document(url: str) -> Fetched[Any]:
        name = {
            "b77d3bc0-76a2-4886-8da8-bac8d99f57fc": "cap_france_b77d3bc0.xml",
            "9c454e4a-5829-413f-a5d2-b0d50bc456e7": "cap_iceland_9c454e4a.xml",
        }.get(url.rsplit("/", 1)[-1])
        if name is None:
            from app.collectors.meteoalarm import CapDocument

            return Fetched(CapDocument(available=False))
        return Fetched(samples.cap(name))

    monkeypatch.setattr(world.aggregator.meteoalarm, "fetch_country", AsyncMock(side_effect=feed))
    monkeypatch.setattr(world.aggregator.meteoalarm_cap, "fetch_document", AsyncMock(side_effect=document))
    return world


def test_a_point_gets_its_warnings(recorded: World) -> None:
    lat, lon = samples.VALENCE
    response = recorded.client.get("/v1/vigilances", params={"lat": lat, "lon": lon})
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == {"lat": lat, "lon": lon, "country": None} and body["countries"] == ["FR"]
    [warning] = body["vigilances"]
    assert warning["id"] == "meteoalarm:b77d3bc0-76a2-4886-8da8-bac8d99f57fc:6-0-1"
    assert (warning["level"], warning["level_original"]) == ("YELLOW", "Yellow")
    assert (warning["phenomenon"], warning["phenomenon_original"]) == ("flood", "Flooding")
    assert (warning["status"], warning["message_type"]) == ("new", "Alert")
    assert warning["derived"] == ["status", "level", "phenomenon"]
    assert warning["area"] == {
        "description": "Drôme",
        "geocodes": [{"scheme": "EMMA_ID", "value": "FR031"}, {"scheme": "NUTS3", "value": "FR713"}],
        "has_polygon": False,
        "localization": "geocode",
    }
    assert warning["issuer"] == "METEO-FRANCE" and warning["texts"][0]["headline"] == "Vigilance jaune inondations"
    assert warning["link"].startswith("https://feeds.meteoalarm.org/api/v1/warnings/")
    assert warning["attribution"].startswith(credit_text("meteoalarm"))
    assert body["attribution"] == credit_text("meteoalarm") and body["disclaimer"] == METEOALARM_DISCLAIMER
    assert body["coverage"][0]["status"] == "ok" and body["note"] is None


def test_the_times_are_utc_epoch_seconds_and_unknown_is_null(recorded: World) -> None:
    lat, lon = samples.VALENCE
    warning = recorded.client.get("/v1/vigilances", params={"lat": lat, "lon": lon}).json()["vigilances"][0]
    assert (warning["sent_utc"], warning["onset_utc"], warning["expires_utc"]) == (1792489329, 1792533600, 1792620000)
    assert warning["ends_utc"] is None  # MeteoAlarm does not give one: it is not invented


def test_a_country_lists_every_warning(recorded: World) -> None:
    body = recorded.client.get("/v1/vigilances", params={"country": "it"}).json()
    assert body["query"]["country"] == "IT" and len(body["vigilances"]) == 3
    assert {w["area"]["localization"] for w in body["vigilances"]} == {"none"}
    assert body["coverage"][0]["warnings_not_matchable"] == 3
    assert body["vigilances"][0]["texts"] == []  # the recorded Italian documents do not exist any more


def test_the_texts_can_be_limited_to_some_languages(recorded: World) -> None:
    lat, lon = samples.VALENCE
    body = recorded.client.get("/v1/vigilances", params={"lat": lat, "lon": lon, "lang": "fr"}).json()
    assert [t["language"] for t in body["vigilances"][0]["texts"]] == ["fr-FR"]
    both = recorded.client.get("/v1/vigilances", params={"lat": lat, "lon": lon, "lang": "en,fr"}).json()
    assert sorted(t["language"] for t in both["vigilances"][0]["texts"]) == ["en-GB", "fr-FR"]


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"lat": 48.0},
        {"lon": 2.0},
        {"lat": 48.0, "lon": 2.0, "country": "FR"},
        {"lat": 91.0, "lon": 2.0},
        {"lat": 48.0, "lon": 181.0},
        {"lat": "north", "lon": 2.0},
        {"country": "FRA"},
        {"country": "F1"},
    ],
)
def test_a_bad_question_is_refused_with_422(recorded: World, params: dict[str, Any]) -> None:
    response = recorded.client.get("/v1/vigilances", params=params)
    assert response.status_code == 422, response.text
    assert recorded.client.get("/v1/vigilances.geojson", params=params).status_code == 422


def test_a_country_without_a_source_is_a_404_that_lists_the_countries(recorded: World) -> None:
    response = recorded.client.get("/v1/vigilances", params={"country": "JP"})
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "JP" in detail and "FR" in detail and "US" in detail


def test_a_point_nobody_covers_is_a_200_with_a_note(recorded: World) -> None:
    body = recorded.client.get("/v1/vigilances", params={"lat": 35.6762, "lon": 139.6503}).json()
    assert body["vigilances"] == [] and body["countries"] == [] and body["coverage"] == []
    assert "No official warning source covers this point" in body["note"]
    assert body["attribution"] is None and body["disclaimer"] is None


def test_the_geojson_is_a_feature_collection_with_the_shape_of_the_area(recorded: World) -> None:
    lat, lon = samples.VALENCE
    response = recorded.client.get("/v1/vigilances.geojson", params={"lat": lat, "lon": lon})
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "FeatureCollection" and len(body["features"]) == 1
    feature = body["features"][0]
    assert feature["type"] == "Feature" and feature["geometry"]["type"] in ("Polygon", "MultiPolygon")
    assert feature["properties"]["geometry_source"] == "geocode:NUTS3:FR713"
    assert feature["properties"]["attribution"].startswith(credit_text("meteoalarm"))
    assert body["metadata"]["disclaimer"] == METEOALARM_DISCLAIMER and body["metadata"]["countries"] == ["FR"]
    # The first ring of the shape is closed and around the point.
    ring = feature["geometry"]["coordinates"][0][0] if feature["geometry"]["type"] == "MultiPolygon" else feature["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    assert min(xs) < lon < max(xs) and min(ys) < lat < max(ys)


def test_the_geojson_of_a_country_keeps_the_warnings_it_cannot_draw(recorded: World) -> None:
    body = recorded.client.get("/v1/vigilances.geojson", params={"country": "IT"}).json()
    assert len(body["features"]) == 3 and all(f["geometry"] is None for f in body["features"])


def test_the_openapi_documents_both_routes(recorded: World) -> None:
    schema = app.openapi()
    for path in ("/v1/vigilances", "/v1/vigilances.geojson"):
        operation = schema["paths"][path]["get"]
        assert operation["tags"] == ["Vigilances"]
        assert "official warning service" in operation["description"] or "not an official" in operation["description"]
        assert {p["name"] for p in operation["parameters"]} == {"lat", "lon", "country", "lang"}
        assert all(p.get("description") for p in operation["parameters"])
        assert {"404", "422"} <= set(operation["responses"])
        assert "/v1/stream" in operation["description"] and "vigilance_push" in operation["description"]
    for model in ("Vigilance", "VigilancesResponse", "VigilanceCoverage"):
        assert model in schema["components"]["schemas"]
    assert schema["components"]["schemas"]["Vigilance"]["properties"]["level"]["description"].startswith("DERIVED")


def test_the_countries_with_a_source_are_the_39_feeds_and_the_united_states() -> None:
    assert SUPPORTED_COUNTRIES == sorted({*COUNTRY_FEEDS, "US"}) and len(SUPPORTED_COUNTRIES) == 40


def test_the_embedded_frame_and_the_hazards_are_not_changed_by_the_warnings(recorded: World) -> None:
    """The warnings have their own route: the snapshot a display polls does not carry them."""
    lat, lon = samples.VALENCE
    snapshot = recorded.client.get("/v1/snapshot").json()
    assert "vigilances" not in snapshot
    frame = recorded.client.get("/v1/embedded/frame", params={"lat": lat, "lon": lon, "device_type": "waveshare-7b"})
    assert frame.status_code == 200 and "vigilance" not in frame.text.lower()


# ---------------------------------------------------------------- following a point on the stream


def _drain_initial(ws: Any) -> None:
    """The stream sends its state first (a snapshot, then the embedded frame)."""
    first = ws.receive_json()
    assert first["event"] == "initial_snapshot"


def test_a_client_follows_a_point_and_gets_its_warnings_at_once(recorded: World) -> None:
    lat, lon = samples.VALENCE
    with recorded.client.websocket_connect("/v1/stream") as ws:
        _drain_initial(ws)
        ws.send_json({"action": "follow", "lat": lat, "lon": lon})
        message = ws.receive_json()
        while message["event"] not in ("vigilance_state", "error"):
            message = ws.receive_json()
        assert message["event"] == "vigilance_state"
        assert message["point"] == {"lat": lat, "lon": lon}
        assert [w["area"]["description"] for w in message["vigilances"]] == ["Drôme"]
        assert message["disclaimer"] == METEOALARM_DISCLAIMER
        from app.main import ws_hub

        assert ws_hub.followed_points() == {(lat, lon)}
        ws.send_json({"action": "unfollow", "lat": lat, "lon": lon})
        message = ws.receive_json()
        while message["event"] != "vigilance_unfollowed":
            message = ws.receive_json()
        assert ws_hub.followed_points() == set()


def test_following_a_point_is_forgotten_when_the_connection_closes(recorded: World) -> None:
    from app.main import ws_hub

    with recorded.client.websocket_connect("/v1/stream") as ws:
        _drain_initial(ws)
        ws.send_json({"action": "follow", "lat": 48.0, "lon": 2.0})
        message = ws.receive_json()
        while message["event"] != "vigilance_state":
            message = ws.receive_json()
        assert ws_hub.followed_points() == {(48.0, 2.0)}
    assert ws_hub.followed_points() == set()


@pytest.mark.parametrize(
    "command",
    [
        {"action": "follow", "lat": "48", "lon": 2},
        {"action": "follow", "lat": 48},
        {"action": "follow", "lat": True, "lon": 2},
        {"action": "follow", "lat": 95, "lon": 2},
        {"action": "follow", "lat": 48, "lon": -181},
        {"action": "follow", "lat": float("nan"), "lon": 2},
    ],
)
def test_a_bad_follow_command_gets_an_error_and_follows_nothing(recorded: World, command: dict[str, Any]) -> None:
    from app.main import ws_hub

    with recorded.client.websocket_connect("/v1/stream") as ws:
        _drain_initial(ws)
        ws.send_json(command)
        message = ws.receive_json()
        while message["event"] not in ("error", "vigilance_state"):
            message = ws.receive_json()
        assert message["event"] == "error"
        assert ws_hub.followed_points() == set()


def test_other_text_is_ignored_and_ping_still_answers(recorded: World) -> None:
    with recorded.client.websocket_connect("/v1/stream") as ws:
        _drain_initial(ws)
        ws.send_text("hello")
        ws.send_text('{"action": "dance"}')
        ws.send_text("[1, 2]")
        ws.send_text("ping")
        message = ws.receive_json()
        while message["event"] != "pong":
            message = ws.receive_json()
        assert message["event"] == "pong"


def test_a_connection_follows_ten_points_at_most(recorded: World) -> None:
    from app.main import ws_hub

    with recorded.client.websocket_connect("/v1/stream") as ws:
        _drain_initial(ws)
        for n in range(MAX_FOLLOWED_POINTS_PER_CONNECTION + 1):
            ws.send_json({"action": "follow", "lat": 40.0 + n, "lon": 2.0})
        events = []
        while len([e for e in events if e in ("vigilance_state", "error")]) < MAX_FOLLOWED_POINTS_PER_CONNECTION + 1:
            events.append(ws.receive_json()["event"])
        assert events.count("vigilance_state") == MAX_FOLLOWED_POINTS_PER_CONNECTION and events.count("error") == 1
        assert len(ws_hub.followed_points()) == MAX_FOLLOWED_POINTS_PER_CONNECTION


def test_a_followed_position_is_rounded_so_that_two_clients_share_a_watch(recorded: World) -> None:
    from app.main import ws_hub

    with recorded.client.websocket_connect("/v1/stream") as one, recorded.client.websocket_connect("/v1/stream") as two:
        _drain_initial(one)
        _drain_initial(two)
        one.send_json({"action": "follow", "lat": 44.93341, "lon": 4.89241})
        two.send_json({"action": "follow", "lat": 44.933412, "lon": 4.892398})
        for ws in (one, two):
            message = ws.receive_json()
            while message["event"] != "vigilance_state":
                message = ws.receive_json()
        assert ws_hub.followed_points() == {(44.9334, 4.8924)}
