"""The activities forecast (``app/services/activities.py``; ADR 0016): the verdicts of surf, kite, boat and shore fishing, hour by hour.

The rules one by one on made-up hours, then a real forecast of Hossegor (the answers of Open-Meteo kept in ``tests/fixtures/surf`` and the
tide computed by Vigie), the official coastal warnings that cap it, and the requests it costs (none more than the surf page). No network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app import clock
from app.collectors.surf import SurfForecastCollector, parse_swell, parse_wind
from app.main import aggregator, app
from app.models.activities import ActivityAlert, ActivityHour, WindRange
from app.models.schemas import SeverityLevel
from app.models.surf import SurfCriterionScore, SurfForecast, SurfHour, SurfRating, SurfWind, Swell
from app.models.vigilance import (
    Vigilance,
    VigilanceArea,
    VigilanceCoverage,
    VigilancePhenomenon,
    VigilanceQuery,
    VigilancesResponse,
    VigilanceStatus,
)
from app.services import activities, surf
from tests.helpers import Provider

FIXTURES = Path(__file__).parent / "fixtures" / "surf"
SWELL = json.loads((FIXTURES / "mfwam-hossegor.json").read_text(encoding="utf-8"))
WIND = json.loads((FIXTURES / "wind-hossegor.json").read_text(encoding="utf-8"))
FIRST_HOUR = 1793145600  # 2026-10-28 00:00 UTC, the first hour of both answers
NOW = FIRST_HOUR + 10 * 3600 + 600  # 10:10 UTC
NOW_HOUR = FIRST_HOUR + 10 * 3600
HOUR = 3600
SPOT_ID = "hossegor-plage-centrale"
SPOT_FILE: dict[str, Any] = {
    "built_utc": "2026-10-28T22:00:00Z",
    "source": "test",
    "licence": "ODbL-1.0",
    "attribution": "© OpenStreetMap contributors",
    "derived": ["facing_deg", "place", "id"],
    "spots": [
        {
            "id": SPOT_ID,
            "name": "Plage Centrale",
            "place": "Hossegor",
            "lat": 43.6644,
            "lon": -1.4432,
            "facing_deg": 280,
            "beach_surface": "sand",
            "bottom": None,
            "best_tide": None,
            "best_swell_from_deg": None,
            "osm": "way/1",
            "evidence": [],
        }
    ],
}
RANGE = WindRange(min_kn=12.0, max_kn=25.0)


@pytest.fixture(autouse=True)
def spot_file(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(surf, "spot_file", lambda: SPOT_FILE)
    return SPOT_FILE


# ------------------------------------------------------------------------------------------------------------- made-up hours


def make_hour(
    t: int = FIRST_HOUR,
    *,
    daylight: Optional[bool] = True,
    speed_kn: Optional[float] = 15.0,
    gust_kn: Optional[float] = 18.0,
    from_deg: Optional[int] = 280,
    relative: Optional[str] = "onshore",
    sea_m: Optional[float] = 0.8,
    rating: Optional[SurfRating] = None,
) -> SurfHour:
    return SurfHour(
        time_utc=t,
        daylight=daylight,
        waves=Swell(height_m=sea_m),
        swell=Swell(),
        secondary_swell=Swell(),
        wind=SurfWind(
            speed_kmh=None if speed_kn is None else speed_kn * 1.852,
            gust_kmh=None if gust_kn is None else gust_kn * 1.852,
            from_deg=from_deg,
            relative=relative,  # type: ignore[arg-type]
        ),
        rating=rating,
    )


def states(criteria: list[Any]) -> dict[str, tuple[Optional[str], Optional[str]]]:
    return {c.name: (c.state, c.verdict) for c in criteria}


def rating(*points: Optional[int], period_s: float = 12.0, height_m: float = 1.5) -> SurfRating:
    names = ("swell_height", "swell_period", "swell_direction", "wind", "tide")
    units = ("m", "s", "deg", "km/h", "m")
    values = (height_m, period_s, 10.0, 8.0, 1.0)
    scores = [SurfCriterionScore(name=n, value=v, unit=u, points=p, max_points=2) for n, v, u, p in zip(names, values, units, points, strict=True)]  # type: ignore[arg-type]
    known = [s for s in scores if s.points is not None]
    return SurfRating(points=sum(s.points or 0 for s in known), out_of=2 * len(known), criteria=scores, unknown=[s.name for s in scores if s.points is None])


# --------------------------------------------------------------------------------------------------------------------- Beaufort


@pytest.mark.parametrize(
    ("knots", "force"),
    [(0, 0), (0.4, 0), (0.5, 1), (3.4, 1), (3.5, 2), (6.5, 3), (10.4, 3), (10.5, 4), (16.4, 4), (16.5, 5), (21.5, 6), (27.4, 6), (33.5, 8), (63.5, 12), (120, 12)],
)
def test_the_beaufort_force_of_a_wind(knots: float, force: int) -> None:
    assert activities.beaufort(knots) == force


# ------------------------------------------------------------------------------------------------------------------------ kite


@pytest.mark.parametrize(
    ("knots", "state", "verdict"),
    [
        (8.5, "too_light", "poor"),
        (9.5, "a_bit_light", "possible"),
        (12.5, "in_range", "good"),
        (24.5, "in_range", "good"),
        (27.0, "a_bit_strong", "possible"),
        (28.5, "too_strong", "poor"),
    ],
)
def test_the_kite_wind_against_the_range(knots: float, state: str, verdict: str) -> None:
    wind = activities._kite_criteria(make_hour(speed_kn=knots, gust_kn=knots), 12.0, 25.0)[0]
    assert (wind.name, wind.unit, wind.state, wind.verdict) == ("wind", "kn", state, verdict)
    assert wind.value == round(knots, 1)


def test_the_range_is_the_one_asked_for() -> None:
    hour = make_hour(speed_kn=15.5, gust_kn=15.5)
    assert states(activities._kite_criteria(hour, 12.0, 25.0))["wind"] == ("in_range", "good")
    assert states(activities._kite_criteria(hour, 18.0, 30.0))["wind"] == ("a_bit_light", "possible")  # 2.5 kn under
    assert states(activities._kite_criteria(hour, 20.0, 30.0))["wind"] == ("too_light", "poor")


@pytest.mark.parametrize(("gust", "state", "verdict"), [(19.5, "steady", "good"), (22.5, "gusty", "possible"), (26.0, "very_gusty", "poor")])
def test_the_gust_gap(gust: float, state: str, verdict: str) -> None:
    gap = activities._kite_criteria(make_hour(speed_kn=15.0, gust_kn=gust), 12.0, 25.0)[1]
    assert (gap.name, gap.state, gap.verdict) == ("gust_gap", state, verdict)


@pytest.mark.parametrize(("relative", "state", "verdict"), [("onshore", "onshore", "good"), ("cross-shore", "cross_shore", "good"), ("offshore", "offshore", "possible")])
def test_a_wind_from_the_land_keeps_the_kite_at_possible(relative: str, state: str, verdict: str) -> None:
    side = activities._kite_criteria(make_hour(relative=relative), 12.0, 25.0)[2]
    assert (side.name, side.state, side.verdict) == ("wind_direction", state, verdict)
    one = activities._judge("kite", make_hour(speed_kn=18.0, gust_kn=20.0, relative=relative), RANGE, None)
    assert one is not None
    assert one.hour.verdict == ("possible" if relative == "offshore" else "good")
    assert one.hour.reason == ("wind_direction.offshore" if relative == "offshore" else None)


def test_an_unknown_wind_gives_no_kite_verdict() -> None:
    one = activities._judge("kite", make_hour(speed_kn=None, gust_kn=None, from_deg=None, relative=None), RANGE, None)
    assert one is not None and one.hour.verdict is None and one.hour.reason is None
    assert all(c.value is None and c.state is None and c.verdict is None for c in one.criteria)


def test_a_gust_alone_unknown_leaves_the_rest_to_judge() -> None:
    one = activities._judge("kite", make_hour(speed_kn=15.0, gust_kn=None), RANGE, None)
    assert one is not None and one.hour.verdict == "good"
    assert states(one.criteria)["gust_gap"] == (None, None)


# ------------------------------------------------------------------------------------------------------------------------ boat


@pytest.mark.parametrize(
    ("knots", "gust", "sea", "verdict", "reason"),
    [
        (15.0, 18.0, 0.8, "good", None),  # force 4, gusts force 5, a moderate sea
        (19.0, 20.0, 0.8, "possible", "wind_force.fresh"),  # force 5
        (24.0, 26.0, 0.8, "poor", "wind_force.strong_or_more"),  # force 6
        (15.0, 28.0, 0.8, "possible", "gust_force.strong"),  # gusts force 7
        (15.0, 35.0, 0.8, "poor", "gust_force.gale_or_more"),  # gusts force 8
        (10.0, 12.0, 1.5, "possible", "sea_height.moderate"),
        (10.0, 12.0, 2.6, "poor", "sea_height.high"),
        (24.0, 36.0, 3.0, "poor", "wind_force.strong_or_more"),  # the first criterion at the lowest level gives the reason
    ],
)
def test_the_boat_verdict(knots: float, gust: float, sea: float, verdict: str, reason: Optional[str]) -> None:
    one = activities._judge("boat", make_hour(speed_kn=knots, gust_kn=gust, sea_m=sea), RANGE, None)
    assert one is not None
    assert (one.hour.verdict, one.hour.reason) == (verdict, reason)
    assert [c.name for c in one.criteria] == ["wind_force", "gust_force", "sea_height"]


def test_the_boat_criteria_are_in_beaufort_and_metres() -> None:
    one = activities._judge("boat", make_hour(speed_kn=19.0, gust_kn=25.0, sea_m=1.4), RANGE, None)
    assert one is not None
    wind, gust, sea = one.criteria
    assert (wind.value, wind.unit, gust.value, gust.unit, sea.value, sea.unit) == (5, "Beaufort", 6, "Beaufort", 1.4, "m")


# ------------------------------------------------------------------------------------------------------------------ shore fishing

LOW = FIRST_HOUR + 6 * HOUR + 20 * 60  # a low water at 06:20 UTC


def context(coefficient: Optional[int] = 80, lows: Optional[list[int]] = None) -> activities.TideContext:
    lows = [LOW] if lows is None else lows
    return activities.TideContext(extremes=[], coefficients=[], low_waters=lows, coefficient_of_low={low: coefficient for low in lows})


def fishing(t: int, ctx: activities.TideContext, **kwargs: Any) -> activities._Judged:
    one = activities._judge("shore_fishing", make_hour(t, speed_kn=8.0, gust_kn=10.0, sea_m=0.6, **kwargs), RANGE, ctx)
    assert one is not None
    return one


def test_shore_fishing_is_judged_in_the_two_hours_before_the_low_water() -> None:
    ctx = context()
    # An hour is judged at its middle: the hour from 03:00 has its middle 2 h 50 before the low water, the one from 04:00 1 h 50.
    outside = fishing(FIRST_HOUR + 3 * HOUR, ctx)
    assert (outside.hour.verdict, outside.hour.reason) == ("poor", "low_water.outside_window")
    for start in (4, 5):
        inside = fishing(FIRST_HOUR + start * HOUR, ctx)
        assert (inside.hour.verdict, inside.hour.reason) == ("good", None)
        assert states(inside.criteria)["low_water"] == ("in_window", "good")
    window = fishing(FIRST_HOUR + 5 * HOUR, ctx).criteria[0]
    assert (window.name, window.unit, window.value) == ("low_water", "min", 50.0)  # the middle, 05:30, is 50 minutes before 06:20


def test_the_hour_of_the_low_water_is_already_the_next_tide() -> None:
    # The middle of the hour from 06:00 is after the low water of 06:20: the next low water is the one that counts.
    ctx = context(lows=[LOW, LOW + 12 * HOUR + 25 * 60])
    after = fishing(FIRST_HOUR + 6 * HOUR, ctx)
    assert (after.hour.verdict, after.hour.reason) == ("poor", "low_water.outside_window")
    assert after.criteria[0].value == pytest.approx(12 * 60 + 25 - 10)


def test_without_a_low_water_to_come_the_hour_has_no_verdict_whatever_the_wind() -> None:
    ctx = context(lows=[LOW])
    after = fishing(FIRST_HOUR + 6 * HOUR, ctx)  # nothing known after 06:20
    assert after.hour.verdict is None and after.hour.reason is None
    assert states(after.criteria)["low_water"] == (None, None) and states(after.criteria)["wind_force"] == ("up_to_fresh", "good")


@pytest.mark.parametrize(
    ("coefficient", "state", "verdict"),
    [(30, "small", "poor"), (49, "small", "poor"), (50, "moderate", "possible"), (69, "moderate", "possible"), (70, "large", "good"), (99, "large", "good"), (100, "very_large", "possible"), (118, "very_large", "possible")],
)
def test_the_coefficient_bands_and_the_snsm_warning_from_100(coefficient: int, state: str, verdict: str) -> None:
    one = fishing(FIRST_HOUR + 5 * HOUR, context(coefficient))
    assert states(one.criteria)["coefficient"] == (state, verdict)
    assert one.hour.verdict == verdict
    assert one.hour.reason == (None if verdict == "good" else f"coefficient.{state}")


def test_an_unknown_coefficient_counts_for_nothing_but_the_window_stays_required() -> None:
    one = fishing(FIRST_HOUR + 5 * HOUR, context(None))
    assert states(one.criteria)["coefficient"] == (None, None) and one.hour.verdict == "good"


@pytest.mark.parametrize(("knots", "verdict", "reason"), [(20.0, "good", None), (24.0, "possible", "wind_force.strong"), (30.0, "poor", "wind_force.near_gale_or_more")])
def test_the_wind_for_shore_fishing_tolerates_one_force_more_than_the_boat(knots: float, verdict: str, reason: Optional[str]) -> None:
    # Force 5 (17-21 kn) is fine on the foreshore, 6 asks care, 7 and over is no weather for it; the boat is good only up to force 4.
    one = activities._judge("shore_fishing", make_hour(FIRST_HOUR + 5 * HOUR, speed_kn=knots, gust_kn=knots, sea_m=0.6), RANGE, context())
    assert one is not None and (one.hour.verdict, one.hour.reason) == (verdict, reason)


def test_the_sea_counts_for_shore_fishing_too() -> None:
    one = activities._judge("shore_fishing", make_hour(FIRST_HOUR + 5 * HOUR, speed_kn=8.0, gust_kn=10.0, sea_m=2.0), RANGE, context())
    assert one is not None and (one.hour.verdict, one.hour.reason) == ("possible", "sea_height.moderate")


def test_the_coefficient_of_a_low_water_is_the_nearest_brest_high_water() -> None:
    spot = surf.find_spot(SPOT_ID)
    ctx = activities.tide_context(spot, NOW_HOUR, 2, NOW)
    assert ctx is not None and len(ctx.low_waters) >= 4
    assert ctx.extremes == sorted(ctx.extremes, key=lambda e: e.time_utc)
    assert all(e.kind in ("high", "low") for e in ctx.extremes)
    for low in ctx.low_waters:
        near = [c for c in ctx.coefficients if abs(c.brest_high_water_utc - low) <= activities.BREST_LAG_S]
        wanted = min(near, key=lambda c: (abs(c.brest_high_water_utc - low), c.value)).value if near else None
        assert ctx.coefficient_of_low[low] == wanted
    assert sum(v is not None for v in ctx.coefficient_of_low.values()) >= len(ctx.low_waters) - 1
    assert all(20 <= v <= 130 for v in ctx.coefficient_of_low.values() if v is not None)


def test_no_tide_model_near_means_no_tide_context() -> None:
    spot = surf.find_spot(SPOT_ID).model_copy(update={"lat": 10.0, "lon": -150.0})  # the middle of the Pacific, far from any cell
    assert activities.tide_context(spot, NOW_HOUR, 1, NOW) is None
    assert activities.tide_context(surf.find_spot(SPOT_ID), 1_000_000_000, 1, 1_000_000_000) is None  # dates the model does not reach


# --------------------------------------------------------------------------------------------------------------------- surf


def test_the_surf_verdict_is_the_share_of_the_points_of_the_rating() -> None:
    def judge(*points: Optional[int]) -> tuple[Any, Any]:
        verdict, reason, *_ = activities._judge_surf(make_hour(rating=rating(*points)))
        return verdict, reason

    assert judge(2, 2, 2, 2, 2) == ("good", None)
    assert judge(2, 2, 2, 1, 1) == ("good", None)  # 8 of 10: 0.8
    assert judge(2, 2, 1, 1, 1)[0] == "possible"  # 7 of 10: 0.7
    assert judge(2, 1, 1, 1, 0)[0] == "possible"  # 5 of 10: 0.5 exactly
    assert judge(1, 1, 1, 1, 0)[0] == "poor"  # 4 of 10
    assert judge(2, 2, 2, None, None) == ("good", None)  # 6 of 6: three criteria are enough
    assert judge(2, 2, None, None, None) == (None, None)  # 4 of 4: too few to say


def test_the_surf_reason_is_the_criterion_that_lost_the_most() -> None:
    hour = make_hour(speed_kn=18.0, relative="onshore", rating=rating(2, 2, 1, 0, 1))
    verdict, reason, criteria, points, out_of = activities._judge_surf(hour)
    assert (verdict, reason, points, out_of) == ("possible", "wind.onshore", 6, 10)  # 0.6; only the wind lost both points
    assert [c.name for c in criteria] == ["swell_height", "swell_period", "swell_direction", "wind", "tide"]
    assert all(c.max_points == 2 for c in criteria) and [c.points for c in criteria] == [2, 2, 1, 0, 1]
    assert [c.verdict for c in criteria] == ["good", "good", "possible", "poor", "possible"]
    short = make_hour(speed_kn=18.0, relative="onshore", rating=rating(2, 0, 2, 0, 0, period_s=6.0))
    verdict, reason, *_ = activities._judge_surf(short)
    assert verdict == "poor" and reason == "swell_period.short"  # three criteria at 0 of 2: the first of them gives the reason
    verdict, reason, *_ = activities._judge_surf(make_hour(speed_kn=18.0, relative="onshore", rating=rating(2, 2, 2, 0, 2)))
    assert (verdict, reason) == ("good", None)  # 8 of 10: 0.8


@pytest.mark.parametrize(("height_m", "state"), [(0.3, "too_small"), (0.49, "too_small"), (4.0, "too_big"), (6.5, "too_big")])
def test_a_swell_too_small_or_too_big_makes_the_surf_poor_whatever_the_rest(height_m: float, state: str) -> None:
    # Every other criterion at the top: 8 of 10 would be `good` by the share (the owner's call of 2026-11-08: no waves, no surf).
    hour = make_hour(rating=rating(0, 2, 2, 2, 2, height_m=height_m))
    verdict, reason, criteria, points, out_of = activities._judge_surf(hour)
    assert (verdict, reason, points, out_of) == ("poor", f"swell_height.{state}", 8, 10)
    assert states(criteria)["swell_height"] == (state, "poor") and states(criteria)["wind"][1] == "good"
    # Judged first: three criteria are no longer needed to say it, since the swell height is always known when there is a rating.
    only_two = activities._judge_surf(make_hour(rating=rating(0, 2, None, None, None, height_m=height_m)))
    assert only_two[:2] == ("poor", f"swell_height.{state}")


@pytest.mark.parametrize("height_m", [0.5, 0.99, 2.6, 3.9])
def test_a_swell_worth_one_point_is_left_to_the_share(height_m: float) -> None:
    # 0.5 m is the first height that scores (1 point), and 3.9 m the last before the 4 m that scores 0 again.
    good = activities._judge_surf(make_hour(rating=rating(1, 2, 2, 2, 2, height_m=height_m)))
    assert good[:2] == ("good", None)  # 9 of 10
    low = activities._judge_surf(make_hour(rating=rating(1, 0, 0, 0, 0, height_m=height_m)))
    assert low[0] == "poor" and low[1] is not None and low[1].startswith("swell_period.")  # 1 of 10: the share decides, not the height


def test_without_a_rating_there_is_no_surf_verdict() -> None:
    assert activities._judge_surf(make_hour(rating=None)) == (None, None, [], None, None)


@pytest.mark.parametrize(
    ("name", "value", "points", "state"),
    [
        ("swell_height", 0.3, 0, "too_small"),
        ("swell_height", 0.8, 1, "small"),
        ("swell_height", 1.5, 2, "right"),
        ("swell_height", 3.0, 1, "big"),
        ("swell_height", 5.0, 0, "too_big"),
        ("swell_period", 7.0, 0, "short"),
        ("swell_period", 9.0, 1, "medium"),
        ("swell_period", 12.0, 2, "long"),
        ("swell_direction", 20.0, 2, "facing"),
        ("swell_direction", 45.0, 1, "oblique"),
        ("swell_direction", 70.0, 0, "off_axis"),
        ("tide", 1.0, 2, "right_stage"),
        ("tide", 1.0, 0, "wrong_stage"),
        ("wind", 8.0, 2, "light"),
        ("swell_height", None, None, None),
    ],
)
def test_the_state_of_a_surf_criterion(name: str, value: Optional[float], points: Optional[int], state: Optional[str]) -> None:
    assert activities._surf_state(name, value, points, make_hour(speed_kn=4.0)) == state


def test_the_surf_wind_states_follow_the_rating() -> None:
    assert activities._surf_state("wind", 25.0, 2, make_hour(speed_kn=14.0, relative="offshore")) == "offshore"
    assert activities._surf_state("wind", 15.0, 1, make_hour(speed_kn=8.0, relative="cross-shore")) == "cross_shore"
    assert activities._surf_state("wind", 30.0, 0, make_hour(speed_kn=16.0, relative="onshore")) == "onshore"
    assert activities._surf_state("wind", 40.0, 0, make_hour(speed_kn=22.0, relative="offshore")) == "strong"


# ---------------------------------------------------------------------------------------------------------------- the warnings


def alert(level: Optional[str], onset: Optional[int] = None, ends: Optional[int] = None, expires: Optional[int] = None) -> ActivityAlert:
    return ActivityAlert(id="w", source="meteoalarm", attribution="x", level=level, onset_utc=onset, expires_utc=expires, ends_utc=ends)


def test_a_warning_covers_the_hours_it_overlaps() -> None:
    a = alert("RED", onset=NOW_HOUR + 1800, ends=NOW_HOUR + 3 * HOUR)
    assert activities._cap_at([a], NOW_HOUR - HOUR) is None  # the hour before ends before it starts
    assert activities._cap_at([a], NOW_HOUR) == "red"  # the hour that holds the onset
    assert activities._cap_at([a], NOW_HOUR + 2 * HOUR) == "red"
    assert activities._cap_at([a], NOW_HOUR + 3 * HOUR) is None  # the end is exclusive
    assert activities._cap_at([alert("RED", onset=None, expires=NOW_HOUR + HOUR)], NOW_HOUR) == "red"  # no end given: the expiry
    assert activities._cap_at([alert("RED", onset=None, ends=None, expires=None)], NOW_HOUR + 99 * HOUR) == "red"  # no dates: always


def test_the_highest_level_wins_and_yellow_changes_nothing() -> None:
    assert activities._cap_at([alert("YELLOW")], NOW_HOUR) is None
    assert activities._cap_at([alert("GREEN"), alert(None)], NOW_HOUR) is None
    assert activities._cap_at([alert("YELLOW"), alert("ORANGE")], NOW_HOUR) == "orange"
    assert activities._cap_at([alert("ORANGE"), alert("RED")], NOW_HOUR) == "red"


def test_the_cap() -> None:
    assert activities._capped("kite", "good", None, "red") == ("poor", "vigilance.red")
    assert activities._capped("surf", None, None, "red") == ("poor", "vigilance.red")  # an official red warning needs no data
    assert activities._capped("boat", "good", None, "orange") == ("possible", "vigilance.orange")
    assert activities._capped("shore_fishing", "good", None, "orange") == ("possible", "vigilance.orange")
    assert activities._capped("boat", "poor", "wind_force.strong", "orange") == ("poor", "wind_force.strong")  # never raised
    assert activities._capped("boat", "possible", "sea_height.moderate", "orange") == ("possible", "sea_height.moderate")
    assert activities._capped("kite", "good", None, "orange") == ("good", None)  # the wave-submersion warning is not about the wind
    assert activities._capped("surf", "good", None, "orange") == ("good", None)
    assert activities._capped("boat", "good", None, None) == ("good", None)


def vigilance(phenomenon: Optional[VigilancePhenomenon], level: Optional[SeverityLevel], status: VigilanceStatus = VigilanceStatus.NEW, **dates: int) -> Vigilance:
    return Vigilance(
        id=f"fr-{phenomenon}-{level}-{status}",
        source="meteoalarm",
        attribution="MeteoAlarm",
        identifier="x",
        status=status,
        level=level,
        phenomenon=phenomenon,
        area=VigilanceArea(),
        **dates,
    )


def response(vigilances: list[Vigilance], coverage: list[str]) -> VigilancesResponse:
    return VigilancesResponse(
        server_utc=NOW,
        query=VigilanceQuery(lat=43.66, lon=-1.44),
        vigilances=vigilances,
        coverage=[VigilanceCoverage(source="meteoalarm", country="FR", status=s) for s in coverage],  # type: ignore[arg-type]
    )


def test_only_the_coastal_warnings_that_stand_are_kept() -> None:
    answer = response(
        [
            vigilance(VigilancePhenomenon.COASTAL, SeverityLevel.ORANGE, onset_utc=NOW_HOUR, expires_utc=NOW_HOUR + 5 * HOUR),
            vigilance(VigilancePhenomenon.WIND, SeverityLevel.RED),
            vigilance(VigilancePhenomenon.COASTAL, SeverityLevel.RED, VigilanceStatus.CANCEL),
            vigilance(VigilancePhenomenon.COASTAL, SeverityLevel.YELLOW, effective_utc=NOW_HOUR - 60),
            vigilance(None, SeverityLevel.RED),
        ],
        ["ok"],
    )
    kept = activities.coastal_alerts(answer)
    assert [(a.level, a.onset_utc, a.expires_utc, a.attribution) for a in kept] == [
        ("ORANGE", NOW_HOUR, NOW_HOUR + 5 * HOUR, "MeteoAlarm"),
        ("YELLOW", NOW_HOUR - 60, None, "MeteoAlarm"),  # no onset given: the effective time
    ]


@pytest.mark.parametrize(("coverage", "read"), [(["ok"], True), (["stale"], True), (["ok", "unavailable"], True), (["unavailable"], False), ([], False)])
def test_an_empty_list_is_no_news_unless_a_source_answered(coverage: list[str], read: bool) -> None:
    assert activities.warnings_read(response([], coverage)) is read


# ------------------------------------------------------------------------------------------------------- the verdict of the day


def day(pattern: str, start: int = NOW_HOUR) -> list[activities._Judged]:
    """One character per hour from ``start``: g, p, x a good, possible or poor daylight hour; G, P, X the same at night; . a daylight hour
    with no verdict."""
    table: dict[str, tuple[bool, Optional[str]]] = {
        "g": (True, "good"), "p": (True, "possible"), "x": (True, "poor"), "G": (False, "good"), "P": (False, "possible"), "X": (False, "poor"), ".": (True, None),
    }
    out = []
    for i, ch in enumerate(pattern):
        daylight, verdict = table[ch]
        out.append(activities._Judged(ActivityHour(time_utc=start + i * HOUR, daylight=daylight, verdict=verdict, reason=None if verdict == "good" else "r"), []))  # type: ignore[arg-type]
    return out


def headline(pattern: str) -> tuple[Any, Any, Any]:
    verdict, slot, _ = activities._headline(day(pattern), NOW_HOUR)
    return verdict, None if slot is None else (slot.start_utc - NOW_HOUR) // HOUR, None if slot is None else (slot.end_utc - NOW_HOUR) // HOUR


def test_the_headline_is_the_best_verdict_of_the_daylight_hours_with_its_longest_run() -> None:
    assert headline("xxpppgggpppggggxxxxxxxxx") == ("good", 11, 15)  # two runs of good, 3 hours then 4: the longer one
    assert headline("ppppxxpppxxxxxxxxxxxxxxx") == ("possible", 0, 4)
    assert headline("xxxxxxxxxxxxxxxxxxxxxxxx")[1:] == (None, None)  # all poor: the verdict, no slot to go
    assert headline("xxxxxxxxxxxxxxxxxxxxxxxx")[0] == "poor"


def test_a_tie_goes_to_the_first_run() -> None:
    assert headline("ggxxxggxxxxxxxxxxxxxxxxx") == ("good", 0, 2)


def test_night_hours_never_make_the_verdict_nor_join_a_run() -> None:
    assert headline("GGGGxxxxxxxxxxxxxxxxxxxx")[0] == "poor"  # good at night is not a day to go
    assert headline("ggGggxxxxxxxxxxxxxxxxxxx") == ("good", 0, 2)  # the night hour cuts the run
    assert headline("pXXXgGGGGggggggggggggggg") == ("good", 9, 24)  # the 15 hours after the night, not the lone hour before it


def test_what_is_beyond_the_next_24_hours_does_not_count() -> None:
    assert headline("xxxxxxxxxxxxxxxxxxxxxxxxggggggg")[0] == "poor"
    assert headline("ppppppppppppppppppppppppgggggggg") == ("possible", 0, 24)  # the slot stays inside the window


def test_hours_without_a_verdict_are_left_out() -> None:
    assert headline("........................") == (None, None, None)
    assert headline("..p.........................")[0] == "possible"
    assert headline("g.gg....................") == ("good", 2, 4)  # an unknown hour cuts a run too


def test_the_detail_is_the_first_hour_of_the_slot_else_the_first_daylight_hour() -> None:
    _, slot, detail = activities._headline(day("xpppxxgggxxxxxxxxxxxxxxx"), NOW_HOUR)
    assert detail is not None and slot is not None and detail.hour.time_utc == slot.start_utc == NOW_HOUR + 6 * HOUR
    _, slot, detail = activities._headline(day("XXxxxxxxxxxxxxxxxxxxxxxx"), NOW_HOUR)
    assert slot is None and detail is not None and detail.hour.time_utc == NOW_HOUR + 2 * HOUR


# ------------------------------------------------------------------------------------------------------------------ Hossegor


def hossegor(alerts: Optional[list[ActivityAlert]] = None, known: bool = True, with_tide: bool = True, days: int = 2) -> Any:
    spot = surf.find_spot(SPOT_ID)
    swell, wind = parse_swell(SWELL), parse_wind(WIND)
    base = surf.build_forecast(spot, swell, wind, days, NOW)
    ctx = activities.tide_context(spot, base.hours[0].time_utc, days, NOW) if with_tide else None
    sun = activities._suns(wind, base.hours[0].time_utc, days)
    return base, activities.build(base, ctx, alerts or [], known, sun, RANGE, NOW)


def verdicts(result: Any, name: str) -> list[Optional[str]]:
    return [h.verdict for h in next(a for a in result.activities if a.id == name).hours]


def test_the_forecast_of_hossegor_has_the_four_activities_hour_by_hour() -> None:
    base, result = hossegor()
    assert [a.id for a in result.activities] == ["surf", "kite", "boat", "shore_fishing"]
    assert all(a.available for a in result.activities)
    times = [h.time_utc for h in base.hours]
    assert len(times) == 48 and times[0] == NOW_HOUR
    for a in result.activities:
        assert [h.time_utc for h in a.hours] == times
        assert [h.daylight for h in a.hours] == [h.daylight for h in base.hours]
        assert a.verdict in ("good", "possible", "poor") and (a.best_slot is None) == (a.verdict == "poor")
    assert (result.spot.id, result.days, result.wind_range) == (SPOT_ID, 2, RANGE)
    assert result.vigilance.status == "ok" and result.vigilance.alerts == []
    assert result.generated_utc == int(NOW) and result.stale is False


def test_the_surf_verdict_of_each_hour_follows_the_rating_of_the_surf_page() -> None:
    base, result = hossegor()
    surf_hours = next(a for a in result.activities if a.id == "surf").hours
    seen = set()
    for hour, mine in zip(base.hours, surf_hours, strict=True):
        r = hour.rating
        swell_points = None if r is None else next(c.points for c in r.criteria if c.name == "swell_height")
        if r is not None and swell_points == 0:
            assert (mine.verdict, mine.reason.split(".")[0]) == ("poor", "swell_height")  # no waves, or too big: nothing makes up for it
            seen.add(mine.verdict)
            continue
        if r is None or r.out_of < surf.MIN_OUT_OF_FOR_BEST:
            assert mine.verdict is None
            continue
        share = r.points / r.out_of
        assert mine.verdict == ("good" if share >= 0.75 else "possible" if share >= 0.5 else "poor")
        assert (mine.reason is None) == (mine.verdict == "good")
        seen.add(mine.verdict)
    assert len(seen) >= 2  # the three days of the fixture are not all alike


def test_the_headline_is_recomputed_from_the_hours() -> None:
    _, result = hossegor()
    for a in result.activities:
        window = [h for h in a.hours if NOW_HOUR <= h.time_utc < NOW_HOUR + 24 * HOUR and h.daylight and h.verdict]
        if not window:
            assert a.verdict is None
            continue
        best = max((h.verdict for h in window), key={"poor": 0, "possible": 1, "good": 2}.__getitem__)
        assert a.verdict == best
        if a.best_slot is not None:
            slot_hours = [h for h in a.hours if a.best_slot.start_utc <= h.time_utc < a.best_slot.end_utc]
            assert slot_hours and all(h.verdict == best and h.daylight for h in slot_hours)
            assert a.detail is not None and a.detail.time_utc == a.best_slot.start_utc and a.detail.verdict == best
        else:
            assert a.detail is not None and a.detail.time_utc == window[0].time_utc


def test_shore_fishing_is_only_possible_before_the_low_waters_of_the_tide() -> None:
    _, result = hossegor()
    assert result.tide is not None and any(e.kind == "low" for e in result.tide.extremes)
    fishing_hours = next(a for a in result.activities if a.id == "shore_fishing").hours
    lows = sorted(e.time_utc for e in result.tide.extremes if e.kind == "low")
    inside = 0
    for h in fishing_hours:
        middle = h.time_utc + HOUR // 2
        following = next((low for low in lows if low >= middle), None)
        if following is None:
            continue
        if following - middle <= 2 * HOUR:
            inside += 1
            assert h.verdict != "poor" or h.reason != "low_water.outside_window"
        else:
            assert (h.verdict, h.reason) == ("poor", "low_water.outside_window")
    assert 6 <= inside <= 10  # two hours before each of about four low waters in two days


def test_the_tide_of_the_answer_starts_just_before_the_first_hour() -> None:
    _, result = hossegor()
    assert result.tide is not None
    assert min(e.time_utc for e in result.tide.extremes) >= NOW_HOUR - 6 * HOUR
    assert min(c.brest_high_water_utc for c in result.tide.coefficients_estimated) >= NOW_HOUR - 6 * HOUR
    assert "Not the official coefficient" in result.tide.note and "not for navigation" in result.tide.note.lower()


def test_the_sun_is_the_one_of_the_wind_answer() -> None:
    _, result = hossegor()
    assert 2 <= len(result.sun) <= 3
    assert result.sun[0].sunrise_utc == WIND["daily"]["sunrise"][0] and result.sun[0].sunset_utc == WIND["daily"]["sunset"][0]
    assert activities._suns(None, NOW_HOUR, 2) == []


def test_a_red_coastal_warning_makes_everything_poor() -> None:
    _, result = hossegor([alert("RED", onset=NOW_HOUR - HOUR, ends=NOW_HOUR + 10 * 24 * HOUR)])
    for a in result.activities:
        assert a.verdict == "poor" and a.best_slot is None and a.reason == "vigilance.red"
        assert {h.verdict for h in a.hours} == {"poor"} and {h.reason for h in a.hours} == {"vigilance.red"}
    assert result.vigilance.status == "ok" and result.vigilance.alerts[0].level == "RED"


def calm_day() -> SurfForecast:
    """A calm day made up: a light onshore wind of 10 knots, a sea of 0.6 m, daylight from 06:00 to 19:59, two days from 00:00."""
    hours = [make_hour(FIRST_HOUR + i * HOUR, daylight=6 <= i % 24 < 20, speed_kn=10.0, gust_kn=12.0, sea_m=0.6) for i in range(48)]
    return SurfForecast(
        generated_utc=int(FIRST_HOUR), spot=surf.find_spot(SPOT_ID), days=2, swell_model="test", hours=hours, rules={}, derived=[], attribution="x"
    )


def calm_build(alerts: list[ActivityAlert]) -> Any:
    lows = [FIRST_HOUR + 9 * HOUR + 20 * 60, FIRST_HOUR + 21 * HOUR + 45 * 60, FIRST_HOUR + 34 * HOUR]  # 09:20, 21:45, then 10:00 the day after
    return activities.build(calm_day(), context(80, lows), alerts, True, [], RANGE, FIRST_HOUR + 6 * HOUR)  # it is 06:00


def test_a_calm_day_is_good_for_the_boat_and_for_the_foreshore_before_the_low_water() -> None:
    result = calm_build([])
    boat = next(a for a in result.activities if a.id == "boat")
    assert boat.verdict == "good" and boat.best_slot is not None and boat.reason is None
    assert (boat.best_slot.start_utc, boat.best_slot.end_utc) == (FIRST_HOUR + 6 * HOUR, FIRST_HOUR + 20 * HOUR)  # from sunrise to sunset
    fishing_activity = next(a for a in result.activities if a.id == "shore_fishing")
    assert fishing_activity.verdict == "good" and fishing_activity.best_slot is not None
    assert (fishing_activity.best_slot.start_utc, fishing_activity.best_slot.end_utc) == (FIRST_HOUR + 7 * HOUR, FIRST_HOUR + 9 * HOUR)  # 07:00-09:00


def test_an_orange_one_keeps_the_boat_and_the_foreshore_at_possible_at_best() -> None:
    plain = calm_build([])
    orange = calm_build([alert("ORANGE", onset=FIRST_HOUR - HOUR, ends=FIRST_HOUR + 10 * 24 * HOUR)])
    for name in ("surf", "kite"):
        assert next(a for a in orange.activities if a.id == name) == next(a for a in plain.activities if a.id == name)
    for name in ("boat", "shore_fishing"):
        before, after = verdicts(plain, name), verdicts(orange, name)
        assert "good" in before, name  # the case is only worth something if the day was good for it somewhere
        assert "good" not in after
        assert after == ["possible" if v == "good" else v for v in before]
        capped = next(a for a in orange.activities if a.id == name)
        assert capped.verdict == "possible" and capped.reason == "vigilance.orange"
        assert capped.best_slot == next(a for a in plain.activities if a.id == name).best_slot  # the same hours, one level lower


def test_a_wind_unknown_leaves_the_boat_and_the_foreshore_without_a_verdict() -> None:
    hour = make_hour(FIRST_HOUR + 5 * HOUR, speed_kn=None, gust_kn=None, sea_m=0.6)
    boat = activities._judge("boat", hour, RANGE, None)
    assert boat is not None and boat.hour.verdict is None and states(boat.criteria)["sea_height"] == ("low", "good")
    shore = activities._judge("shore_fishing", hour, RANGE, context())
    assert shore is not None and shore.hour.verdict is None and states(shore.criteria)["low_water"] == ("in_window", "good")


def test_a_warning_that_starts_later_changes_only_the_hours_it_covers() -> None:
    _, plain = hossegor()
    start = NOW_HOUR + 30 * HOUR + 1800
    _, later = hossegor([alert("RED", onset=start, ends=start + 3 * HOUR)])
    for name in ("surf", "kite", "boat", "shore_fishing"):
        a, b = (next(x for x in r.activities if x.id == name).hours for r in (plain, later))
        for before, after in zip(a, b, strict=True):
            covered = NOW_HOUR + 30 * HOUR <= before.time_utc < start + 3 * HOUR
            assert after == before or covered
            assert (after.verdict, after.reason) == (("poor", "vigilance.red") if covered else (before.verdict, before.reason))


def test_when_the_warnings_cannot_be_read_nothing_is_capped_and_it_is_said() -> None:
    _, plain = hossegor()
    _, unknown = hossegor(known=False)
    assert unknown.vigilance.status == "unknown" and unknown.vigilance.alerts == []
    assert unknown.activities == plain.activities


def test_without_a_tide_shore_fishing_is_not_judged_and_the_rest_is_unchanged() -> None:
    _, plain = hossegor()
    _, none = hossegor(with_tide=False)
    fishing_activity = next(a for a in none.activities if a.id == "shore_fishing")
    assert fishing_activity.available is False and fishing_activity.verdict is None and fishing_activity.hours == [] and fishing_activity.note
    assert none.tide is None
    for name in ("surf", "kite", "boat"):
        assert next(a for a in none.activities if a.id == name) == next(a for a in plain.activities if a.id == name)


def test_without_the_wind_the_wind_activities_are_unknown() -> None:
    spot = surf.find_spot(SPOT_ID)
    base = surf.build_forecast(spot, parse_swell(SWELL), None, 1, NOW)
    result = activities.build(base, None, [], True, [], RANGE, NOW)
    assert result.sun == []
    for a in result.activities:
        if a.available:
            assert a.verdict is None and a.best_slot is None and a.detail is None  # no daylight known: no verdict of the day
            assert a.id == "surf" or {h.verdict for h in a.hours} == {None}


def test_the_answer_is_json_with_its_rules_and_its_limits() -> None:
    _, result = hossegor()
    body = json.loads(result.model_dump_json())
    assert set(body["rules"]) == {"verdict", "headline", "surf", "kite", "boat", "shore_fishing", "vigilance"}
    assert "activities.verdict" in body["derived"] and "tide.coefficients_estimated" in body["derived"]
    assert "Not for safety" in body["disclaimer"] and "rules of Vigie" in body["limits"]
    for credit in ("Open-Meteo", "OpenStreetMap", "EOT20"):
        assert credit in body["attribution"]
    assert body["activities"][0]["detail"]["criteria"][0]["name"] == "swell_height"
    assert "NaN" not in result.model_dump_json()


def test_the_rules_say_every_threshold_the_code_uses() -> None:
    text = " ".join(activities.RULES.values())
    for number in ("75 %", "50 %", "12 to 25 knots", "3 knots", "5 knots", "1.25 m", "2.5 m", "70 to 99", "50 to 69", "2 hours before a low water", "100 and over", "0 points (under 0.5 m, or 4 m and over)"):
        assert number in text, number
    assert (activities.SURF_GOOD_SHARE, activities.SURF_POSSIBLE_SHARE) == (0.75, 0.5)
    assert (activities.SEA_GOOD_BELOW_M, activities.SEA_POSSIBLE_BELOW_M) == (1.25, 2.5)
    assert (activities.DEFAULT_WIND_MIN_KN, activities.DEFAULT_WIND_MAX_KN) == (12.0, 25.0)
    assert activities.BEAUFORT_LOWER_KN == (0, 1, 4, 7, 11, 17, 22, 28, 34, 41, 48, 56, 64)


# ---------------------------------------------------------------------------------------------------------------- the service


class FakeVigilances:
    def __init__(self, answer: Optional[VigilancesResponse] = None, error: Optional[Exception] = None) -> None:
        self.answer, self.error, self.calls = answer, error, []

    async def for_point(self, lat: float, lon: float) -> VigilancesResponse:
        self.calls.append((lat, lon))
        if self.error is not None:
            raise self.error
        assert self.answer is not None
        return self.answer


def provider() -> Provider:
    return Provider(answer=lambda r: httpx.Response(200, json=SWELL if "marine" in r.url.host else WIND), latency_ticks=0)


@pytest.mark.asyncio
async def test_the_activities_cost_the_providers_nothing_more_than_the_surf_page() -> None:
    mine, theirs = provider(), provider()
    vigilances = FakeVigilances(response([], ["ok"]))
    result = await activities.forecast(SurfForecastCollector(http_client=mine.client()), vigilances, SPOT_ID, 3, NOW)
    surf_forecast = await surf.forecast(SurfForecastCollector(http_client=theirs.client()), SPOT_ID, 3, NOW)
    assert mine.count == theirs.count == 2  # one swell, one wind, the sun is read from the wind answer
    assert vigilances.calls == [(43.6644, -1.4432)]
    assert len(result.activities[0].hours) == len(surf_forecast.hours) == 72
    # A second ask, and the surf page after it, ride on the same two cached answers.
    collector = SurfForecastCollector(http_client=mine.client())
    before = mine.count
    await activities.forecast(collector, vigilances, SPOT_ID, 1, NOW)
    await activities.forecast(collector, vigilances, SPOT_ID, 2, NOW + 600)
    await surf.forecast(collector, SPOT_ID, 3, NOW + 900)
    assert mine.count == before + 2


@pytest.mark.asyncio
async def test_the_service_matches_the_pure_build_and_reads_the_coastal_warning() -> None:
    warning = vigilance(VigilancePhenomenon.COASTAL, SeverityLevel.ORANGE, onset_utc=NOW_HOUR, expires_utc=NOW_HOUR + 20 * HOUR)
    vigilances = FakeVigilances(response([warning, vigilance(VigilancePhenomenon.WIND, SeverityLevel.RED)], ["ok"]))
    result = await activities.forecast(SurfForecastCollector(http_client=provider().client()), vigilances, SPOT_ID, 2, NOW, 15.0, 30.0)
    assert result.wind_range == WindRange(min_kn=15.0, max_kn=30.0)
    assert [(a.level, a.source) for a in result.vigilance.alerts] == [("ORANGE", "meteoalarm")] and result.vigilance.status == "ok"
    # The service is the pure build fed with the same answers: the swell and the wind, the tide, the sun, the warning, the range asked for.
    spot = surf.find_spot(SPOT_ID)
    base = surf.build_forecast(spot, parse_swell(SWELL), parse_wind(WIND), 2, NOW)
    first = base.hours[0].time_utc
    expected = activities.build(
        base,
        activities.tide_context(spot, first, 2, NOW),
        activities.coastal_alerts(vigilances.answer),
        True,
        activities._suns(parse_wind(WIND), first, 2),
        WindRange(min_kn=15.0, max_kn=30.0),
        NOW,
    )
    assert result == expected
    assert result.tide is not None and result.sun


@pytest.mark.asyncio
async def test_a_failing_warning_source_never_fails_the_answer() -> None:
    vigilances = FakeVigilances(error=RuntimeError("boom"))
    result = await activities.forecast(SurfForecastCollector(http_client=provider().client()), vigilances, SPOT_ID, 1, NOW)
    assert result.vigilance.status == "unknown" and result.vigilance.alerts == []
    assert all(a.available for a in result.activities)


@pytest.mark.asyncio
async def test_a_point_no_source_covers_is_unknown_not_clear() -> None:
    result = await activities.forecast(SurfForecastCollector(http_client=provider().client()), FakeVigilances(response([], [])), SPOT_ID, 1, NOW)
    assert result.vigilance.status == "unknown"


@pytest.mark.asyncio
async def test_an_unknown_spot_and_a_missing_swell_are_the_surf_errors() -> None:
    collector = SurfForecastCollector(http_client=provider().client())
    with pytest.raises(surf.UnknownSpot):
        await activities.forecast(collector, FakeVigilances(response([], ["ok"])), "nowhere", 1, NOW)
    broken = SurfForecastCollector(http_client=provider().client())
    broken.fetch_swell = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
    with pytest.raises(surf.NoSurfForecast):
        await activities.forecast(broken, FakeVigilances(response([], ["ok"])), SPOT_ID, 1, NOW)


@pytest.mark.asyncio
async def test_without_the_wind_answer_the_service_still_answers() -> None:
    collector = SurfForecastCollector(http_client=provider().client())
    collector.fetch_wind = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
    result = await activities.forecast(collector, FakeVigilances(response([], ["ok"])), SPOT_ID, 1, NOW)
    assert result.sun == [] and all(a.verdict is None for a in result.activities if a.available)


@pytest.mark.asyncio
@pytest.mark.parametrize(("low", "high"), [(0, 10), (-5, 10), (20, 10), (10, 10), (12, 61)])
async def test_the_wind_range_has_to_make_sense(low: float, high: float) -> None:
    with pytest.raises(ValueError):
        await activities.forecast(SurfForecastCollector(http_client=provider().client()), FakeVigilances(response([], ["ok"])), SPOT_ID, 1, NOW, low, high)


def test_the_models_are_used_as_the_surf_ones_are() -> None:
    assert activities.MAX_DAYS == 3 and issubclass(surf.UnknownSpot, LookupError)
    assert isinstance(hossegor()[0], SurfForecast)


# ---------------------------------------------------------------------------------------------------------------- the route


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(clock, "_wall", lambda: float(NOW))
    monkeypatch.setattr(aggregator.surf, "fetch_swell", AsyncMock(return_value=(parse_swell(SWELL), False)))
    monkeypatch.setattr(aggregator.surf, "fetch_wind", AsyncMock(return_value=(parse_wind(WIND), False)))
    monkeypatch.setattr(aggregator.vigilances, "for_point", AsyncMock(return_value=response([], ["ok"])))
    return TestClient(app)


def test_the_route_gives_the_activities_of_a_spot(client: TestClient) -> None:
    reply = client.get(f"/v1/activities/{SPOT_ID}")
    assert reply.status_code == 200
    assert reply.headers["Cache-Control"] == "public, max-age=300"
    body = reply.json()
    assert body["spot"]["id"] == SPOT_ID and body["days"] == 2 and body["wind_range"] == {"min_kn": 12.0, "max_kn": 25.0}
    assert [a["id"] for a in body["activities"]] == ["surf", "kite", "boat", "shore_fishing"]
    assert all(len(a["hours"]) == 48 for a in body["activities"])
    assert body["vigilance"] == {"status": "ok", "alerts": []} and body["tide"]["extremes"]
    assert body["activities"][1]["detail"]["criteria"][0]["name"] == "wind"
    aggregator.vigilances.for_point.assert_awaited_once_with(43.6644, -1.4432)  # type: ignore[attr-defined]


def test_the_route_takes_the_days_and_the_wind_range_of_the_kite(client: TestClient) -> None:
    body = client.get(f"/v1/activities/{SPOT_ID}", params={"days": 3, "wind_min_kn": 15, "wind_max_kn": 30}).json()
    assert body["days"] == 3 and body["wind_range"] == {"min_kn": 15.0, "max_kn": 30.0}
    assert len(body["activities"][0]["hours"]) == 72


@pytest.mark.parametrize(
    "params",
    [{"days": 0}, {"days": 4}, {"wind_min_kn": 0}, {"wind_max_kn": 61}, {"wind_min_kn": 25, "wind_max_kn": 12}, {"wind_min_kn": 20, "wind_max_kn": 20}, {"wind_min_kn": "x"}],
)
def test_the_route_refuses_what_is_not_a_range_of_days_or_of_wind(client: TestClient, params: dict[str, Any]) -> None:
    assert client.get(f"/v1/activities/{SPOT_ID}", params=params).status_code == 422


def test_the_route_says_when_the_spot_or_the_swell_is_missing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.get("/v1/activities/nowhere").status_code == 404
    monkeypatch.setattr(aggregator.surf, "fetch_swell", AsyncMock(side_effect=RuntimeError("down")))
    broken = client.get(f"/v1/activities/{SPOT_ID}")
    assert broken.status_code == 503 and "wave model" in broken.json()["detail"]


def test_the_route_is_documented_with_its_errors(client: TestClient) -> None:
    operation = client.get("/openapi.json").json()["paths"]["/v1/activities/{spot_id}"]["get"]
    assert set(operation["responses"]) >= {"200", "404", "422", "503"}
    assert "Not for safety" in operation["description"] or "**Not for safety.**" in operation["description"]
