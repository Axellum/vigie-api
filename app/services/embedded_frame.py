"""Compact text frame for embedded displays (``GET /v1/embedded/frame``).

The contract of this frame is documented in ``docs/embedded-frame.md``, which is its single
source of truth; ``tests/test_embedded_frame_contract.py`` pins it. Summary:

- Line oriented, fields separated by ``|``: ``HEAD|``, ``ZONE|``, ``RAIN5M|``, ``DAY|``,
  ``HAZ|``, then, when a surf spot is asked for, ``SEA|``, ``ACT|`` and ``CRIT|`` (the activities of
  that spot), then ``END|<CRC32>``. Lines end with ``\\n``.
- A value the providers did not supply is an **empty field**, never a made-up number.
- Decodable in one pass by the ESP32-P4 firmware with no dynamic allocation.
- Integrity: CRC32 (IEEE 802.3, 8 uppercase hex digits) over every byte before the ``END|`` line.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable
from typing import Optional

from app.models.activities import ActivitiesForecast, Activity, ActivityAlert, ActivityHour
from app.models.schemas import (
    CurrentWeather,
    DailyForecast,
    MarineConditions,
    PointResponse,
    SnapshotResponse,
)
from app.services import activities as activities_service

# Columns of a ZONE line that depend on the weather: temp_c ... current_kt (indexes 6 to 23).
_ZONE_WEATHER_COLUMNS = 18


def _clean_field(text: str, max_len: int = 64) -> str:
    """Clean a string so that it holds no ``|``, no line break and fits ``max_len`` characters."""
    cleaned = text.replace("|", "/").replace("\r", " ").replace("\n", " ").strip()
    return cleaned[:max_len]


def _f(value: Optional[float], digits: int) -> str:
    """Format a float with ``digits`` decimals, or an empty field when unknown."""
    return "" if value is None else f"{value:.{digits}f}"


def _i(value: Optional[float]) -> str:
    """Format an integer, or an empty field when unknown."""
    return "" if value is None else str(int(value))


def _flag(value: Optional[bool]) -> str:
    """Format a boolean as ``1`` / ``0``, or an empty field when unknown."""
    return "" if value is None else ("1" if value else "0")


def _rain_15m(values: Optional[list[float]]) -> Optional[list[float]]:
    """Return exactly four 15-minute precipitation values (mm), or ``None`` when the nowcast is unknown."""
    if values is None or len(values) != 4:
        return None
    return [max(0.0, float(v)) for v in values]


def _rain_eta_and_max_mmh(c15: list[float]) -> tuple[int, float]:
    """Return the delay before rain in minutes (-1 when dry for the next hour) and the peak mm/h."""
    eta_min = -1
    for idx, val in enumerate(c15):
        if val > 0.05:
            eta_min = idx * 15
            break
    return eta_min, round(max(c15) * 4.0, 1)


def _rain_5m_from_15m(c15: list[float]) -> list[float]:
    """Resample four 15-minute amounts to twelve 5-minute steps expressed in mm/h.

    Each 15-minute amount becomes one rate (amount x 4) repeated for its three 5-minute steps.
    The API has no real 5-minute data, so nothing is interpolated or extrapolated.
    """
    return [round(v * 4.0, 2) for v in c15 for _ in range(3)]


def _emit_zone_block(
    lines: list[str],
    zone_id: str,
    zone_name: str,
    lat: float,
    lon: float,
    utc_offset_s: Optional[int],
    current: Optional[CurrentWeather],
    rain_next_60m: Optional[list[float]],
    daily: list[DailyForecast],
    marine: Optional[MarineConditions] = None,
) -> None:
    """Emit the ``ZONE|``, ``RAIN5M|`` and ``DAY|`` lines of one zone."""
    zid = _clean_field(zone_id, 12)
    zname = _clean_field(zone_name, 32)
    c15 = _rain_15m(rain_next_60m) if current is not None else None

    if current is None:
        weather_cols = [""] * _ZONE_WEATHER_COLUMNS
    else:
        if c15 is None:
            eta_col = max_col = c15_col = ""
        else:
            eta_min, max_mmh = _rain_eta_and_max_mmh(c15)
            eta_col, max_col = str(eta_min), f"{max_mmh:.1f}"
            c15_col = ",".join(f"{v:.2f}" for v in c15)
        current_kt = (
            None
            if marine is None or marine.ocean_current_velocity_kmh is None
            else round(marine.ocean_current_velocity_kmh / 1.852, 2)
        )
        weather_cols = [
            _f(current.temp_c, 1),
            _f(current.feels_like_c, 1),
            _i(current.humidity_pct),
            _f(current.pressure_hpa, 1),
            _f(current.wind_kmh, 1),
            _f(current.gust_kmh, 1),
            _i(current.wind_deg),
            _i(current.weather_code),
            _f(current.uv_index, 1),
            _i(current.aqi_eu),
            eta_col,
            max_col,
            c15_col,
            _f(marine.wave_height_m if marine else None, 2),
            _f(marine.wave_period_s if marine else None, 1),
            _i(marine.wave_direction_deg if marine else None),
            _f(marine.swell_wave_height_m if marine else None, 2),
            _f(current_kt, 2),
        ]

    # ZONE line: the tag + 23 columns
    lines.append(
        "|".join(
            ["ZONE", zid, zname, f"{lat:.4f}", f"{lon:.4f}", _i(utc_offset_s), *weather_cols]
        )
    )

    # RAIN5M line: twelve 5-minute steps, or an empty field when the nowcast is unknown
    rain_5m_csv = "" if c15 is None else ",".join(f"{v:.2f}" for v in _rain_5m_from_15m(c15))
    lines.append(f"RAIN5M|{zid}|{rain_5m_csv}")

    # DAY lines: one per forecast day
    for day in daily:
        lines.append(
            "|".join(
                [
                    "DAY",
                    zid,
                    _clean_field(day.date, 10),
                    _i(day.code),
                    _f(day.tmin, 1),
                    _f(day.tmax, 1),
                    _f(day.rain_mm, 1),
                    _i(day.rain_probability_pct),
                    _f(day.wind_max, 1),
                    _f(day.uv_max, 1),
                ]
            )
        )


# ------------------------------------------------------------------------------------------------ activities: SEA, ACT, CRIT

ACTIVITY_HOURS = 24  # the hours of the two strips of an ACT line: the 24 hours the headline verdict looks at
TIDE_AFTER_S = 30 * 3600  # the high and low waters are sent up to this long after the first hour
MAX_TIDE_EXTREMES = 8  # with these two caps the SEA line stays under the 512 bytes the display reads a line into
MAX_COEFFICIENTS = 4
_VERDICT_CHAR = {"good": "G", "possible": "P", "poor": "X"}
_LEVEL_RANK = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}


def _first_hour(forecast: ActivitiesForecast) -> Optional[int]:
    """The start of the first hour the verdicts cover (the same for every activity), or ``None`` when there is none."""
    times = [h.time_utc for a in forecast.activities for h in a.hours]
    return min(times) if times else None


def _strip(activity: Activity, first_hour: Optional[int], char_of: Callable[[ActivityHour], str]) -> str:
    """One character per hour for ``ACTIVITY_HOURS`` hours from ``first_hour``; ``-`` where an hour is missing."""
    chars = ["-"] * ACTIVITY_HOURS
    if first_hour is not None:
        for hour in activity.hours:
            index, rest = divmod(hour.time_utc - first_hour, 3600)
            if rest == 0 and 0 <= index < ACTIVITY_HOURS:
                chars[index] = char_of(hour)
    return "".join(chars)


def _strongest_warning(alerts: list[ActivityAlert], start_utc: int) -> tuple[str, str, str]:
    """Level, end and source of the strongest coastal warning that stands in the next 24 hours (empty fields when there is none).

    The end is empty when one of the warnings at that level gives none; a warning whose level is not in the API's table is left out."""
    standing = [a for a in activities_service.alerts_in_window(alerts, start_utc) if a.level in _LEVEL_RANK]
    if not standing:
        return "", "", ""
    top = max(_LEVEL_RANK[a.level or ""] for a in standing)
    at_top = [a for a in standing if _LEVEL_RANK[a.level or ""] == top]
    ends = [activities_service.alert_end(a) for a in at_top]
    known_ends = [e for e in ends if e is not None]
    until = str(max(known_ends)) if len(known_ends) == len(ends) else ""
    return at_top[0].level or "", until, _clean_field(at_top[0].source, 24)


