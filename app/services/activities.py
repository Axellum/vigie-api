"""The activities forecast of a surf spot: a verdict for surf, kite or wing or windsurf, boat and shore fishing (ADR 0016).

It reads what the surf page already reads (``app/services/surf.py``: the swell and the wind of Open-Meteo, the tide computed by Vigie,
the rating of ours), adds the high and low waters with the coefficient Vigie estimates, and the official coastal warnings that cover the
spot (``VigilanceService.for_point``), and turns them into hour by hour verdicts with the thresholds of ``RULES`` (ours, not a school's
nor a federation's: only the Beaufort scale, the 50 km/h of the official strong-wind notice and the colours of a warning come from an
official source, and the words of the SNSM on the coefficient). An unknown value counts for nothing and is said so; nothing is guessed.

No request to a provider is added to the surf page's: the swell and the wind are the two answers it reads, the tide is computed here,
the warnings are the cached ones of ``GET /v1/vigilances``.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Optional

from app import clock
from app.attribution import SURF, credit_text
from app.collectors.surf import SurfForecastCollector
from app.models.activities import (
    ActivitiesForecast,
    Activity,
    ActivityAlert,
    ActivityCriterion,
    ActivityDetail,
    ActivityHour,
    ActivitySlot,
    ActivitySun,
    ActivityTide,
    ActivityVigilance,
    Verdict,
    WindRange,
)
from app.models.schemas import SeverityLevel
from app.models.surf import SurfForecast, SurfHour, SurfSpot
from app.models.tides import COEFFICIENT_NOTE, NOT_FOR_NAVIGATION, TideCoefficient, TideExtreme
from app.models.vigilance import VigilancePhenomenon, VigilancesResponse, VigilanceStatus
from app.services import surf, tides

_log = logging.getLogger("vigie.activities")

HOUR = 3600
HEADLINE_HOURS = 24
KMH_PER_KNOT = 1.852
MAX_DAYS = 3
DEFAULT_WIND_MIN_KN = 12.0
DEFAULT_WIND_MAX_KN = 25.0
MAX_WIND_KN = 60.0

# The thresholds (ours). A band is "below this bound".
SURF_GOOD_SHARE = 0.75
SURF_POSSIBLE_SHARE = 0.5
WIND_RANGE_MARGIN_KN = 3.0
GUST_GAP_GOOD_KN = 5.0
GUST_GAP_POSSIBLE_KN = 8.0
SEA_GOOD_BELOW_M = 1.25  # significant wave height; ours, like the other bounds of a band
SEA_POSSIBLE_BELOW_M = 2.5
FISHING_WINDOW_S = 2 * HOUR
COEFFICIENT_SMALL_BELOW = 50
COEFFICIENT_GOOD_FROM = 70
COEFFICIENT_BIG_FROM = 100  # the SNSM: "100 or more = danger"
TIDE_LEAD_S = 12 * HOUR  # the tide is read this long before the first hour and after the last
BREST_LAG_S = 7 * HOUR  # a low water takes the coefficient of the Brest high water nearest in time, if within this
# Lower bound of each Beaufort force, in knots (the scale as ECCC and the Met Office give it).
BEAUFORT_LOWER_KN = (0, 1, 4, 7, 11, 17, 22, 28, 34, 41, 48, 56, 64)

RULES: dict[str, str] = {
    "verdict": "good, possible or poor, hour by hour: the verdict of an hour is the lowest of its criteria; a criterion whose value is "
    "unknown counts for nothing, except the one an activity cannot be judged without (kite: the wind; boat: the wind; shore fishing: the "
    "low water and the wind), which makes the verdict null; with none known the verdict is null",
    "headline": "the best verdict among the daylight hours of the next 24 hours; best_slot: the longest run of consecutive daylight hours at "
    "that verdict, the first on a tie, and none when that verdict is poor",
    "surf": "the rating of GET /v1/surf/spots/{id} (its rules are there), by its share of points: 75 % and over good, 50 % and over "
    "possible, less poor; with fewer than three criteria known, null. A primary swell worth 0 points (under 0.5 m, or 4 m and over) "
    "makes it poor whatever the other criteria give",
    "kite": "wind within the range asked for (default 12 to 25 knots): good; within 3 knots of it: possible; else poor. Gusts above the mean "
    "wind by 5 knots or less: good; 6 to 8: possible; more: poor. Wind from the land side (offshore): possible at best",
    "boat": "mean wind in Beaufort force (knots rounded): 4 or less good, 5 possible, 6 and over poor. Gusts: force 5 or less good, 6 to 7 "
    "possible, 8 and over poor. Sea (significant wave height): under 1.25 m good, under 2.5 m possible, else poor",
    "shore_fishing": "only in the 2 hours before a low water, judged at the middle of the hour; outside, poor. Coefficient estimated by Vigie "
    "(not SHOM's) of the Brest high water nearest in time to that low water: 70 to 99 good, 50 to 69 possible, 100 and over possible (the "
    "SNSM says 100 and over is a danger), under 50 poor. Wind force 5 or less good, 6 possible, 7 and over poor. Sea under 1.25 m good, "
    "under 2.5 m possible, else poor. Without the low water, null",
    "vigilance": "an official coastal warning (phenomenon coastal; in France the wave-submersion vigilance) in force at the spot: red makes "
    "every verdict poor; orange keeps the boat and the shore fishing at possible at best; yellow changes nothing. Where the warnings could "
    "not be read, nothing is capped and vigilance.status says unknown",
}
DERIVED = [
    "activities",
    "activities.hours",
    "activities.verdict",
    "activities.best_slot",
    "activities.detail",
    "tide.coefficients_estimated",
]
ACTIVITY_ORDER = ("surf", "kite", "boat", "shore_fishing")
# The criteria an activity cannot be judged without: the sea alone says nothing of a day on the water, nor the wind of a low water.
REQUIRED = {"kite": ("wind",), "boat": ("wind_force",), "shore_fishing": ("low_water", "wind_force")}
_LEVEL = {"poor": 0, "possible": 1, "good": 2}
_NO_TIDE_NOTE = "No tide model near this spot, or none for these dates: shore fishing cannot be judged here."


# ----------------------------------------------------------------------------------------------------------------- helpers


def _kn(kmh: Optional[float]) -> Optional[float]:
    return None if kmh is None else kmh / KMH_PER_KNOT


def beaufort(knots: float) -> int:
    """The Beaufort force of a mean wind in knots (rounded to the knot first, as the scale is written)."""
    wanted = math.floor(knots + 0.5)  # half a knot goes up (Python's round() goes to the even one)
    return max(number for number, lower in enumerate(BEAUFORT_LOWER_KN) if wanted >= lower)


def _criterion(
    name: str,
    value: Optional[float],
    unit: str,
    state: Optional[str],
    verdict: Optional[Verdict],
    points: Optional[int] = None,
    max_points: Optional[int] = None,
) -> ActivityCriterion:
    rounded = None if value is None else round(value, 1)
    return ActivityCriterion(name=name, value=rounded, unit=unit, state=state, verdict=verdict, points=points, max_points=max_points)


def _sea(height_m: Optional[float]) -> ActivityCriterion:
    if height_m is None:
        return _criterion("sea_height", None, "m", None, None)
    if height_m < SEA_GOOD_BELOW_M:
        return _criterion("sea_height", height_m, "m", "low", "good")
    if height_m < SEA_POSSIBLE_BELOW_M:
        return _criterion("sea_height", height_m, "m", "moderate", "possible")
    return _criterion("sea_height", height_m, "m", "high", "poor")


def _force(name: str, knots: Optional[float], good_to: int, possible_to: int, states: tuple[str, str, str]) -> ActivityCriterion:
    """A criterion on a Beaufort force: ``good_to`` and below good, up to ``possible_to`` possible, above poor."""
    if knots is None:
        return _criterion(name, None, "Beaufort", None, None)
    force = beaufort(knots)
    if force <= good_to:
        return _criterion(name, force, "Beaufort", states[0], "good")
    if force <= possible_to:
        return _criterion(name, force, "Beaufort", states[1], "possible")
    return _criterion(name, force, "Beaufort", states[2], "poor")


def _worst(criteria: list[ActivityCriterion]) -> tuple[Optional[Verdict], Optional[str]]:
    """The lowest verdict of the criteria that have one, and ``<name>.<state>`` of the first criterion at that level (none when good)."""
    known = [(c, c.verdict) for c in criteria if c.verdict is not None]
    if not known:
        return None, None
    lowest = min(_LEVEL[v] for _, v in known)
    first, verdict = next((c, v) for c, v in known if _LEVEL[v] == lowest)
    return verdict, (None if verdict == "good" else f"{first.name}.{first.state}")


# ------------------------------------------------------------------------------------------------------ one hour, per activity

_SURF_STATES: dict[str, tuple[str, ...]] = {
    "swell_height": ("too_small", "small", "right", "big", "too_big"),
    "swell_period": ("short", "medium", "long"),
    "swell_direction": ("facing", "oblique", "off_axis"),
}
_SURF_BANDS = {
    "swell_height": surf.SWELL_HEIGHT_BANDS,
    "swell_period": surf.SWELL_PERIOD_BANDS,
    "swell_direction": surf.SWELL_ANGLE_BANDS,
}


def _surf_state(name: str, value: Optional[float], points: Optional[int], hour: SurfHour) -> Optional[str]:
    """What the points of a surf criterion mean, as a code (the bands are those of ``surf.rate``)."""
    if points is None:
        return None
    if name in _SURF_BANDS:
        if value is None:
            return None
        index = next(i for i, (bound, _) in enumerate(_SURF_BANDS[name]) if value < bound)
        return _SURF_STATES[name][index]
    if name == "wind":
        if points == 2:
            return "light" if (hour.wind.speed_kmh or 0.0) < surf.LIGHT_WIND_KMH else "offshore"
        if points == 1:
            return "cross_shore"
        return "onshore" if hour.wind.relative == "onshore" else "strong"
    return "right_stage" if points == 2 else "wrong_stage"


def _judge_surf(hour: SurfHour) -> tuple[Optional[Verdict], Optional[str], list[ActivityCriterion], Optional[int], Optional[int]]:
    rating = hour.rating
    if rating is None:
        return None, None, [], None, None
    criteria: list[ActivityCriterion] = []
    for s in rating.criteria:
        verdict: Optional[Verdict] = None
        if s.points is not None:
            verdict = "good" if s.points >= s.max_points else "possible" if s.points > 0 else "poor"
        criteria.append(
            _criterion(s.name, s.value, s.unit, _surf_state(s.name, s.value, s.points, hour), verdict, points=s.points, max_points=s.max_points)
        )
    height = next((c for c in criteria if c.name == "swell_height"), None)
    if height is not None and height.points == 0:
        # A swell too small to ride or too big to paddle into: the other criteria cannot make up for it (the owner's call, 2026-11-08).
        return "poor", f"swell_height.{height.state}", criteria, rating.points, rating.out_of
    if rating.out_of < surf.MIN_OUT_OF_FOR_BEST:
        return None, None, criteria, rating.points, rating.out_of
    share = rating.points / rating.out_of
    verdict = "good" if share >= SURF_GOOD_SHARE else "possible" if share >= SURF_POSSIBLE_SHARE else "poor"
    reason = None
    if verdict != "good":
        scored = [c for c in criteria if c.points is not None and c.max_points]
        worst = min(scored, key=lambda c: (c.points or 0) / (c.max_points or 1))
        reason = f"{worst.name}.{worst.state}"
    return verdict, reason, criteria, rating.points, rating.out_of


def _kite_criteria(hour: SurfHour, low_kn: float, high_kn: float) -> list[ActivityCriterion]:
    speed, gust = _kn(hour.wind.speed_kmh), _kn(hour.wind.gust_kmh)
    if speed is None:
        wind = _criterion("wind", None, "kn", None, None)
    elif low_kn <= speed <= high_kn:
        wind = _criterion("wind", speed, "kn", "in_range", "good")
    elif low_kn - WIND_RANGE_MARGIN_KN <= speed < low_kn:
        wind = _criterion("wind", speed, "kn", "a_bit_light", "possible")
    elif high_kn < speed <= high_kn + WIND_RANGE_MARGIN_KN:
        wind = _criterion("wind", speed, "kn", "a_bit_strong", "possible")
    else:
        wind = _criterion("wind", speed, "kn", "too_light" if speed < low_kn else "too_strong", "poor")
    if speed is None or gust is None:
        gap = _criterion("gust_gap", None, "kn", None, None)
    else:
        spread = max(gust - speed, 0.0)
        if spread <= GUST_GAP_GOOD_KN:
            gap = _criterion("gust_gap", spread, "kn", "steady", "good")
        elif spread <= GUST_GAP_POSSIBLE_KN:
            gap = _criterion("gust_gap", spread, "kn", "gusty", "possible")
        else:
            gap = _criterion("gust_gap", spread, "kn", "very_gusty", "poor")
    relative = hour.wind.relative
    if relative is None:
        side = _criterion("wind_direction", None, "deg", None, None)
    else:
        side = _criterion(
            "wind_direction", hour.wind.from_deg, "deg", relative.replace("-", "_"), "possible" if relative == "offshore" else "good"
        )
    return [wind, gap, side]


def _boat_criteria(hour: SurfHour) -> list[ActivityCriterion]:
    return [
        _force("wind_force", _kn(hour.wind.speed_kmh), 4, 5, ("up_to_moderate", "fresh", "strong_or_more")),
        _force("gust_force", _kn(hour.wind.gust_kmh), 5, 7, ("up_to_fresh", "strong", "gale_or_more")),
        _sea(hour.waves.height_m),
    ]


@dataclass(frozen=True)
class TideContext:
    """What the shore fishing and the answer need of the tide: the extremes, the coefficients, the low waters and their coefficient."""

    extremes: list[TideExtreme]
    coefficients: list[TideCoefficient]
    low_waters: list[int]
    coefficient_of_low: dict[int, Optional[int]]


def _fishing_criteria(hour: SurfHour, context: TideContext) -> list[ActivityCriterion]:
    """The first criterion is the window: without the low water that follows, the hour has no verdict (the wind alone says nothing)."""
    middle = hour.time_utc + HOUR // 2
    following = next((low for low in context.low_waters if low >= middle), None)
    if following is None:
        window = _criterion("low_water", None, "min", None, None)
        coefficient = _criterion("coefficient", None, "", None, None)
    else:
        inside = following - middle <= FISHING_WINDOW_S
        window = _criterion(
            "low_water", (following - middle) / 60.0, "min", "in_window" if inside else "outside_window", "good" if inside else "poor"
        )
        value = context.coefficient_of_low.get(following)
        if value is None:
            coefficient = _criterion("coefficient", None, "", None, None)
        elif value < COEFFICIENT_SMALL_BELOW:
            coefficient = _criterion("coefficient", value, "", "small", "poor")
        elif value < COEFFICIENT_GOOD_FROM:
            coefficient = _criterion("coefficient", value, "", "moderate", "possible")
        elif value < COEFFICIENT_BIG_FROM:
            coefficient = _criterion("coefficient", value, "", "large", "good")
        else:
            coefficient = _criterion("coefficient", value, "", "very_large", "possible")
    return [window, coefficient, _force("wind_force", _kn(hour.wind.speed_kmh), 5, 6, ("up_to_fresh", "strong", "near_gale_or_more")), _sea(hour.waves.height_m)]


# ------------------------------------------------------------------------------------------------------------ warnings and tide


def alert_end(alert: ActivityAlert) -> Optional[int]:
    """When a warning ends: the end the issuer gives, else its expiry; null when it gives neither."""
    return alert.ends_utc if alert.ends_utc is not None else alert.expires_utc


def _alert_active(alert: ActivityAlert, hour_start: int) -> bool:
    return bool(alerts_in_window([alert], hour_start, 1))


def alerts_in_window(alerts: list[ActivityAlert], start_utc: int, hours: int = HEADLINE_HOURS) -> list[ActivityAlert]:
    """The warnings that stand at some moment of the ``hours`` hours from ``start_utc``: begun before the window's end, not over before its start."""
    end_of_window = start_utc + hours * HOUR
    kept = []
    for alert in alerts:
        end = alert_end(alert)
        if (alert.onset_utc is None or alert.onset_utc < end_of_window) and (end is None or end > start_utc):
            kept.append(alert)
    return kept


