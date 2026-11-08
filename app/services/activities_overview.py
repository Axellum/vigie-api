"""Every surf spot's activities in short (``GET /v1/activities/overview``; ADR 0016): one request for a list or a ranking.

Same verdicts as ``GET /v1/activities/{spot_id}``, worked out by the same code (``activities.build``), cut to the headline of each
activity: the best verdict of the daylight hours of the next 24 hours, why it is not ``good``, its best slot, and the verdict of the
current hour. No request is added to a provider: the swell and the wind of all the spots are the two every-spot answers that
``GET /v1/surf/overview`` reads and keeps (same cache entries), the tide is computed here, and the official coastal warnings are the
cached ones of ``GET /v1/vigilances``, asked once per spot and kept five minutes.

A spot the wave model gave nothing for is left out, never filled in. A spot whose warnings could not be read says so (``warnings:
unknown``) and nothing is capped there.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app import clock
from app.attribution import SURF, credit_text
from app.collectors.surf import SurfForecastCollector
from app.models.activities import (
    ActivitiesOverview,
    ActivityAlert,
    ActivityOverview,
    SpotActivities,
    WindRange,
)
from app.models.schemas import SeverityLevel
from app.models.surf import SurfSpot
from app.services import activities, surf

_log = logging.getLogger("vigie.activities.overview")

HOUR = 3600
HEADLINE_HOURS = activities.HEADLINE_HOURS
WARNINGS_TTL_S = 300.0  # the answers cache says five minutes (ACTIVITIES_CACHE): a warning can come in
WARNINGS_PARALLEL = 6  # how many spots ask the warnings at once; the sources are cached, this only keeps the first call from piling up
_LEVELS = (SeverityLevel.RED.value, SeverityLevel.ORANGE.value)  # the levels that change a verdict, the highest first

# Warnings read per spot: (read at, alerts). Only an answer that could be read is kept: a failure is asked again on the next call.
_warnings_memo: dict[str, tuple[float, list[ActivityAlert]]] = {}
# The last overview worked out: what it was made from, and the overview.
_memo: Optional[tuple[object, object, int, int, float, float, ActivitiesOverview]] = None


def _coastal_level(alerts: list[ActivityAlert], first_hour: int) -> Optional[str]:
    """The highest of ORANGE and RED among the warnings that stand at some moment of the 24 hours from ``first_hour``."""
    end_of_window = first_hour + HEADLINE_HOURS * HOUR
    standing = set()
    for alert in alerts:
        end = alert.ends_utc if alert.ends_utc is not None else alert.expires_utc
        if (alert.onset_utc is None or alert.onset_utc < end_of_window) and (end is None or end > first_hour):
            standing.add(alert.level)
    return next((level for level in _LEVELS if level in standing), None)


def summarize(
    spot: SurfSpot,
    swell: dict[str, Any],
    wind: Optional[dict[str, Any]],
    alerts: list[ActivityAlert],
    warnings_known: bool,
    wind_range: WindRange,
    now_unix: float,
    stale: bool = False,
) -> SpotActivities:
    """One spot in short, from its parsed swell and wind: the forecast of ``GET /v1/activities/{spot_id}`` cut to its headlines."""
    base = surf.build_forecast(spot, swell, wind, 1, now_unix, stale)
    now_hour = int(now_unix // HOUR) * HOUR
    first = base.hours[0].time_utc if base.hours else now_hour
    context = activities.tide_context(spot, first, 1, now_unix)
    full = activities.build(base, context, alerts, warnings_known, [], wind_range, now_unix)
    listed = []
    for a in full.activities:
        current = next((h.verdict for h in a.hours if h.time_utc == now_hour), None)
        listed.append(
            ActivityOverview(id=a.id, available=a.available, verdict=a.verdict, reason=a.reason, best_slot=a.best_slot, now=current)
        )
    return SpotActivities(
        id=spot.id,
        name=spot.name,
        place=spot.place,
        lat=spot.lat,
        lon=spot.lon,
        activities=listed,
        warnings="ok" if warnings_known else "unknown",
        coastal_level=_coastal_level(alerts, now_hour),
    )


def build(
    swell: dict[str, dict[str, Any]],
    wind: Optional[dict[str, dict[str, Any]]],
    warnings: dict[str, tuple[list[ActivityAlert], bool]],
    wind_range: WindRange,
    now_unix: float,
    stale: bool = False,
) -> ActivitiesOverview:
    """Every spot in short from the parsed every-spot answers (pure: no request, no clock). ``warnings``: by spot id, the alerts and
    whether they could be read; a spot with no entry has its warnings unknown."""
    listed: list[SpotActivities] = []
    for raw in surf.spot_file()["spots"]:
        if raw["id"] not in swell:
            continue
        spot = surf.find_spot(raw["id"])
        alerts, known = warnings.get(spot.id, ([], False))
        try:
            listed.append(summarize(spot, swell[spot.id], (wind or {}).get(spot.id), alerts, known, wind_range, now_unix, stale))
        except ValueError:
            _log.warning("activities overview: spot %s left out, its forecast could not be built", spot.id, exc_info=True)
    return ActivitiesOverview(
        generated_utc=int(now_unix),
        count=len(listed),
        wind_range=wind_range,
        spots=listed,
        rules=activities.RULES,
        derived=[
            "spots.activities",
            "spots.activities.verdict",
            "spots.activities.best_slot",
            "spots.activities.now",
            "spots.coastal_level",
        ],
        stale=stale,
        attribution=credit_text(*SURF),
    )


async def _warnings_of(vigilances: Any, spot_id: str, lat: float, lon: float, gate: asyncio.Semaphore) -> tuple[list[ActivityAlert], bool]:
    kept = _warnings_memo.get(spot_id)
    if kept is not None and clock.monotonic() - kept[0] < WARNINGS_TTL_S:
        return kept[1], True
    async with gate:
        try:
            answer = await vigilances.for_point(lat, lon)
        except Exception:  # noqa: BLE001 - a failing source never fails the answer; the spot says its warnings are unknown
            _log.warning("coastal warnings unreadable for spot %s", spot_id, exc_info=True)
            return [], False
    alerts, known = activities.coastal_alerts(answer), activities.warnings_read(answer)
    if known:
        _warnings_memo[spot_id] = (clock.monotonic(), alerts)
    return alerts, known


async def overview(
    collector: SurfForecastCollector,
    vigilances: Any,
    now_unix: float,
    wind_min_kn: float = activities.DEFAULT_WIND_MIN_KN,
    wind_max_kn: float = activities.DEFAULT_WIND_MAX_KN,
) -> ActivitiesOverview:
    """Every spot in short. ``surf.NoSurfForecast`` when the swell of no spot can be had; ``ValueError`` when the wind range is not one."""
    global _memo
    if not 0 < wind_min_kn < wind_max_kn <= activities.MAX_WIND_KN:
        raise ValueError(f"the wind range needs 0 < wind_min_kn < wind_max_kn <= {activities.MAX_WIND_KN:g}")
    points = tuple((raw["id"], raw["lat"], raw["lon"]) for raw in surf.spot_file()["spots"])
    swell, wind = await asyncio.gather(collector.fetch_all("swell", points), collector.fetch_all("wind", points), return_exceptions=True)
    if isinstance(swell, BaseException):
        raise surf.NoSurfForecast(f"the wave model gave no answer for the spots ({type(swell).__name__})")
    swell_data, swell_stale = swell
    wind_data: Optional[dict[str, dict[str, Any]]] = None
    wind_stale = False
    if not isinstance(wind, BaseException):
        wind_data, wind_stale = wind
    hour = int(now_unix // HOUR)
    bucket = int(now_unix // WARNINGS_TTL_S)
    memo = _memo
    if (
        memo is not None
        and memo[0] is swell_data
        and memo[1] is wind_data
        and memo[2] == hour
        and memo[3] == bucket
        and (memo[4], memo[5]) == (wind_min_kn, wind_max_kn)
    ):
        return memo[6]
    gate = asyncio.Semaphore(WARNINGS_PARALLEL)
    asked = [(spot_id, lat, lon) for spot_id, lat, lon in points if spot_id in swell_data]
    answers = await asyncio.gather(*(_warnings_of(vigilances, spot_id, lat, lon, gate) for spot_id, lat, lon in asked))
    warnings = dict(zip((spot_id for spot_id, _, _ in asked), answers, strict=True))
    # 51 spots of rating and tide: a fraction of a second of numpy, in a worker thread, as the surf overview.
    built = await clock.run_blocking(
        build, swell_data, wind_data, warnings, WindRange(min_kn=wind_min_kn, max_kn=wind_max_kn), now_unix, swell_stale or wind_stale
    )
    _memo = (swell_data, wind_data, hour, bucket, wind_min_kn, wind_max_kn, built)
    return built