def _sea_line(forecast: ActivitiesForecast, first_hour: Optional[int]) -> str:
    """``SEA``: the spot, the time frame, the sun, the tide and the strongest coastal warning."""
    spot = forecast.spot
    sun_columns: list[str] = []
    for index in range(2):
        day = forecast.sun[index] if index < len(forecast.sun) else None
        sun_columns += [_i(day.sunrise_utc) if day else "", _i(day.sunset_utc) if day else ""]
    extremes = coefficients = ""
    if forecast.tide is not None and first_hour is not None:
        last = first_hour + TIDE_AFTER_S
        kept = [e for e in forecast.tide.extremes if e.time_utc <= last][:MAX_TIDE_EXTREMES]
        extremes = ",".join(f"{'H' if e.kind == 'high' else 'L'}:{e.time_utc}:{e.height_m:.2f}" for e in kept)
        estimates = [c for c in forecast.tide.coefficients_estimated if c.brest_high_water_utc <= last][:MAX_COEFFICIENTS]
        coefficients = ",".join(f"{c.brest_high_water_utc}:{c.value}" for c in estimates)
    level = until = source = ""  # warnings that could not be read say nothing: an empty level is then not "nothing to fear"
    if forecast.vigilance.status == "ok":
        level, until, source = _strongest_warning(forecast.vigilance.alerts, first_hour if first_hour is not None else forecast.generated_utc)
    return "|".join(
        [
            "SEA",
            _clean_field(spot.name, 32),
            _clean_field(spot.place or "", 32),
            str(forecast.generated_utc),
            _i(first_hour),
            _flag(forecast.stale),
            _f(forecast.wind_range.min_kn, 1),
            _f(forecast.wind_range.max_kn, 1),
            *sun_columns,
            extremes,
            coefficients,
            forecast.vigilance.status,
            level,
            until,
            source,
        ]
    )