def _cap_at(alerts: list[ActivityAlert], hour_start: int) -> Optional[str]:
    levels = {a.level for a in alerts if _alert_active(a, hour_start)}
    if SeverityLevel.RED.value in levels:
        return "red"
    if SeverityLevel.ORANGE.value in levels:
        return "orange"
    return None


def _capped(activity: str, verdict: Optional[Verdict], reason: Optional[str], cap: Optional[str]) -> tuple[Optional[Verdict], Optional[str]]:
    if cap == "red":
        return "poor", "vigilance.red"
    if cap == "orange" and activity in ("boat", "shore_fishing") and verdict == "good":
        return "possible", "vigilance.orange"
    return verdict, reason


def coastal_alerts(answer: VigilancesResponse) -> list[ActivityAlert]:
    """The coastal warnings (not cancelled) of the answer for a point, in short."""
    return [
        ActivityAlert(
            id=v.id,
            source=v.source,
            attribution=v.attribution,
            level=None if v.level is None else v.level.value,
            onset_utc=v.onset_utc if v.onset_utc is not None else v.effective_utc,
            expires_utc=v.expires_utc,
            ends_utc=v.ends_utc,
        )
        for v in answer.vigilances
        if v.phenomenon == VigilancePhenomenon.COASTAL and v.status != VigilanceStatus.CANCEL
    ]


