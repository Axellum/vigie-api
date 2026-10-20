"""Compact text frame for embedded displays (``GET /v1/embedded/frame``).

The contract of this frame is documented in ``docs/embedded-frame.md``, which is its single
source of truth; ``tests/test_embedded_frame_contract.py`` pins it. Summary:

- Line oriented, fields separated by ``|``: ``HEAD|``, ``ZONE|``, ``RAIN5M|``, ``DAY|``,
  ``HAZ|``, then ``END|<CRC32>``. Lines end with ``\\n``.
- A value the providers did not supply is an **empty field**, never a made-up number.
- Decodable in one pass by the ESP32-P4 firmware with no dynamic allocation.
- Integrity: CRC32 (IEEE 802.3, 8 uppercase hex digits) over every byte before the ``END|`` line.
"""

from __future__ import annotations

import zlib
from typing import Optional

from app.models.schemas import (
    CurrentWeather,
    DailyForecast,
    MarineConditions,
    PointResponse,
    SnapshotResponse,
)

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


def serialize_embedded_frame(
    snapshot: SnapshotResponse,
    point: Optional[PointResponse] = None,
) -> str:
    """Build the compact frame and sign it with ``END|<CRC32>``."""
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
