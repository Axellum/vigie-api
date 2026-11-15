"""Decode Météo-France / SHOM HYCOM2D storm-surge GRIB2 fields published on public open data.

Météo-France publishes hourly barotropic storm surge (``surcote``) 2D fields from its operational
HYCOM2D (TOLOSA/HOMONIM) configurations on its public OVH S3 bucket under Licence Ouverte Etalab 2.0
(https://meteo.data.gouv.fr):

- ``HYCOM2D-WARP`` / ``HYCOM2D-WARO``: Northeast Atlantic, English Channel and North Sea (43 N to 62 N,
  9 W to 10 E, 1/24 degree ~ 4.6 km), forced by ARPEGE (00, 06, 12, 18 UTC up to +60..114 h) and AROME
  (00, 03, 06, 12, 18 UTC up to +36..42 h).
- ``HYCOM2D-MARP`` / ``HYCOM2D-MARO``: Western Mediterranean (35 N to 45 N, 6 W to 17 E, 1/24 degree).

Each hourly GRIB2 file carries instantaneous storm surge (parameter category 3, parameter number 192,
``stepType == "instant"``, in metres); every 3 h / 24 h an extra summary message (parameter number 193)
may appear in the same file and is ignored here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np

from app.prealert.gfs import _DECODE_LOCK, split_messages
from app.services.geodesy import haversine_km

OVH_PNT_BASE = "https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt"
SURGE_MODELS = ("HYCOM2D-WARP", "HYCOM2D-WARO", "HYCOM2D-MARP", "HYCOM2D-MARO")
GRID_CODE = "004"
PACKAGE = "SP1"
SURGE_CATEGORY = 3
SURGE_INSTANT_NUMBER = 192


def grib_url(model: str, run_utc: datetime, lead_h: int) -> str:
    """OVH open-data URL for one HYCOM2D hourly GRIB2 file."""
    run_iso = run_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"{OVH_PNT_BASE}/{run_iso}/vague-surcote/{model}/{GRID_CODE}/{PACKAGE}/"
        f"vague-surcote-{model}__{GRID_CODE}__{PACKAGE}__{lead_h:03d}H__{run_iso}.grib2"
    )


def _normalize_lon(lon: float) -> float:
    return ((lon + 180.0) % 360.0) - 180.0


@dataclass(frozen=True)
class SurgeSample:
    """Storm surge interpolated or read at the nearest valid sea cell for a target point."""

    surge_m: float
    cell_lat: float
    cell_lon: float
    distance_km: float
    method: str  # "bilinear" or "nearest_sea"


@dataclass(frozen=True)
class SurgeGrid:
    """One instantaneous 2D HYCOM2D surge field, with ascending latitudes and [-180, 180) longitudes."""

    model: str
    run_utc: datetime
    lead_h: int
    valid_utc: datetime
    lats: np.ndarray  # ascending (Nj,)
    lons: np.ndarray  # ascending (Ni,) in [-180, 180)
    values: np.ndarray  # (Nj, Ni) metres, NaN on land

    def sample(self, lat: float, lon: float, max_distance_km: float = 15.0) -> Optional[SurgeSample]:
        """Return surge in metres at ``(lat, lon)`` using sea-aware bilinear or nearest sea cell.

        Most coastal gauges sit on or just inside a port whose enclosing 1/24-degree grid cell has at
        least one masked land corner; bilinear is used when all four corners are wet, otherwise the
        closest wet sea point within ``max_distance_km`` is selected.
        """
        target_lon = _normalize_lon(lon)
        if not (self.lats[0] - 0.5 <= lat <= self.lats[-1] + 0.5 and self.lons[0] - 0.5 <= target_lon <= self.lons[-1] + 0.5):
            return None

        j = int(np.searchsorted(self.lats, lat))
        i = int(np.searchsorted(self.lons, target_lon))
        j0, j1 = max(0, j - 1), min(len(self.lats) - 1, j)
        i0, i1 = max(0, i - 1), min(len(self.lons) - 1, i)

        if self.lats[0] <= lat <= self.lats[-1] and self.lons[0] <= target_lon <= self.lons[-1]:
            c00 = self.values[j0, i0]
            c01 = self.values[j0, i1]
            c10 = self.values[j1, i0]
            c11 = self.values[j1, i1]
            if np.isfinite(c00) and np.isfinite(c01) and np.isfinite(c10) and np.isfinite(c11):
                wy = 0.0 if j1 == j0 else (lat - self.lats[j0]) / (self.lats[j1] - self.lats[j0])
                wx = 0.0 if i1 == i0 else (target_lon - self.lons[i0]) / (self.lons[i1] - self.lons[i0])
                val = (
                    (1.0 - wy) * ((1.0 - wx) * c00 + wx * c01)
                    + wy * ((1.0 - wx) * c10 + wx * c11)
                )
                return SurgeSample(float(val), float(lat), float(target_lon), 0.0, "bilinear")

        # Fall back to nearest wet grid point in a bounded local window.
        radius_cells = max(5, int(math.ceil(max_distance_km / 3.0)) + 2)
        j_lo = max(0, j0 - radius_cells)
        j_hi = min(len(self.lats), j1 + radius_cells + 1)
        i_lo = max(0, i0 - radius_cells)
        i_hi = min(len(self.lons), i1 + radius_cells + 1)
        sub_vals = self.values[j_lo:j_hi, i_lo:i_hi]
        wet_j, wet_i = np.where(np.isfinite(sub_vals))
        if wet_j.size == 0:
            return None

        best_km = float("inf")
        best_val = 0.0
        best_lat = 0.0
        best_lon = 0.0
        for r_off, c_off in zip(wet_j.tolist(), wet_i.tolist(), strict=True):
            c_lat = float(self.lats[j_lo + r_off])
            c_lon = float(self.lons[i_lo + c_off])
            km = haversine_km(lat, target_lon, c_lat, c_lon)
            if km < best_km:
                best_km = km
                best_val = float(sub_vals[r_off, c_off])
                best_lat = c_lat
                best_lon = c_lon

        if best_km > max_distance_km:
            return None
        return SurgeSample(best_val, best_lat, best_lon, best_km, "nearest_sea")


def decode_surge(data: bytes, model: str = "HYCOM2D") -> SurgeGrid:
    """Decode the instantaneous surge field from one HYCOM2D GRIB2 byte buffer."""
    import eccodes  # optional dependency (extra ``prealert``)

    with _DECODE_LOCK:
        return _decode_locked(eccodes, data, model)


def _decode_locked(eccodes: Any, data: bytes, model: str) -> SurgeGrid:
    for raw in split_messages(data):
        handle = eccodes.codes_new_from_message(raw)
        try:
            cat = int(eccodes.codes_get(handle, "parameterCategory"))
            num = int(eccodes.codes_get(handle, "parameterNumber"))
            step_type = str(eccodes.codes_get(handle, "stepType"))
            if (cat, num, step_type) != (SURGE_CATEGORY, SURGE_INSTANT_NUMBER, "instant"):
                continue

            if eccodes.codes_get(handle, "gridType") != "regular_ll":
                raise ValueError(f"HYCOM2D grid {eccodes.codes_get(handle, 'gridType')} is not regular_ll")

            ni = int(eccodes.codes_get(handle, "Ni"))
            nj = int(eccodes.codes_get(handle, "Nj"))
            lat1 = float(eccodes.codes_get(handle, "latitudeOfFirstGridPointInDegrees"))
            lat2 = float(eccodes.codes_get(handle, "latitudeOfLastGridPointInDegrees"))
            lon1 = float(eccodes.codes_get(handle, "longitudeOfFirstGridPointInDegrees"))
            lon2 = float(eccodes.codes_get(handle, "longitudeOfLastGridPointInDegrees"))
            if lon2 < lon1:
                lon2 += 360.0

            lats = np.linspace(lat1, lat2, nj)
            lons = np.linspace(lon1, lon2, ni)
            lons = np.array([_normalize_lon(float(x)) for x in lons], dtype=np.float64)

            values = np.asarray(eccodes.codes_get_values(handle), dtype=np.float64).reshape(nj, ni)
            missing = float(eccodes.codes_get(handle, "missingValue"))
            has_bitmap = bool(eccodes.codes_get(handle, "bitmapPresent"))
            if has_bitmap or np.any(values == missing):
                values = np.where(values == missing, np.nan, values)

            data_date = int(eccodes.codes_get(handle, "dataDate"))
            data_time = int(eccodes.codes_get(handle, "dataTime"))
            lead_h = int(eccodes.codes_get(handle, "endStep"))
        finally:
            eccodes.codes_release(handle)

        if lats[0] > lats[-1]:
            lats = lats[::-1].copy()
            values = values[::-1, :]
        if lons[0] > lons[-1]:
            order = np.argsort(lons)
            lons = lons[order]
            values = values[:, order]

        year, rem = divmod(data_date, 10000)
        month, day = divmod(rem, 100)
        hour, minute = divmod(data_time, 100)
        run_utc = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        valid_utc = run_utc + timedelta(hours=lead_h)
        return SurgeGrid(
            model=model,
            run_utc=run_utc,
            lead_h=lead_h,
            valid_utc=valid_utc,
            lats=lats,
            lons=lons,
            values=values,
        )

    raise ValueError("GRIB2 payload has no instantaneous HYCOM2D surge field (cat=3, num=192)")