def warnings_read(answer: VigilancesResponse) -> bool:
    """True when a source answered for the spot (fresh, or its last answer): an empty list then means no warning. Otherwise it means nothing."""
    return any(c.status in ("ok", "stale") for c in answer.coverage)


def tide_context(spot: SurfSpot, first_hour: int, days: int, now_unix: float) -> Optional[TideContext]:
    """The high and low waters from 12 hours before ``first_hour`` to 12 hours after the last hour, and the coefficient of each low water.

    ``None`` where the tide model has no cell near the spot or does not reach these dates."""
    try:
        response = tides.tides_at(spot.lat, spot.lon, first_hour - TIDE_LEAD_S, days + 1.0, 720, now_unix)
    except (tides.NoTideHere, ValueError):
        return None
    lows = sorted(e.time_utc for e in response.extremes if e.kind == "low")
    coefficient_of: dict[int, Optional[int]] = {}
    for low in lows:
        near = [
            (abs(c.brest_high_water_utc - low), c.value) for c in response.coefficients_estimated if abs(c.brest_high_water_utc - low) <= BREST_LAG_S
        ]
        coefficient_of[low] = min(near)[1] if near else None
    return TideContext(list(response.extremes), list(response.coefficients_estimated), lows, coefficient_of)


# ------------------------------------------------------------------------------------------------------------------ the answer


