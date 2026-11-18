"""``GET /v1/forest-fire``: the forest fire danger of the department of a position, through the gateway (ADR 0035).

The levels below are the ones of the real sample Météo-France publishes (22/05/2024: the Aude at 2 then 1, the Ain at 1 and 1); the seasonal 404 of the
portal (out of season, read on 2026-11-18) is the gateway's ``out_of_season``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.collectors.meteofrance_forests import MeteoFranceForestsCollector, parse_gateway_forests
from app.services.forest_fire import department_of, query_forest_fire
from tests.helpers import Provider, SimClock

CARCASSONNE = (43.2130, 2.3491)  # the Aude
BOURG = (46.2052, 5.2255)  # the Ain
AJACCIO = (41.9192, 8.7386)  # Corse-du-Sud
FORT_DE_FRANCE = (14.6161, -61.0588)
MADRID = (40.4168, -3.7038)


def available(department: str, d1: int, d2: int) -> dict[str, Any]:
    return {
        "source_model": "meteofrance-forests",
        "attribution": "Source : Météo-France, Météo des forêts, Licence Ouverte 2.0",
        "status": "available",
        "reference_time": "2024-05-22T14:50:05Z",
        "checked_at": "2026-11-18T12:30:00Z",
        "departments": [{"department": department, "name": "x", "level_d1": d1, "level_d2": d2}],
    }


OUT_OF_SEASON = {
    "source_model": "meteofrance-forests",
    "attribution": "Source : Météo-France, Météo des forêts, Licence Ouverte 2.0",
    "status": "out_of_season",
    "reference_time": None,
    "checked_at": "2026-11-18T12:30:00Z",
    "departments": [],
}


def _gateway(body: Any = None, status: int = 200) -> Provider:
    def answer(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"detail": "x"})
        return httpx.Response(200, json=body if body is not None else available(request.url.params["department"], 2, 1))

    return Provider(answer=answer)


def _collector(provider: Provider) -> MeteoFranceForestsCollector:
    return MeteoFranceForestsCollector("http://gateway.test/", http_client=provider.client())


def test_the_department_of_a_point_is_found_offline() -> None:
    assert department_of(*CARCASSONNE) == "11"
    assert department_of(*BOURG) == "01"
    assert department_of(*AJACCIO) == "2A"
    assert department_of(*FORT_DE_FRANCE) is None  # the Météo des forêts is for metropolitan France
    assert department_of(*MADRID) is None


def test_the_gateway_answer_is_read_and_a_wrong_one_refused() -> None:
    got = parse_gateway_forests(available("11", 2, 1), "11")
    assert (got.status, got.level_d1, got.level_d2, got.reference_time) == ("available", 2, 1, "2024-05-22T14:50:05Z")
    assert parse_gateway_forests(OUT_OF_SEASON, "11").status == "out_of_season"
    for bad in (
        {**available("11", 2, 1), "source_model": "meteofrance-piaf"},
        {**available("11", 9, 1)},  # a level of 9
        available("34", 1, 1),  # the department asked is not in the map
        {**available("11", 2, 1), "status": "maybe"},
        "nope",
    ):
        with pytest.raises(ValueError):
            parse_gateway_forests(bad, "11")


async def test_in_season_the_levels_of_the_department_are_given_with_their_words(sim_clock: SimClock) -> None:
    provider = _gateway()
    out = await query_forest_fire(_collector(provider), lat=CARCASSONNE[0], lon=CARCASSONNE[1])
    assert out.status == "available" and out.department is not None and (out.department.code, out.department.name) == ("11", "Aude")
    assert out.day1 is not None and (out.day1.level, out.day1.label) == (2, "moderate")
    assert out.day2 is not None and (out.day2.level, out.day2.label) == (1, "low")
    assert out.reference_time == "2024-05-22T14:50:05Z" and out.checked_at_utc == 1795005000
    assert provider.requests[0].url.params["department"] == "11"
    assert "Licence Ouverte 2.0" in out.attribution


async def test_out_of_season_is_a_quiet_answer_without_any_level(sim_clock: SimClock) -> None:
    out = await query_forest_fire(_collector(_gateway(OUT_OF_SEASON)), lat=BOURG[0], lon=BOURG[1])
    assert out.status == "out_of_season" and out.day1 is None and out.day2 is None and out.reference_time is None
    assert out.department is not None and out.department.code == "01" and "June to September" in (out.note or "")


async def test_a_point_outside_metropolitan_france_costs_no_request(sim_clock: SimClock) -> None:
    provider = _gateway()
    for lat, lon in (FORT_DE_FRANCE, MADRID):
        out = await query_forest_fire(_collector(provider), lat=lat, lon=lon)
        assert out.status == "outside_coverage" and out.department is None
    assert provider.count == 0


async def test_a_gateway_that_fails_or_answers_something_else_is_unavailable(sim_clock: SimClock) -> None:
    for provider in (_gateway(status=503), _gateway({"source_model": "x"})):
        out = await query_forest_fire(_collector(provider), lat=CARCASSONNE[0], lon=CARCASSONNE[1])
        assert out.status == "unavailable" and out.day1 is None and out.department is not None


async def test_without_a_gateway_it_says_so() -> None:
    out = await query_forest_fire(None, lat=CARCASSONNE[0], lon=CARCASSONNE[1])
    assert out.status == "not_configured" and "VIGIE_METEO_GATEWAY_URL" in (out.note or "")


def test_the_route_says_why_there_is_no_level(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import aggregator, app

    client = TestClient(app)
    monkeypatch.setattr(aggregator, "meteofrance_forests", None)
    assert client.get("/v1/forest-fire", params={"lat": CARCASSONNE[0], "lon": CARCASSONNE[1]}).json()["status"] == "not_configured"
    monkeypatch.setattr(aggregator, "meteofrance_forests", _collector(_gateway(OUT_OF_SEASON)))
    reply = client.get("/v1/forest-fire", params={"lat": CARCASSONNE[0], "lon": CARCASSONNE[1]})
    assert reply.status_code == 200 and reply.headers["cache-control"] == "public, max-age=600"
    assert reply.json()["status"] == "out_of_season" and reply.json()["department"]["code"] == "11"
    assert client.get("/v1/forest-fire", params={"lat": 95, "lon": 0}).status_code == 422
