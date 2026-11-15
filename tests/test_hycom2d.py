"""Météo-France HYCOM2D storm-surge GRIB2 decoder and grid sampling tests (no network)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.prealert.hycom2d import SURGE_CATEGORY, SURGE_INSTANT_NUMBER, SurgeGrid, decode_surge, grib_url


def test_grib_url_matches_meteofrance_ovh_s3_layout() -> None:
    run = datetime(2026, 11, 15, 6, 0, tzinfo=timezone.utc)
    assert grib_url("HYCOM2D-WARP", run, 3) == (
        "https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt/2026-11-15T06:00:00Z/"
        "vague-surcote/HYCOM2D-WARP/004/SP1/"
        "vague-surcote-HYCOM2D-WARP__004__SP1__003H__2026-11-15T06:00:00Z.grib2"
    )


def _sample_grid() -> SurgeGrid:
    lats = np.array([48.0, 48.5, 49.0], dtype=np.float64)
    lons = np.array([-5.0, -4.5, -4.0], dtype=np.float64)
    values = np.array(
        [
            [0.10, 0.20, np.nan],
            [0.30, 0.40, 0.50],
            [np.nan, np.nan, 0.60],
        ],
        dtype=np.float64,
    )
    run = datetime(2026, 11, 15, 6, 0, tzinfo=timezone.utc)
    return SurgeGrid("HYCOM2D-WARP", run, 3, run + timedelta(hours=3), lats, lons, values)


def test_sample_uses_bilinear_over_wet_corners_and_nearest_sea_by_coast() -> None:
    grid = _sample_grid()
    mid = grid.sample(48.25, -4.75)
    assert mid is not None
    assert mid.method == "bilinear"
    assert mid.distance_km == 0.0
    assert mid.surge_m == pytest.approx(0.25)

    # Corner (48.0, -4.0) is NaN (land), so a point in that coastal square falls back to nearest wet cell.
    coastal = grid.sample(48.05, -4.05, max_distance_km=60.0)
    assert coastal is not None
    assert coastal.method == "nearest_sea"
    assert coastal.surge_m in (0.20, 0.50)

    # Far outside domain or too far from wet sea points -> None (never fabricates a number).
    assert grid.sample(43.28, 5.35, max_distance_km=15.0) is None
    assert grid.sample(48.0, -4.0, max_distance_km=1.0) is None


def test_decode_surge_reads_instant_192_flips_descending_lat_and_masks_missing() -> None:
    eccodes = pytest.importorskip("eccodes")

    def _encode(param_number: int, step_type: str, values: list[float], end_step: int = 3) -> bytes:
        h = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
        try:
            eccodes.codes_set(h, "Ni", 3)
            eccodes.codes_set(h, "Nj", 2)
            # Mirror HYCOM2D's N->S scanning and 0..360 west-longitude encoding
            eccodes.codes_set(h, "latitudeOfFirstGridPointInDegrees", 49.0)
            eccodes.codes_set(h, "latitudeOfLastGridPointInDegrees", 48.0)
            eccodes.codes_set(h, "longitudeOfFirstGridPointInDegrees", 355.0)
            eccodes.codes_set(h, "longitudeOfLastGridPointInDegrees", 357.0)
            eccodes.codes_set(h, "iDirectionIncrementInDegrees", 1.0)
            eccodes.codes_set(h, "jDirectionIncrementInDegrees", 1.0)
            eccodes.codes_set(h, "dataDate", 20261115)
            eccodes.codes_set(h, "dataTime", 600)
            eccodes.codes_set(h, "stepType", step_type)
            eccodes.codes_set(h, "endStep", end_step)
            eccodes.codes_set(h, "parameterCategory", SURGE_CATEGORY)
            eccodes.codes_set(h, "parameterNumber", param_number)
            eccodes.codes_set(h, "missingValue", 9999.0)
            eccodes.codes_set(h, "bitmapPresent", 1)
            eccodes.codes_set_values(h, values)
            return eccodes.codes_get_message(h)
        finally:
            eccodes.codes_release(h)

    # First message is param 193 avg (must be skipped); second is param 192 instant (must be decoded).
    avg_msg = _encode(193, "avg", [9.9] * 6)
    inst_msg = _encode(SURGE_INSTANT_NUMBER, "instant", [9999.0, 0.35, 0.45, 0.15, 0.25, 9999.0])
    grid = decode_surge(avg_msg + inst_msg, model="HYCOM2D-WARP")

    assert grid.model == "HYCOM2D-WARP"
    assert grid.run_utc == datetime(2026, 11, 15, 6, 0, tzinfo=timezone.utc)
    assert grid.valid_utc == datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)
    assert grid.lats.tolist() == pytest.approx([48.0, 49.0])
    assert grid.lons.tolist() == pytest.approx([-5.0, -4.0, -3.0])
    assert grid.values[0, 0] == pytest.approx(0.15)  # row flipped from S to N
    assert grid.values[1, 1] == pytest.approx(0.35)
    assert np.isnan(grid.values[1, 0])
    assert np.isnan(grid.values[0, 2])