@dataclass
class _Judged:
    hour: ActivityHour
    criteria: list[ActivityCriterion]
    points: Optional[int] = None
    out_of: Optional[int] = None


def _headline(judged: list[_Judged], now_hour: int) -> tuple[Optional[Verdict], Optional[ActivitySlot], Optional[_Judged]]:
    """The best verdict of the daylight hours of the next 24 hours, its longest run of hours (none when it is poor) and the hour to detail."""
    window = [j for j in judged if now_hour <= j.hour.time_utc < now_hour + HEADLINE_HOURS * HOUR]
    candidates = [j for j in window if j.hour.daylight is True and j.hour.verdict is not None]
    if not candidates:
        return None, None, None
    best_level = max(_LEVEL[j.hour.verdict] for j in candidates if j.hour.verdict is not None)
    best: Verdict = next(v for v, level in _LEVEL.items() if level == best_level)  # type: ignore[assignment]
    if best == "poor":
        return best, None, candidates[0]
    runs: list[list[_Judged]] = []
    run: list[_Judged] = []
    for j in window:
        at_best = j.hour.daylight is True and j.hour.verdict == best
        if at_best and run and j.hour.time_utc - run[-1].hour.time_utc == HOUR:
            run.append(j)
            continue
        if run:
            runs.append(run)
        run = [j] if at_best else []
    if run:
        runs.append(run)
    longest = max(runs, key=lambda r: (len(r), -r[0].hour.time_utc))
    return best, ActivitySlot(start_utc=longest[0].hour.time_utc, end_utc=longest[-1].hour.time_utc + HOUR), longest[0]


