"""``GET /v1/climate/recent``: the last days a Météo-France station measured, through the gateway (ADR 0034).

The gateway answers below are the real ones of 2026-11-18 (Lille-Lesquin 59343001, days 15 to 17 November, the gust of the 17th flagged doubtful).
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
import pytest

from app.collectors.base import CollectorUnavailableError
from app.collectors.meteofrance_station_daily import MeteoFranceStationDailyCollector, parse_gateway_station_daily
from app.services.climate import load_index
from app.services.climate_recent import query_climate_recent
from tests.helpers import Provider, SimClock

LILLE = "59343001"


def _day(date: str, rain: float, tmin: float, tmax: float, tmean: float, wind: float, gust: Optional[float], doubtful: Optional[list[str]] = None) -> dict[str, Any]:
    return {
        "date": date,
        "rain_mm": rain,
        "tmin_c": tmin,
        "tmax_c": tmax,
        "tmean_c": tmean,
        "wind_mean_ms": wind,
        "gust_ms": gust,
        "doubtful": doubtful or [],
    }


def payload(station: str = LILLE, days: Optional[list[dict[str, Any]]] = None, model: str = "meteofrance-dpclim") -> dict[str, Any]:
    rows = (
        [
            _day("2026-11-15", 5.5, 1.3, 10.9, 7.2, 4.6, 12.5),
            _day("2026-11-16", 3.6, 6.1, 11.5, 9.0, 4.2, 11.7),
            _day("2026-11-17", 22.3, 5.3, 15.5, 11.3, 8.3, None, ["gust_ms"]),
        ]
        if days is None
        else days
    )
    return {
        "source_model": model,
        "attribution": "Source : Météo-France, données climatologiques de base, Licence Ouverte 2.0",
        "station": station,
        "last_day": "2026-11-17",
        "fetched_at": "2026-11-18T12:19:33Z",
        "days": rows,
    }


def _sheet(station_id: str, name: str, lat: float, lon: float, *, until: str = "2026-11-05") -> dict[str, Any]:
    return {
        "id": station_id,
        "name": name,
        "lat": lat,
        "lon": lon,
        "alt": 47,
        "edited": "2026-11-06",
        "period": ["1991", "2020"],
        "blocks": {
            "tmax_mean": {"p": None, "v": [6.0, 7.0, 10.0, 14.0, 17.0, 20.0, 23.0, 23.0, 19.0, 14.0, 9.0, 6.0, 14.0]},
            "tmin_mean": {"p": None, "v": [1.0, 1.0, 3.0, 5.0, 8.0, 11.0, 13.0, 13.0, 10.0, 8.0, 4.0, 2.0, 7.0]},
            "tmax_record": {"p": ["1950-01-01", until], "v": [15.0] * 13, "d": ["2015-11-07"] * 12 + ["2015"]},
        },
    }


INDEX = load_index(
    {
        "dataset_updated": "2026-11-06",
        "stations": [
            _sheet(LILLE, "LILLE-LESQUIN (59)", 50.57, 3.09),
            _sheet("59178001", "DOUAI (59)", 50.38, 3.09),
            _sheet("62000001", "ARRAS (62)", 50.29, 2.78),
            _sheet("59999001", "CLOSED (59)", 50.60, 3.10, until="2001-01-01"),  # no longer measures: never asked
        ],
    }
)


def _collector(provider: Provider) -> MeteoFranceStationDailyCollector:
    return MeteoFranceStationDailyCollector("http://gateway.test/", http_client=provider.client())


def _per_station(answers: dict[str, Any]) -> Provider:
    def answer(request: httpx.Request) -> httpx.Response:
        station = request.url.params["station"]
        value = answers.get(station)
        if isinstance(value, int):
            return httpx.Response(value, json={"detail": "x"})
        return httpx.Response(200, json=value if value is not None else payload(station))

    return Provider(answer=answer)


# ------------------------------------------------------------------------------------------------ the gateway answer


def test_the_gateway_answer_is_read_with_its_doubtful_measure() -> None:
    raw = parse_gateway_station_daily(payload(), LILLE)
    assert raw.last_day == "2026-11-17" and raw.fetched_at_utc == 1795004373
    last = raw.days[-1]
    assert (last.rain_mm, last.tmax_c, last.gust_ms, last.doubtful) == (22.3, 15.5, None, ["gust_ms"])


@pytest.mark.parametrize(
    "bad",
    [
        payload(model="meteofrance-piaf"),
        payload(station="12345678"),
        payload(days=[_day("2026-11-17", 5.0, 1.0, 99.0, 5.0, 1.0, 2.0)]),  # a maximum of 99 degrees
        payload(days=[_day("2026-11-17", -1.0, 1.0, 9.0, 5.0, 1.0, 2.0)]),  # negative rain
        payload(days=[_day("2026-11-17", 1.0, 1.0, 9.0, 5.0, 1.0, 2.0), _day("2026-11-16", 1.0, 1.0, 9.0, 5.0, 1.0, 2.0)]),  # not in order
        {"days": "no"},
    ],
)
def test_an_answer_that_is_not_dpclims_or_is_impossible_is_refused(bad: Any) -> None:
    with pytest.raises(ValueError):
        parse_gateway_station_daily(bad, LILLE)


async def test_one_station_is_asked_once_and_the_number_is_checked(sim_clock: SimClock) -> None:
    provider = _per_station({})
    collector = _collector(provider)
    await collector.fetch_station(LILLE)
    await collector.fetch_station(LILLE)
    assert provider.count == 1 and provider.requests[0].url.path == "/v1/station-daily"
    with pytest.raises(ValueError):
        await collector.fetch_station("../etc")
    assert provider.count == 1


# ------------------------------------------------------------------------------------------------ the service


async def test_the_nearest_measuring_station_gives_its_days_and_the_normals_of_the_month(sim_clock: SimClock) -> None:
    provider = _per_station({})
    out = await query_climate_recent(_collector(provider), lat=50.63, lon=3.06, days=2, max_distance_km=50.0, index=INDEX)
    assert out.status == "available"
    assert out.station is not None and out.station.id == LILLE and out.station.distance_km < 10 and out.station.active
    assert [d.date for d in out.days] == ["2026-11-16", "2026-11-17"]
    assert out.last_day == "2026-11-17" and out.fetched_at_utc == 1795004373
    assert out.days[-1].gust_ms is None and out.days[-1].doubtful == ["gust_ms"]
    assert out.normals_month is not None and (out.normals_month.month, out.normals_month.tmax_mean_c, out.normals_month.tmin_mean_c) == (11, 9.0, 4.0)
    assert out.derived == ["station", "station.distance_km"]
    assert "Météo-France" in out.attribution and "Fiches climatologiques" in out.attribution
    assert provider.count == 1  # the closed station was never asked


async def test_a_silent_station_leaves_the_next_one_to_answer(sim_clock: SimClock) -> None:
    provider = _per_station({LILLE: payload(days=[])})
    out = await query_climate_recent(_collector(provider), lat=50.57, lon=3.09, days=3, max_distance_km=50.0, index=INDEX)
    assert out.status == "available" and out.station is not None and out.station.id == "59178001"
    assert [r.url.params["station"] for r in provider.requests] == [LILLE, "59178001"]


async def test_no_station_has_data_is_said_and_names_the_nearest(sim_clock: SimClock) -> None:
    provider = _per_station({LILLE: payload(days=[]), "59178001": payload("59178001", days=[]), "62000001": payload("62000001", days=[])})
    out = await query_climate_recent(_collector(provider), lat=50.57, lon=3.09, days=3, max_distance_km=50.0, index=INDEX)
    assert out.status == "no_recent_data" and out.days == [] and out.station is not None and out.station.id == LILLE


async def test_a_gateway_that_fails_is_unavailable_and_does_not_try_every_station(sim_clock: SimClock) -> None:
    provider = _per_station({LILLE: 503, "59178001": 503, "62000001": 503})
    out = await query_climate_recent(_collector(provider), lat=50.57, lon=3.09, days=3, max_distance_km=50.0, index=INDEX)
    assert out.status == "unavailable" and out.days == [] and out.station is None
    assert provider.count <= 2  # one try (and the collector's retry), not one per station


async def test_far_from_every_measuring_station_nothing_is_estimated(sim_clock: SimClock) -> None:
    provider = _per_station({})
    out = await query_climate_recent(_collector(provider), lat=43.3, lon=-0.4, days=3, max_distance_km=50.0, index=INDEX)
    assert out.status == "outside_coverage" and out.station is None and "km" in (out.note or "") and provider.count == 0


async def test_without_a_gateway_it_says_so() -> None:
    out = await query_climate_recent(None, lat=50.57, lon=3.09, days=3, max_distance_km=50.0, index=INDEX)
    assert out.status == "not_configured" and "VIGIE_METEO_GATEWAY_URL" in (out.note or "")


async def test_an_open_circuit_is_unavailable_too(sim_clock: SimClock) -> None:
    provider = _per_station({LILLE: 503})
    collector = _collector(provider)
    for _ in range(4):
        with pytest.raises((ValueError, CollectorUnavailableError, httpx.HTTPError)):
            await collector.fetch_station(LILLE)
    out = await query_climate_recent(collector, lat=50.57, lon=3.09, days=3, max_distance_km=50.0, index=INDEX)
    assert out.status == "unavailable"


# ------------------------------------------------------------------------------------------------ the route


def test_the_route_without_a_gateway_says_not_configured_and_checks_its_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import aggregator, app

    monkeypatch.setattr(aggregator, "meteofrance_station_daily", None)
    client = TestClient(app)
    body = client.get("/v1/climate/recent", params={"lat": 50.6, "lon": 3.1}).json()
    assert body["status"] == "not_configured" and body["days"] == [] and body["attribution"]
    assert client.get("/v1/climate/recent", params={"lat": 50.6, "lon": 3.1, "days": 15}).status_code == 422
    assert client.get("/v1/climate/recent", params={"lat": 91, "lon": 3.1}).status_code == 422


def test_the_route_serves_the_days_of_a_station_through_the_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import aggregator, app

    provider = _per_station({})
    monkeypatch.setattr(aggregator, "meteofrance_station_daily", _collector(provider))
    reply = TestClient(app).get("/v1/climate/recent", params={"lat": 50.63, "lon": 3.06, "days": 2})
    assert reply.status_code == 200 and reply.headers["cache-control"] == "public, max-age=3600"
    body = reply.json()
    assert body["status"] == "available" and body["station"]["id"] == LILLE
    assert [d["date"] for d in body["days"]] == ["2026-11-16", "2026-11-17"]