def _emit_activity(lines: list[str], activity: Activity, first_hour: Optional[int]) -> None:
    """The ``ACT`` line of an activity, then the ``CRIT`` lines of the hour its verdict stands on."""
    if not activity.available:
        lines.append("|".join(["ACT", activity.id, "0", *[""] * 9]))
        return
    slot, detail = activity.best_slot, activity.detail
    lines.append(
        "|".join(
            [
                "ACT",
                activity.id,
                "1",
                activity.verdict or "",
                _clean_field(activity.reason or "", 48),
                "" if slot is None else str(slot.start_utc),
                "" if slot is None else str(slot.end_utc),
                _strip(activity, first_hour, lambda h: _VERDICT_CHAR.get(h.verdict or "", "-")),
                _strip(activity, first_hour, lambda h: "-" if h.daylight is None else ("1" if h.daylight else "0")),
                "" if detail is None else str(detail.time_utc),
                "" if detail is None else _i(detail.points),
                "" if detail is None else _i(detail.out_of),
            ]
        )
    )
    for criterion in [] if detail is None else detail.criteria:
        lines.append(
            "|".join(
                [
                    "CRIT",
                    activity.id,
                    _clean_field(criterion.name, 24),
                    _f(criterion.value, 1),
                    _clean_field(criterion.unit, 12),
                    _clean_field(criterion.state or "", 24),
                    criterion.verdict or "",
                    _i(criterion.points),
                    _i(criterion.max_points),
                ]
            )
        )


def activities_lines(forecast: ActivitiesForecast) -> list[str]:
    """The ``SEA`` line, then the ``ACT`` line of each activity followed by its ``CRIT`` lines."""
    first_hour = _first_hour(forecast)
    lines = [_sea_line(forecast, first_hour)]
    for activity in forecast.activities:
        _emit_activity(lines, activity, first_hour)
    return lines


