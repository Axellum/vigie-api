"""From daily quantities to a level per province: 0 none, 1 "watch", 2 "strong", 3 "severe" (our words, never the MGM's colours).

A province's value for a phenomenon is a high quantile of its grid points (``PROVINCE_QUANTILE``), not their maximum: one point of
a 28 km grid does not make a province. For the cold the low quantile of the minimum temperature is taken instead.

The thresholds (``THRESHOLDS``) are one national table per phenomenon. ADR 0011 says how they are chosen: on the learning set only
(the press episodes of 2024 and 2025 and the forecasts of those days), and frozen when the shadow phase starts. Whatever value is in
the table, it is a setting of this pilot, not a fact about the weather of Turkey, and not the MGM's (which are not published).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np

from app.prealert.gfs import DayValues
from app.prealert.provinces import Province

Phenomenon = Literal["rain", "wind", "snow", "heat", "cold"]
PHENOMENA: tuple[Phenomenon, ...] = ("rain", "wind", "snow", "heat", "cold")
LEVEL_WORDS = {0: "none", 1: "watch", 2: "strong", 3: "severe"}

PROVINCE_QUANTILE = 0.9

# A day the run covers for less than this many hours gives no rain or snow level (the first day of a 12 UTC run, for one).
MIN_HOURS_FOR_TOTALS = 18


@dataclass(frozen=True)
class Threshold:
    """The values a province's quantity must reach for levels 1, 2 and 3 (``above``), or fall to (``below``)."""

    quantity: str  # what is compared, with its unit
    levels: tuple[float, float, float]
    direction: Literal["above", "below"] = "above"

    def level_of(self, value: Optional[float]) -> Optional[int]:
        if value is None or np.isnan(value):
            return None
        level = 0
        for rank, limit in enumerate(self.levels, start=1):
            if (value >= limit) if self.direction == "above" else (value <= limit):
                level = rank
        return level


# PROVISIONAL: to be replaced by the values the backtest chooses on the learning set (tools/backtest_prealert_tr.py).
THRESHOLDS: dict[Phenomenon, Threshold] = {
    "rain": Threshold("rain in 24 h, mm", (20.0, 50.0, 100.0)),
    "wind": Threshold("strongest gust, km/h", (65.0, 90.0, 115.0)),
    "snow": Threshold("water fallen as snow in 24 h, mm", (5.0, 15.0, 30.0)),
    "heat": Threshold("maximum temperature, degC", (38.0, 41.0, 44.0)),
    "cold": Threshold("minimum temperature, degC", (-15.0, -20.0, -25.0), "below"),
}


def _quantile(values: np.ndarray, province: Province, q: float) -> Optional[float]:
    points = values[np.asarray(province.rows), np.asarray(province.cols)]
    points = points[~np.isnan(points)]
    if points.size == 0:
        return None
    return float(np.quantile(points, q))


def province_quantities(day: DayValues, province: Province) -> dict[Phenomenon, Optional[float]]:
    """The value of each phenomenon for one province and day, in the unit of its threshold; ``None`` when the run lacks it."""
    totals_ok = day.hours >= MIN_HOURS_FOR_TOTALS
    q = PROVINCE_QUANTILE
    rain = _quantile(day.rain_mm, province, q) if (totals_ok and day.rain_mm is not None) else None
    snow = _quantile(day.snow_mm, province, q) if (totals_ok and day.snow_mm is not None) else None
    gust = _quantile(day.gust_ms, province, q) if day.gust_ms is not None else None
    tmax = _quantile(day.tmax_k, province, q) if day.tmax_k is not None else None
    tmin = _quantile(day.tmin_k, province, 1.0 - q) if day.tmin_k is not None else None
    return {
        "rain": rain,
        "wind": None if gust is None else gust * 3.6,
        "snow": snow,
        "heat": None if tmax is None else tmax - 273.15,
        "cold": None if tmin is None else tmin - 273.15,
    }


def province_levels(
    quantities: dict[Phenomenon, Optional[float]], thresholds: dict[Phenomenon, Threshold] = THRESHOLDS
) -> dict[Phenomenon, Optional[int]]:
    """The level of each phenomenon; ``None`` when its quantity is unknown."""
    return {name: thresholds[name].level_of(quantities.get(name)) for name in PHENOMENA}
