"""The modelled tide, corrected with what the nearest REFMAR tide gauge measured just now (ADR 0017).

EOT20 ignores the weather. A tide gauge of REFMAR (SHOM, Licence Ouverte 2.0) that measured in the last hour tells how far the sea
is from the model, and that gap fades with the surge it comes from. The correction is one number and one rule:

    anomaly = mean over the last ``WINDOW_MINUTES`` of the measures of (observed - modelled - datum offset of the station)
    corrected height(t) = modelled height(t) + anomaly * exp(-(t - t_measure) / TAU_HOURS)        for t after the last measure

- The window is one cycle of M2 (12.42 h): the error of the model that comes back with the tide cancels in the mean, what stays is
  what lasts (the surge). A short window followed the tide's own error and made the forecast worse from +3 h on (measured).
- ``TAU_HOURS`` and ``WINDOW_MINUTES`` were chosen on the first half of the measure window and scored on the second
  (``tools/measure_tide_correction.py``, ``docs/measurements/tide-correction-2026-11.md``).
- The datum offset of a station (its chart datum against the mean sea level of the model) is bundled in
  ``app/data/refmar-stations.json``, worked out by the same tool.
- No station within ``MAX_DISTANCE_KM``, no fresh measure, or no way to ask: no correction, and the answer says which.

Heights before the last measure are not touched: the rule was measured forward only.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import numpy as np

from app import clock
from app.attribution import TIDE_CORRECTION, credit_text
from app.models.tides import TideCorrection, TideCorrectionStation
from app.services.geodesy import haversine_km

if TYPE_CHECKING:
    from app.collectors.refmar import RefmarCollector

from app.services.tides import (
    MINUTE,
    TideModel,
    calibrated_heights,
    calibration_for,
    mjd_from_unix,
    model,
    unix_from_mjd,
)

STATIONS_FILE = Path(__file__).resolve().parents[1] / "data" / "refmar-stations.json"

# The decay of the gap and the window it is averaged over: measured, see the module docstring.
TAU_HOURS = 12.0
WINDOW_MINUTES = 745  # one cycle of M2
# A station further than this from the point is not used. Measured: the gap of one Atlantic gauge helped another 44 to 111 km away,
# and hurt between Le Havre and Cherbourg (127 km); 50 km is inside what was seen to help.
MAX_DISTANCE_KM = 50.0
# The last measure must be this recent (REFMAR posted the latest value 3 to 20 minutes late when read on 2026-11-08), and the window must
# hold at least this share of its minutes.
MAX_AGE_MINUTES = 60
MIN_COVERAGE = 0.5
# A gap this large is a gauge fault or a wrong datum, not weather (a guard of ours, not measured).
MAX_ANOMALY_M = 1.5

DEFAULT_ON_BY = "VIGIE_TIDE_CORRECTION"  # "on": an instance applies the correction unless a request says `correct=false`


@dataclass(frozen=True)
class RefmarStation:
    """A REFMAR tide gauge and the offset of its datum against the model's mean sea level (m)."""

    id: str
    name: str
    lat: float
    lon: float
    offset_m: float


@dataclass(frozen=True)
class Anomaly:
    """The gap between the sea and the model at the last measure."""

    station: RefmarStation
    distance_km: float
    measured_mjd: float
    anomaly_m: float
    minutes: int  # how many measures the mean holds


@dataclass(frozen=True)
class Correction:
    """What ``GET /v1/tides`` knows about the correction of an answer: applied (``anomaly``) or why not (``reason``)."""

    anomaly: Optional[Anomaly]
    reason: Optional[str]
    station: Optional[RefmarStation] = None
    distance_km: Optional[float] = None

    def at(self, mjd: np.ndarray) -> np.ndarray:
        """The correction (m) to add at the times ``mjd``: zero before the last measure, then the gap fading."""
        mjd = np.asarray(mjd, dtype=np.float64)
        if self.anomaly is None:
            return np.zeros(mjd.shape)
        hours = (mjd - self.anomaly.measured_mjd) * 24.0
        return np.where(hours >= 0.0, self.anomaly.anomaly_m * np.exp(-np.maximum(hours, 0.0) / TAU_HOURS), 0.0)


NOT_ASKED = Correction(None, "not_asked")
NO_CORRECTION = NOT_ASKED


@lru_cache(maxsize=1)
def stations() -> tuple[RefmarStation, ...]:
    """The gauges that can correct a tide (``app/data/refmar-stations.json``)."""
    data = json.loads(STATIONS_FILE.read_text(encoding="utf-8"))
    return tuple(RefmarStation(s["id"], s["name"], s["lat"], s["lon"], s["offset_m"]) for s in data["stations"])


def nearest_station(lat: float, lon: float, max_km: float = MAX_DISTANCE_KM) -> Optional[tuple[RefmarStation, float]]:
    """The gauge nearest to a point, within ``max_km``, with its distance."""
    best: Optional[tuple[RefmarStation, float]] = None
    for station in stations():
        distance = haversine_km(lat, lon, station.lat, station.lon)
        if distance <= max_km and (best is None or distance < best[1]):
            best = (station, distance)
    return best