def serialize_embedded_frame(
    snapshot: SnapshotResponse,
    point: Optional[PointResponse] = None,
    activities: Optional[ActivitiesForecast] = None,
) -> str:
    """Build the compact frame and sign it with ``END|<CRC32>``. ``activities``, when given, adds the ``SEA``, ``ACT`` and ``CRIT`` lines."""
    lines: list[str] = []
    gs = snapshot.global_status

    # 1. Global header (HEAD)
    # HEAD|server_utc|solar_kp|0|version|device_type|max_severity|red|orange|yellow|gps_degraded|hf_risk|simulation
    lines.append(
        "|".join(
            [
                "HEAD",
                str(snapshot.server_utc),
                _f(gs.solar_kp, 1),
                "0",
                snapshot.version,
                snapshot.device_type.value,
                gs.max_severity.value if gs.max_severity is not None else "",
                _i(gs.active_red_count),
                _i(gs.active_orange_count),
                _i(gs.active_yellow_count),
                _flag(gs.gps_degraded),
                _flag(gs.hf_radio_blackout_risk),
                "1" if snapshot.simulation_active else "0",
            ]
        )
    )

    # 2. When a GPS position is given it is the first zone (id "GPS")
    if point is not None:
        _emit_zone_block(
            lines=lines,
            zone_id="GPS",
            zone_name="GPS position",
            lat=point.coordinates.lat,
            lon=point.coordinates.lon,
            utc_offset_s=point.utc_offset_s,
            current=point.current,
            rain_next_60m=point.rain_nowcast.mm_per_step,
            daily=point.daily,
            marine=point.marine,
        )

    # 3. Watched zones (ZONE + RAIN5M + DAY)
    for z in snapshot.zones:
        _emit_zone_block(
            lines=lines,
            zone_id=z.id,
            zone_name=z.name,
            lat=z.lat,
            lon=z.lon,
            utc_offset_s=z.utc_offset_s,
            current=z.current,
            rain_next_60m=z.rain_next_60m,
            daily=z.daily_10d,
        )

    # 4. Active hazards (HAZ)
    # HAZ|id|category|severity|lat|lon|value|depth_km|tsunami_0_1|timestamp_utc|sources_plus|title
    hazards_source = point.nearby_hazards if point is not None else snapshot.hazards
    for h in hazards_source:
        sources_str = "+".join(h.sources) if h.sources else h.source
        lines.append(
            "|".join(
                [
                    "HAZ",
                    _clean_field(h.id, 28),
                    h.category.value,
                    h.severity.value,
                    f"{h.lat:.4f}",
                    f"{h.lon:.4f}",
                    _f(h.value_primary, 1),
                    _f(h.depth_km, 1),
                    "1" if h.tsunami_warning else "0",
                    str(int(h.timestamp_utc)),
                    _clean_field(sources_str, 24),
                    _clean_field(h.title, 64),
                ]
            )
        )

    # 5. The activities of a surf spot (SEA, then ACT and CRIT lines), only when asked for
    if activities is not None:
        lines.extend(activities_lines(activities))

    body_with_newline = "\n".join(lines) + "\n"
    crc_int = zlib.crc32(body_with_newline.encode("utf-8")) & 0xFFFFFFFF
    return f"{body_with_newline}END|{crc_int:08X}\n"


def verify_embedded_frame_crc(frame_text: str) -> bool:
    """Check the CRC32 of an embedded frame ``HEAD...END|<CRC32>``."""
    marker = "\nEND|"
    idx = frame_text.rfind(marker)
    if idx == -1:
        return False
    body = frame_text[: idx + 1]  # includes the '\n' just before END|
    trailer = frame_text[idx + len(marker) :].strip()
    if len(trailer) != 8:
        return False
    expected_crc = zlib.crc32(body.encode("utf-8")) & 0xFFFFFFFF
    return trailer.upper() == f"{expected_crc:08X}"
