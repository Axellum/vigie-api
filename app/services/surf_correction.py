"""The height of the sea of a surf forecast, multiplied by a factor found against a wave buoy, at eight spots and only if asked (ADR 0023).

MFWAM reads about 10 % under the CANDHIS buoys (ADR 0022). At the spots whose factor met the criterion written before the measure (held out, both
directions, at 1, 3 and 5 days), ``hours[].waves.height_m`` can be multiplied by that spot's factor. The constants are ``app/data/surf-calibration.json``
(written by ``tools/measure_surf_error.py --write-calibration``); nothing is asked of CANDHIS at run time.

- One field is corrected, the one that was measured; the model's value is kept beside it (``waves_model_height_m``) and the fields that were not scored
  against the buoy are named in the answer (``LEFT_AS_MODELLED``).
- The factor is applied only on the model cell it was found on: another cell, or no cell, gives the model's height and says why.
- It brings the forecast closer to the buoy named in the answer, which can be 25 km away; the answer says so (``NOTE``).
- Off unless the request says ``correct=true``, or the instance sets ``VIGIE_SURF_CORRECTION=on`` (then ``correct=false`` asks for the model alone).
  The switch concerns ``GET /v1/surf/spots/{id}`` only: the overview, the activity verdicts and the frame never use it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from app.attribution import credit_text
from app.models.surf import SurfCorrection, SurfCorrectionBuoy

CALIBRATION_FILE = Path(__file__).resolve().parents[1] / "data" / "surf-calibration.json"
DEFAULT_ON_BY = "VIGIE_SURF_CORRECTION"  # "on": an instance applies the correction unless a request says `correct=false`
CELL_TOLERANCE_DEG = 0.005  # the cell the provider names is compared with the one of the measure (values such as 43.458336 and 43.458334 are the same cell)
MEASURE_DOC = "docs/measurements/surf-error-2026-11.md"

CORRECTED_FIELD = "hours.waves.height_m"
# Not scored against the buoys (ADR 0022 described the period and the direction only and did not compare the swell partitions): left as the model gives them.
LEFT_AS_MODELLED = [
    "hours.swell",
    "hours.secondary_swell",
    "hours.wind_wave_height_m",
    "hours.waves.period_s",
    "hours.waves.from_deg",
    "hours.swell_power_kw_m",
    "hours.rating",
    "best_slot",
]
NOTE = (
    "The factor was found by comparing the model's height of the sea with the wave buoy named here, over the window given. It brings the forecast closer to that "
    "buoy, which can be far from the spot and does not see the sandbanks, the reefs or the shelter of the beach; the swell, the periods, the rating and the "
    "best slot were not measured against the buoy and are the model's."
)


@dataclass(frozen=True)
class SpotFactor:
    """The factor of one surf spot and what it was found on."""

    spot_id: str
    buoy_id: str
    buoy_name: str
    distance_km: float
    cell: tuple[float, float]
    factor: float
    held_out_gain: Optional[dict[str, float]]


@dataclass(frozen=True)
class Plan:
    """What to do to a forecast: the factor to multiply the height of the sea by (``None``: nothing) and the report of it."""

    factor: Optional[float]
    report: SurfCorrection


@lru_cache(maxsize=1)
def _file() -> dict:
    return json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def factors() -> dict[str, SpotFactor]:
    """The spots whose factor is applied (``applied: true``), by spot id."""
    out: dict[str, SpotFactor] = {}
    for entry in _file()["pairs"]:
        if not entry["applied"] or entry["factor"] is None:
            continue
        out[entry["spot_id"]] = SpotFactor(
            spot_id=entry["spot_id"],
            buoy_id=entry["buoy_id"],
            buoy_name=entry["buoy_name"],
            distance_km=entry["distance_km"],
            cell=(float(entry["cell"][0]), float(entry["cell"][1])),
            factor=float(entry["factor"]),
            held_out_gain=entry.get("held_out_gain"),
        )
    return out


def window() -> list[str]:
    return list(_file()["window"])


def on_by_default() -> bool:
    """Whether an instance applies the correction when a request does not say (it does not, unless ``VIGIE_SURF_CORRECTION=on``)."""
    return os.environ.get(DEFAULT_ON_BY, "").strip().lower() == "on"


def _not_applied(reason: Literal["not_asked", "not_calibrated", "other_cell", "cell_unknown"]) -> Plan:
    return Plan(None, SurfCorrection(applied=False, reason=reason))


NOT_ASKED = _not_applied("not_asked")


def _same_cell(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) <= CELL_TOLERANCE_DEG and abs(a[1] - b[1]) <= CELL_TOLERANCE_DEG


def plan(spot_id: str, cell: Optional[tuple[float, float]], asked: bool) -> Plan:
    """The correction of the forecast of ``spot_id``, which the model answered from ``cell`` (lat, lon; ``None`` when it did not say)."""
    if not asked:
        return NOT_ASKED
    found = factors().get(spot_id)
    if found is None:
        return _not_applied("not_calibrated")
    if cell is None:
        return _not_applied("cell_unknown")
    if not _same_cell(cell, found.cell):
        return _not_applied("other_cell")
    return Plan(
        found.factor,
        SurfCorrection(
            applied=True,
            field=CORRECTED_FIELD,
            factor=found.factor,
            buoy=SurfCorrectionBuoy(id=found.buoy_id, name=found.buoy_name, distance_km=found.distance_km),
            window=window(),
            held_out_gain=found.held_out_gain,
            left_as_modelled=list(LEFT_AS_MODELLED),
            note=NOTE,
            measure=MEASURE_DOC,
            attribution=credit_text("surf_height_factors"),
        ),
    )


__all__ = ["CALIBRATION_FILE", "NOT_ASKED", "Plan", "SpotFactor", "factors", "on_by_default", "plan", "window"]
