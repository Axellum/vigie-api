"""The activities overview (``app/services/activities_overview.py``, ``GET /v1/activities/overview``): every spot's four activities in short.

It must say what ``GET /v1/activities/{spot_id}`` says (same rules, same code), cut to the headlines; add no request to a provider;
leave out a spot the wave model gave nothing for; and say, spot by spot, when the official coastal warnings could not be read. No network.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import clock
from app.collectors.surf import parse_swell, parse_wind
from app.main import aggregator, app
from app.models.activities import WindRange
from app.models.schemas import SeverityLevel
from app.models.vigilance import VigilancePhenomenon
from app.services import activities, activities_overview, surf
from tests.test_activities import (
    HOUR,
    NOW,
    NOW_HOUR,
    SPOT_FILE,
    SPOT_ID,
    SWELL,
    WIND,
    FakeVigilances,
    response,
    vigilance,
)

RANGE = WindRange(min_kn=12.0, max_kn=25.0)


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """One spot in the file, and no memory kept from another test."""
    monkeypatch.setattr(surf, "spot_file", lambda: SPOT_FILE)
    monkeypatch.setattr(activities_overview, "_memo", None)
    monkeypatch.setattr(activities_overview, "_warnings_memo", {})
    return SPOT_FILE


class FakeCollector:
    """The every-spot answers of the surf collector, and how many times they were asked."""

    def __init__(self, swell: Any = None, wind: Any = None, stale: bool = False) -> None:
        self.swell = {SPOT_ID: parse_swell(SWELL)} if swell is None else swell
        self.wind = {SPOT_ID: parse_wind(WIND)} if wind is None else wind
        self.stale = stale
        self.asked: list[str] = []

    async def fetch_all(self, kind: str, points: tuple[Any, ...]) -> tuple[dict[str, dict[str, Any]], bool]:
        self.asked.append(kind)
        data = self.swell if kind == "swell" else self.wind
        if isinstance(data, BaseException):
            raise data
        return data, self.stale


def pure(alerts: list[Any] | None = None, known: bool = True, rng: WindRange = RANGE) -> Any:
    return activities_overview.build({SPOT_ID: parse_swell(SWELL)}, {SPOT_ID: parse_wind(WIND)}, {SPOT_ID: (alerts or [], known)}, rng, NOW)


# --------------------------------------------------------------------------------------------------------------- the headlines


def test_each_spot_gets_the_headlines_of_its_own_forecast() -> None:
    """The overview is the forecast of the spot cut short, not a second set of rules: every field equals the one of ``activities.build``."""
    spot = surf.find_spot(SPOT_ID)
    base = surf.build_forecast(spot, parse_swell(SWELL), parse_wind(WIND), 1, NOW)
    full = activities.build(base, activities.tide_context(spot, base.hours[0].time_utc, 1, NOW), [], True, [], RANGE, NOW)
    result = pure()
    assert result.count == 1 and [s.id for s in result.spots] == [SPOT_ID]
    listed = result.spots[0].activities
    assert [a.id for a in listed] == ["surf", "kite", "boat", "shore_fishing"]
    for short, long in zip(listed, full.activities, strict=True):
        assert (short.available, short.verdict, short.reason, short.best_slot) == (long.available, long.verdict, long.reason, long.best_slot)
    assert any(a.verdict is not None for a in listed)


def test_now_is_the_verdict_of_the_current_hour() -> None:
    spot = surf.find_spot(SPOT_ID)
    base = surf.build_forecast(spot, parse_swell(SWELL), parse_wind(WIND), 1, NOW)
    full = activities.build(base, activities.tide_context(spot, base.hours[0].time_utc, 1, NOW), [], True, [], RANGE, NOW)
    for short, long in zip(pure().spots[0].activities, full.activities, strict=True):
        wanted = next((h.verdict for h in long.hours if h.time_utc == NOW_HOUR), None)
        assert short.now == wanted


def test_the_overview_carries_the_rules_the_limits_and_the_credit() -> None:
    result = pure()
    assert result.rules == activities.RULES and result.wind_range == RANGE
    assert "Not for safety" in result.disclaimer and "OpenStreetMap" in result.attribution and "Open-Meteo" in result.attribution
    assert "spots.activities.verdict" in result.derived and result.stale is False


def test_the_kite_follows_the_range_asked_for() -> None:
    narrow = pure(rng=WindRange(min_kn=40.0, max_kn=50.0))
    kite = next(a for a in narrow.spots[0].activities if a.id == "kite")
    assert narrow.wind_range == WindRange(min_kn=40.0, max_kn=50.0)
    assert kite.verdict in ("poor", None)


# -------------------------------------------------------------------------------------------------------------------- warnings


def full_headlines(alerts: list[Any]) -> list[tuple[str, Any, Any]]:
    """The headlines of ``GET /v1/activities/{spot_id}`` for the same warnings: what the overview has to say too."""
    spot = surf.find_spot(SPOT_ID)
    base = surf.build_forecast(spot, parse_swell(SWELL), parse_wind(WIND), 1, NOW)
    full = activities.build(base, activities.tide_context(spot, base.hours[0].time_utc, 1, NOW), alerts, True, [], RANGE, NOW)
    return [(a.id, a.verdict, a.reason) for a in full.activities]


def test_an_orange_coastal_warning_is_said_and_gives_the_verdicts_of_the_spot_route() -> None:
    warning = vigilance(VigilancePhenomenon.COASTAL, SeverityLevel.ORANGE, onset_utc=NOW_HOUR, expires_utc=NOW_HOUR + 48 * HOUR)
    alerts = activities.coastal_alerts(response([warning], ["ok"]))
    spot = pure(alerts=alerts).spots[0]
    assert spot.coastal_level == "ORANGE" and spot.warnings == "ok"
    assert [(a.id, a.verdict, a.reason) for a in spot.activities] == full_headlines(alerts)


def test_a_red_coastal_warning_makes_every_available_activity_poor_and_the_highest_level_wins() -> None:
    alerts = activities.coastal_alerts(
        response(
            [
                vigilance(VigilancePhenomenon.COASTAL, SeverityLevel.ORANGE, onset_utc=NOW_HOUR, expires_utc=NOW_HOUR + 48 * HOUR),
                vigilance(VigilancePhenomenon.COASTAL, SeverityLevel.RED, onset_utc=NOW_HOUR, expires_utc=NOW_HOUR + 48 * HOUR),
            ],
            ["ok"],
        )
    )
    spot = pure(alerts=alerts).spots[0]
    assert spot.coastal_level == "RED"
    assert [(a.id, a.verdict, a.reason) for a in spot.activities] == full_headlines(alerts)
    assert all((a.verdict, a.reason, a.now) == ("poor", "vigilance.red", "poor") for a in spot.activities if a.available)


def test_a_warning_over_before_the_window_or_not_begun_is_not_reported() -> None:
    over = vigilance(VigilancePhenomenon.COASTAL, SeverityLevel.RED, onset_utc=NOW_HOUR - 10 * HOUR, expires_utc=NOW_HOUR - HOUR)
    later = vigilance(VigilancePhenomenon.COASTAL, SeverityLevel.RED, onset_utc=NOW_HOUR + 30 * HOUR, expires_utc=NOW_HOUR + 40 * HOUR)
    assert pure(alerts=activities.coastal_alerts(response([over, later], ["ok"]))).spots[0].coastal_level is None


def test_warnings_that_could_not_be_read_are_said_and_cap_nothing() -> None:
    spot = pure(known=False).spots[0]
    assert spot.warnings == "unknown" and spot.coastal_level is None


# ------------------------------------------------------------------------------------------------------------------- the service


@pytest.mark.asyncio
async def test_the_service_asks_the_two_every_spot_answers_and_each_spots_warnings_once() -> None:
    collector, vigilances = FakeCollector(), FakeVigilances(response([], ["ok"]))
    result = await activities_overview.overview(collector, vigilances, NOW)  # type: ignore[arg-type]
    assert sorted(collector.asked) == ["swell", "wind"]  # the two answers the surf overview keeps: no request to a provider more
    assert len(vigilances.calls) == 1 and result.spots[0].warnings == "ok"
    again = await activities_overview.overview(collector, vigilances, NOW + 60)  # type: ignore[arg-type]
    assert len(vigilances.calls) == 1 and again is result  # the same hour, five minutes: the same answer


@pytest.mark.asyncio
async def test_a_spot_the_wave_model_gave_nothing_for_is_left_out() -> None:
    result = await activities_overview.overview(FakeCollector(swell={}), FakeVigilances(response([], ["ok"])), NOW)  # type: ignore[arg-type]
    assert result.count == 0 and result.spots == []


@pytest.mark.asyncio
async def test_no_swell_at_all_is_the_surf_error() -> None:
    with pytest.raises(surf.NoSurfForecast):
        await activities_overview.overview(FakeCollector(swell=RuntimeError("down")), FakeVigilances(response([], ["ok"])), NOW)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_without_the_wind_the_service_still_answers_and_says_nothing_is_known() -> None:
    result = await activities_overview.overview(FakeCollector(wind=RuntimeError("down")), FakeVigilances(response([], ["ok"])), NOW)  # type: ignore[arg-type]
    assert result.count == 1 and all(a.verdict is None for a in result.spots[0].activities if a.id in ("kite", "boat"))


@pytest.mark.asyncio
async def test_a_failing_warning_source_never_fails_the_answer() -> None:
    result = await activities_overview.overview(FakeCollector(), FakeVigilances(error=RuntimeError("boom")), NOW)  # type: ignore[arg-type]
    assert result.spots[0].warnings == "unknown" and result.spots[0].coastal_level is None


@pytest.mark.asyncio
async def test_a_source_that_covers_nobody_is_unknown_not_clear() -> None:
    result = await activities_overview.overview(FakeCollector(), FakeVigilances(response([], [])), NOW)  # type: ignore[arg-type]
    assert result.spots[0].warnings == "unknown"


@pytest.mark.asyncio
async def test_a_stale_answer_is_flagged() -> None:
    result = await activities_overview.overview(FakeCollector(stale=True), FakeVigilances(response([], ["ok"])), NOW)  # type: ignore[arg-type]
    assert result.stale is True


@pytest.mark.asyncio
@pytest.mark.parametrize(("low", "high"), [(0, 10), (-5, 10), (20, 10), (10, 10), (12, 61)])
async def test_the_wind_range_has_to_make_sense(low: float, high: float) -> None:
    with pytest.raises(ValueError):
        await activities_overview.overview(FakeCollector(), FakeVigilances(response([], ["ok"])), NOW, low, high)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------------------------------- the route


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(clock, "_wall", lambda: float(NOW))
    swell, wind = {SPOT_ID: parse_swell(SWELL)}, {SPOT_ID: parse_wind(WIND)}
    monkeypatch.setattr(aggregator.surf, "fetch_all", AsyncMock(side_effect=lambda kind, points: ((swell if kind == "swell" else wind), False)))
    monkeypatch.setattr(aggregator.vigilances, "for_point", AsyncMock(return_value=response([], ["ok"])))
    return TestClient(app)


def test_the_route_is_not_taken_for_a_spot(client: TestClient) -> None:
    reply = client.get("/v1/activities/overview")
    assert reply.status_code == 200 and "spots" in reply.json()  # not the 404 "no surf spot 'overview'"
    assert reply.headers["Cache-Control"] == "public, max-age=300"


def test_the_route_gives_every_spot_in_short(client: TestClient) -> None:
    body = client.get("/v1/activities/overview").json()
    assert body["count"] == 1 and body["wind_range"] == {"min_kn": 12.0, "max_kn": 25.0}
    spot = body["spots"][0]
    assert spot["id"] == SPOT_ID and spot["warnings"] == "ok" and spot["coastal_level"] is None
    assert [a["id"] for a in spot["activities"]] == ["surf", "kite", "boat", "shore_fishing"]
    assert set(spot["activities"][0]) == {"id", "available", "verdict", "reason", "best_slot", "now"}  # no hours, no criteria: it is short


def test_the_route_takes_the_wind_range_of_the_kite(client: TestClient) -> None:
    assert client.get("/v1/activities/overview", params={"wind_min_kn": 15, "wind_max_kn": 30}).json()["wind_range"] == {"min_kn": 15.0, "max_kn": 30.0}


@pytest.mark.parametrize("params", [{"wind_min_kn": 20, "wind_max_kn": 10}, {"wind_min_kn": 10, "wind_max_kn": 10}, {"wind_min_kn": 0}, {"wind_max_kn": 61}])
def test_the_route_refuses_what_is_not_a_wind_range(client: TestClient, params: dict[str, Any]) -> None:
    assert client.get("/v1/activities/overview", params=params).status_code == 422


def test_the_route_says_when_the_swell_is_missing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aggregator.surf, "fetch_all", AsyncMock(side_effect=RuntimeError("down")))
    assert client.get("/v1/activities/overview").status_code == 503


def test_the_route_is_documented_and_the_spot_route_still_answers(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    operation = paths["/v1/activities/overview"]["get"]
    assert set(operation["responses"]) >= {"200", "422", "503"} and "Not for safety" in operation["description"]
    assert "/v1/activities/{spot_id}" in paths