def trailing_anomaly(mjd: np.ndarray, residual: np.ndarray, now_mjd: float) -> Optional[tuple[float, float, int]]:
    """The mean of ``residual`` (observed - modelled - offset, one per measure at ``mjd``, sorted) over the last
    ``WINDOW_MINUTES`` before the last measure not after ``now_mjd``: (anomaly, time of that measure, measures in the mean).
    ``None`` when the last measure is older than ``MAX_AGE_MINUTES`` or the window holds under ``MIN_COVERAGE`` of its minutes."""
    end = int(np.searchsorted(mjd, now_mjd, side="right"))
    if end == 0:
        return None
    last = float(mjd[end - 1])
    if (now_mjd - last) / MINUTE > MAX_AGE_MINUTES:
        return None
    start = int(np.searchsorted(mjd, last - WINDOW_MINUTES * MINUTE, side="left"))
    window = residual[start:end]
    window = window[np.isfinite(window)]
    if len(window) < MIN_COVERAGE * WINDOW_MINUTES:
        return None
    return float(window.mean()), last, len(window)


def residuals(tide_model: TideModel, station: RefmarStation, observations: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    """The times (MJD) and the residuals (observed - modelled at the cell nearest to the gauge - offset) of the measures
    ``(unix time, metres above the chart datum)``, sorted by time."""
    ordered = sorted(observations)
    mjd = np.array([mjd_from_unix(moment) for moment, _ in ordered])
    values = np.array([value for _, value in ordered])
    if not len(mjd):
        return mjd, values
    cell = tide_model.nearest_cell(station.lat, station.lon)
    # The model as the API serves it at the gauge: calibrated (ADR 0019) where a calibration is applied there.
    served = calibrated_heights(tide_model, cell.constants, mjd, calibration_for(station.lat, station.lon))
    return mjd, values - served - station.offset_m


def decide(
    tide_model: TideModel, observations: Optional[list[tuple[float, float]]], station: Optional[RefmarStation],
    distance_km: Optional[float], now_unix: float,
) -> Correction:  # fmt: skip
    """The correction of a point, given the measures of its gauge (``None``: they could not be read)."""
    if station is None:
        return Correction(None, "no_station_near")
    if observations is None:
        return Correction(None, "measure_unavailable", station, distance_km)
    mjd, gap = residuals(tide_model, station, observations)
    found = trailing_anomaly(mjd, gap, mjd_from_unix(now_unix)) if len(mjd) else None
    if found is None:
        return Correction(None, "no_recent_measure", station, distance_km)
    anomaly, measured_mjd, minutes = found
    if abs(anomaly) > MAX_ANOMALY_M:
        return Correction(None, "implausible_gap", station, distance_km)
    assert distance_km is not None
    return Correction(Anomaly(station, distance_km, measured_mjd, anomaly, minutes), None, station, distance_km)


def on_by_default() -> bool:
    """Whether an instance applies the correction when a request does not say (it does not, unless ``VIGIE_TIDE_CORRECTION=on``)."""
    return os.environ.get(DEFAULT_ON_BY, "").strip().lower() in ("on", "1", "true", "yes")


def measured_unix(correction: Correction) -> Optional[int]:
    return None if correction.anomaly is None else int(round(unix_from_mjd(correction.anomaly.measured_mjd)))


CORRECTION_NOTE = (
    "When applied, `heights`, `extremes` and `now` are the model's plus the gap that the nearest REFMAR tide gauge measured against the "
    "model, fading with time (ADR 0017). The gap is a mean over 12.4 hours (it cancels the model's own tidal error); heights before the "
    "last measure and the estimated coefficient are the model's. Ours, not SHOM's prediction."
)


def correction_report(correction: Correction) -> TideCorrection:
    """The ``correction`` field of an answer: applied, or why not."""
    station = None
    if correction.station is not None and correction.distance_km is not None:
        station = TideCorrectionStation(
            id=correction.station.id, name=correction.station.name, lat=correction.station.lat, lon=correction.station.lon,
            distance_km=round(correction.distance_km, 1),
        )  # fmt: skip
    anomaly = correction.anomaly
    return TideCorrection(
        applied=anomaly is not None,
        reason=correction.reason if anomaly is None else None,  # type: ignore[arg-type]
        station=station,
        measured_utc=measured_unix(correction),
        anomaly_m=None if anomaly is None else round(anomaly.anomaly_m, 3),
        decay_hours=TAU_HOURS,
        window_minutes=WINDOW_MINUTES,
        attribution=None if anomaly is None else credit_text(*TIDE_CORRECTION),
        note=CORRECTION_NOTE,
    )


async def resolve(collector: "RefmarCollector", lat: float, lon: float, now_unix: float) -> Correction:
    """The correction of a point now. Never raises: a gauge that cannot be read is a reason, not an error."""
    found = nearest_station(lat, lon)
    if found is None:
        return Correction(None, "no_station_near")
    station, distance = found
    try:
        observations: Optional[list[tuple[float, float]]] = await collector.fetch_recent(station.id)
    except Exception:  # noqa: BLE001 - the collector keeps the cause (health); the answer only says it could not read
        observations = None
    return await clock.run_blocking(decide, model(), observations, station, distance, now_unix)