def _activity(activity_id: str, judged: list[_Judged], now_hour: int) -> Activity:
    verdict, slot, detail_hour = _headline(judged, now_hour)
    detail = None
    if detail_hour is not None:
        h = detail_hour.hour
        detail = ActivityDetail(
            time_utc=h.time_utc,
            verdict=h.verdict,
            reason=h.reason,
            criteria=detail_hour.criteria,
            points=detail_hour.points,
            out_of=detail_hour.out_of,
        )
    return Activity(
        id=activity_id,  # type: ignore[arg-type]
        available=True,
        verdict=verdict,
        reason=None if detail is None else detail.reason,
        best_slot=slot,
        hours=[j.hour for j in judged],
        detail=detail,
    )


def _suns(wind: Optional[dict[str, Any]], first_hour: int, days: int) -> list[ActivitySun]:
    """Sunrise and sunset of each day from the first hour, as the wind answer gives them; empty without that answer."""
    if wind is None:
        return []
    pairs = zip(wind.get("sunrise", []), wind.get("sunset", []), strict=False)
    kept = [ActivitySun(sunrise_utc=rise, sunset_utc=sets) for rise, sets in pairs if sets is None or sets > first_hour]
    return kept[: days + 1]


def _judge(name: str, hour: SurfHour, wind_range: WindRange, context: Optional[TideContext]) -> Optional[_Judged]:
    """One hour of one activity, before the warnings; ``None`` for the shore fishing where there is no tide."""
    if name == "surf":
        verdict, reason, criteria, points, out_of = _judge_surf(hour)
        return _Judged(ActivityHour(time_utc=hour.time_utc, daylight=hour.daylight, verdict=verdict, reason=reason), criteria, points, out_of)
    if name == "kite":
        criteria = _kite_criteria(hour, wind_range.min_kn, wind_range.max_kn)
    elif name == "boat":
        criteria = _boat_criteria(hour)
    elif context is None:
        return None
    else:
        criteria = _fishing_criteria(hour, context)
    verdict, reason = _worst(criteria)
    if any(c.verdict is None for c in criteria if c.name in REQUIRED[name]):
        verdict, reason = None, None
    return _Judged(ActivityHour(time_utc=hour.time_utc, daylight=hour.daylight, verdict=verdict, reason=reason), criteria)


