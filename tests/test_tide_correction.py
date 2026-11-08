"""The modelled tide corrected with the nearest REFMAR tide gauge (``app/services/tide_correction.py``, ``GET /v1/tides``; ADR 0017).

Nothing here reads the network: the gauge is a fake that answers measures made from the model plus a known gap.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import clock
from app.collectors.refmar import RefmarCollector, parse_observations
from app.main import aggregator, app
from app.services import tide_correction as tc
from app.services import tides
from app.services.tides import MINUTE, mjd_from_unix

# 2026-10-28 12:00 UTC
NOW = 1793188800
BREST = (48.3829, -4.4950)


def measures(gap_m: float, until: float, minutes: int = 800) -> list[tuple[float, float]]:
    """What the gauge of Brest would have measured if the sea were ``gap_m`` above the model: one value a minute up to ``until`` (unix)."""
    station = next(s for s in tc.stations() if s.id == "3")
    model = tides.model()
    moments = until - 60.0 * np.arange(minutes)[::-1]
    heights = model.heights(model.nearest_cell(station.lat, station.lon).constants, np.array([mjd_from_unix(m) for m in moments]))
    return [(float(m), float(h) + station.offset_m + gap_m) for m, h in zip(moments, heights, strict=True)]


class FakeGauge:
    def __init__(self, observations: Any) -> None:
        self.observations, self.asked = observations, []

    async def fetch_recent(self, station_id: str) -> list[tuple[float, float]]:
        self.asked.append(station_id)
        if isinstance(self.observations, Exception):
            raise self.observations
        return self.observations  # type: ignore[no-any-return]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(clock, "_wall", lambda: float(NOW))
    return TestClient(app)


def with_gauge(monkeypatch: pytest.MonkeyPatch, observations: Any) -> FakeGauge:
    gauge = FakeGauge(observations)
    monkeypatch.setattr(aggregator, "refmar", gauge)
    return gauge


# --------------------------------------------------------------------------------------------------------------- the rule


def test_the_gap_is_the_mean_of_the_last_twelve_and_a_half_hours_and_fades() -> None:
    mjd = mjd_from_unix(NOW) - np.arange(800)[::-1] * MINUTE
    gap = np.where(np.arange(800) < 50, 9.0, 0.30)  # the 50 oldest minutes are outside the window
    found = tc.trailing_anomaly(mjd, gap, mjd_from_unix(NOW))
    assert found is not None
    anomaly, last, minutes = found
    assert anomaly == pytest.approx(0.30) and last == pytest.approx(mjd[-1]) and minutes == tc.WINDOW_MINUTES + 1
    correction = tc.Correction(tc.Anomaly(tc.stations()[0], 5.0, last, 0.30, minutes), None)
    at = correction.at(np.array([last - 1 / 24, last, last + 12 / 24, last + 24 / 24]))
    assert at[0] == 0.0 and at[1] == pytest.approx(0.30) and at[2] == pytest.approx(0.30 / np.e) and at[3] == pytest.approx(0.30 / np.e**2)


def test_an_old_measure_or_a_thin_window_gives_no_gap() -> None:
    mjd = mjd_from_unix(NOW) - np.arange(800)[::-1] * MINUTE
    gap = np.full(800, 0.1)
    assert tc.trailing_anomaly(mjd, gap, mjd_from_unix(NOW) + 61 * MINUTE) is None  # the last measure is 61 minutes old
    assert tc.trailing_anomaly(mjd, gap, mjd_from_unix(NOW) + 59 * MINUTE) is not None
    thin = np.full(800, np.nan)
    thin[-100:] = 0.1
    assert tc.trailing_anomaly(mjd, thin, mjd_from_unix(NOW)) is None  # 100 of 745 minutes
    assert tc.trailing_anomaly(mjd[:0], gap[:0], mjd_from_unix(NOW)) is None


def test_the_stations_are_inside_the_model_and_have_a_datum() -> None:
    found = tc.stations()
    assert {s.id for s in found} >= {"3", "6349"} and len(found) == 11
    for station in found:
        tides.model().nearest_cell(station.lat, station.lon)  # a cell within the radius, or this raises
        assert 2.0 < station.offset_m < 7.0  # a chart datum lies metres below the mean sea level on these macro-tidal coasts
    near = tc.nearest_station(*BREST)
    assert near is not None and near[0].id == "3" and near[1] < 1.0
    assert tc.nearest_station(43.0, 5.0) is None and tc.nearest_station(50.0, -30.0) is None


def test_refmars_answer_is_read_as_measures() -> None:
    payload = {
        "data": [
            {"timestamp": "2026/11/08 09:17:00", "value": 1.6668},
            {"timestamp": "2026/11/08 09:16:00", "value": None},
            {"timestamp": "2026/11/08 09:15:00", "value": "x"},
            {"timestamp": "bad", "value": 1.0},
            {"timestamp": "2026/11/08 09:14:00", "value": 1.6},
        ]
    }
    read = parse_observations(payload)
    assert [value for _, value in read] == [1.6, 1.6668]
    assert read[1][0] - read[0][0] == 180.0 and read[1][0] == datetime(2026, 11, 8, 9, 17, tzinfo=timezone.utc).timestamp()
    for broken in ({}, {"data": "x"}, [], None):
        with pytest.raises(ValueError):
            parse_observations(broken)


def test_the_collector_asks_one_request_a_quarter_of_an_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"data": [{"timestamp": "2026/10/28 11:59:00", "value": 2.5}]})

    monkeypatch.setattr(clock, "_wall", lambda: float(NOW))
    collector = RefmarCollector(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def run() -> None:
        for _ in range(3):
            assert await collector.fetch_recent("3") == [(NOW - 60.0, 2.5)]

    asyncio.run(run())
    assert len(sent) == 1 and sent[0].url.path.endswith("/maregraphie/observation/json/3")
    assert sent[0].url.params["sources"] == "1" and sent[0].url.params["dtEnd"] == "2026-10-28T12:00:00Z"
    assert sent[0].url.params["dtStart"] == "2026-10-27T22:00:00Z"


# -------------------------------------------------------------------------------------------------------------- the route


def tide(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/v1/tides", params={"lat": BREST[0], "lon": BREST[1], "days": 1, "interval_minutes": 60, "correct": "true", **params})
    assert response.status_code == 200
    return response.json()  # type: ignore[no-any-return]


def test_the_gap_lifts_the_heights_and_fades(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    plain = tide(client, correct="false")
    gauge = with_gauge(monkeypatch, measures(0.30, NOW - 300))  # the last measure is 5 minutes old
    corrected = tide(client)
    assert gauge.asked == ["3"]
    info = corrected["correction"]
    assert info["applied"] is True and info["reason"] is None and info["station"]["id"] == "3" and info["station"]["distance_km"] < 1.0
    assert info["measured_utc"] == NOW - 300 and info["anomaly_m"] == pytest.approx(0.30, abs=0.01)
    assert info["decay_hours"] == 12 and info["window_minutes"] == 745
    assert "Licence Ouverte 2.0" in info["attribution"] and "SHOM" in info["attribution"] and "correction" in corrected["derived"]
    hours = np.array([(h["time_utc"] - (NOW - 300)) / 3600.0 for h in corrected["heights"]])
    lift = np.array([c["height_m"] - p["height_m"] for c, p in zip(corrected["heights"], plain["heights"], strict=True)])
    after = hours >= 0
    assert lift[after] == pytest.approx(0.30 * np.exp(-hours[after] / 12.0), abs=0.011)
    assert (lift[~after] == 0).all()
    assert corrected["coefficients_estimated"] == plain["coefficients_estimated"]  # the coefficient stays the model's estimate
    assert corrected["now"]["height_m"] == pytest.approx(plain["now"]["height_m"] + 0.30 * np.exp(-(300 / 3600) / 12.0), abs=0.011)


@pytest.mark.parametrize(
    ("observations", "reason"),
    [
        (RuntimeError("REFMAR down"), "measure_unavailable"),
        ([], "no_recent_measure"),
        ("thin", "no_recent_measure"),
        ("old", "no_recent_measure"),
        ("big", "implausible_gap"),
    ],
)
def test_without_a_fresh_measure_the_answer_is_the_models_and_says_why(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, observations: Any, reason: str
) -> None:
    plain = tide(client, correct="false")
    if observations == "old":
        observations = measures(0.30, NOW - 2 * 3600)
    elif observations == "big":
        observations = measures(2.0, NOW - 300)
    elif observations == "thin":
        observations = measures(0.30, NOW - 300, minutes=100)  # 100 of the last 745 minutes
    with_gauge(monkeypatch, observations)
    answer = tide(client)
    assert answer["correction"]["applied"] is False and answer["correction"]["reason"] == reason
    assert answer["correction"]["anomaly_m"] is None and answer["correction"]["measured_utc"] is None
    assert answer["correction"]["attribution"] is None and answer["correction"]["station"]["id"] == "3"
    assert answer["heights"] == plain["heights"] and answer["extremes"] == plain["extremes"] and answer["now"] == plain["now"]


def test_no_gauge_within_fifty_kilometres_asks_nobody(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    gauge = with_gauge(monkeypatch, RuntimeError("must not be asked"))
    answer = client.get("/v1/tides", params={"lat": 43.2, "lon": 5.3, "days": 1, "correct": "true"}).json()  # Marseille: no gauge of the list is near
    assert answer["correction"]["applied"] is False and answer["correction"]["reason"] == "no_station_near"
    assert answer["correction"]["station"] is None and gauge.asked == []


def test_it_is_off_unless_asked_or_the_instance_turns_it_on(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    gauge = with_gauge(monkeypatch, measures(0.30, NOW - 300))
    params = {"lat": BREST[0], "lon": BREST[1], "days": 1}
    assert client.get("/v1/tides", params=params).json()["correction"]["reason"] == "not_asked"  # the default of an instance
    assert tide(client, correct="false")["correction"]["reason"] == "not_asked"
    assert gauge.asked == []
    monkeypatch.setenv(tc.DEFAULT_ON_BY, "on")
    assert client.get("/v1/tides", params=params).json()["correction"]["applied"] is True
    assert client.get("/v1/tides", params={**params, "correct": "false"}).json()["correction"]["reason"] == "not_asked"
    assert gauge.asked == ["3"]


def test_the_real_collector_cannot_reach_the_network_in_a_test(client: TestClient) -> None:
    answer = tide(client)  # the network is refused: "the gauge cannot be read", and the model answers
    assert answer["correction"]["applied"] is False and answer["correction"]["reason"] == "measure_unavailable"
