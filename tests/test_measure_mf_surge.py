"""Unit tests for tools/measure_mf_surge.py (synthetic series, no network)."""

from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import measure_mf_surge as ms  # noqa: E402
import measure_tides as mt  # noqa: E402

from app.services.tides import MINUTE, mjd_from_unix  # noqa: E402


def test_basin_station_selects_expected_hycom2d_model() -> None:
    atl = ms.BasinStation(mt.Station("refmar", "3", "Brest", 48.38, -4.49), "ATL")
    med = ms.BasinStation(mt.Station("refmar", "524", "Marseille", 43.28, 5.35), "MED")
    assert atl.model_for("arpege") == "HYCOM2D-WARP"
    assert atl.model_for("arome") == "HYCOM2D-WARO"
    assert med.model_for("arpege") == "HYCOM2D-MARP"
    assert med.model_for("arome") == "HYCOM2D-MARO"


def test_score_gauge_and_format_markdown_on_synthetic_surge() -> None:
    station = mt.Station("refmar", "3", "Brest", 48.3829, -4.4950)
    bs = ms.BasinStation(station, "ATL")
    start_mjd = mjd_from_unix(ms.OBS_START.timestamp())
    end_mjd = mjd_from_unix(ms.OBS_END.timestamp()) + 0.1
    mjd = np.arange(start_mjd, end_mjd, MINUTE)

    # Known true storm surge wave (period 48h, amplitude 0.25m) on top of 2.0m chart datum + tide.
    hours = (mjd - start_mjd) * 24.0
    true_surge = 0.25 * np.sin(2.0 * math.pi * hours / 48.0)
    eot20 = 2.0 * np.cos(2.0 * math.pi * hours / 12.42)
    smooth = 4.5 + eot20 + true_surge
    gd = ms.GaugeData(bs=bs, mjd=mjd, raw=smooth.copy(), smooth=smooth, eot20=eot20, offset_eot20=4.5)

    # ARPEGE carries a -0.04m systematic bias (like HYCOM2D spec's -2cm..-5cm); AROME is exact.
    hycom: dict[tuple[str, dt.datetime, int], dict[str, float | None]] = {}
    for run in ms._run_cycles(ms.WINDOW_START, ms.TEST_LAST_ORIGIN):
        for lh in sorted(set(ms.LEADS_H) | {0}):
            target = run + dt.timedelta(hours=lh)
            if target > ms.OBS_END:
                continue
            target_h = (mjd_from_unix(target.timestamp()) - start_mjd) * 24.0
            surge_val = 0.25 * math.sin(2.0 * math.pi * target_h / 48.0)
            hycom[("HYCOM2D-WARP", run, lh)] = {"3": surge_val - 0.04}
            hycom[("HYCOM2D-WARO", run, lh)] = {"3": surge_val}

    scored = ms.score_gauge(gd, hycom)
    assert scored["train_bias_arpege_cm"] == pytest.approx(-4.0, abs=0.5)
    assert scored["train_bias_arome_cm"] == pytest.approx(0.0, abs=0.5)

    lead24 = scored["leads"][24]
    assert lead24["n"] > 0
    assert lead24["r_arpege"] == pytest.approx(1.0, abs=1e-4)
    assert lead24["arome_deb"]["rms_cm"] < 0.5
    assert lead24["arpege_deb"]["rms_cm"] < lead24["arpege_raw"]["rms_cm"]
    assert lead24["arpege_deb"]["rms_cm"] < lead24["eot20"]["rms_cm"]

    report = ms.format_markdown(
        {
            "stations": [scored],
            "pooled_atl": ms._pooled_summary([scored], "ATL"),
            "pooled_med": ms._pooled_summary([scored], "MED"),
        }
    )
    assert "HYCOM2D storm surge vs SHOM REFMAR" in report
    assert "| +24 h |" in report
    assert "| Brest | ATL |" in report