def build(
    forecast: SurfForecast,
    context: Optional[TideContext],
    alerts: list[ActivityAlert],
    alerts_known: bool,
    sun: list[ActivitySun],
    wind_range: WindRange,
    now_unix: float,
) -> ActivitiesForecast:
    """The activities forecast from the surf forecast of the spot, the tide, the warnings and the sun (pure: no request, no clock)."""
    now_hour = int(now_unix // HOUR) * HOUR
    judged: dict[str, list[_Judged]] = {name: [] for name in ACTIVITY_ORDER}
    for hour in forecast.hours:
        cap = _cap_at(alerts, hour.time_utc)
        for name in ACTIVITY_ORDER:
            one = _judge(name, hour, wind_range, context)
            if one is None:
                continue
            one.hour.verdict, one.hour.reason = _capped(name, one.hour.verdict, one.hour.reason, cap)
            judged[name].append(one)
    activities = []
    for name in ACTIVITY_ORDER:
        if name == "shore_fishing" and context is None:
            activities.append(Activity(id=name, available=False, note=_NO_TIDE_NOTE))
        else:
            activities.append(_activity(name, judged[name], now_hour))
    activity_tide = None
    if context is not None:
        first = forecast.hours[0].time_utc if forecast.hours else now_hour
        activity_tide = ActivityTide(
            extremes=[e for e in context.extremes if e.time_utc >= first - 6 * HOUR],
            coefficients_estimated=[c for c in context.coefficients if c.brest_high_water_utc >= first - 6 * HOUR],
            note=f"{NOT_FOR_NAVIGATION} {COEFFICIENT_NOTE}",
        )
    return ActivitiesForecast(
        generated_utc=int(now_unix),
        spot=forecast.spot,
        days=forecast.days,
        wind_range=wind_range,
        activities=activities,
        tide=activity_tide,
        sun=sun,
        vigilance=ActivityVigilance(status="ok" if alerts_known else "unknown", alerts=alerts),
        rules=RULES,
        derived=DERIVED,
        stale=forecast.stale,
        attribution=credit_text(*SURF),
    )


async def forecast(
    collector: SurfForecastCollector,
    vigilances: Any,
    spot_id: str,
    days: int,
    now_unix: float,
    wind_min_kn: float = DEFAULT_WIND_MIN_KN,
    wind_max_kn: float = DEFAULT_WIND_MAX_KN,
) -> ActivitiesForecast:
    """The activities of a spot. ``surf.UnknownSpot`` for an id that is not in the list; ``surf.NoSurfForecast`` when the swell cannot be had.

    ``vigilances`` is the ``VigilanceService``. The wind range is the kite's: ``0 < wind_min_kn < wind_max_kn``, else ``ValueError``."""
    if not 0 < wind_min_kn < wind_max_kn <= MAX_WIND_KN:
        raise ValueError(f"the wind range needs 0 < wind_min_kn < wind_max_kn <= {MAX_WIND_KN:g}")
    spot = surf.find_spot(spot_id)
    swell_data, wind_data, stale = await surf.fetch(collector, spot)
    base = await clock.run_blocking(surf.build_forecast, spot, swell_data, wind_data, days, now_unix, stale)
    alerts: list[ActivityAlert] = []
    known = False
    try:
        answer = await vigilances.for_point(spot.lat, spot.lon)
        alerts, known = coastal_alerts(answer), warnings_read(answer)
    except Exception:  # noqa: BLE001 - a failing source never fails the answer; the status says the warnings are unknown
        _log.warning("coastal warnings unreadable for spot %s", spot.id, exc_info=True)
    first = base.hours[0].time_utc if base.hours else int(now_unix // HOUR) * HOUR
    context = await clock.run_blocking(tide_context, spot, first, days, now_unix)
    sun = _suns(wind_data, first, days)
    return await clock.run_blocking(build, base, context, alerts, known, sun, WindRange(min_kn=wind_min_kn, max_kn=wind_max_kn), now_unix)
