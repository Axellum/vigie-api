"""The card's "rain in N min": counted from now, from the start of the steps the API gives (``rain_nowcast.start_utc``).

With Open-Meteo the first 15-minute step is a quarter-hour already over; with Météo-France PIAF the twelve 5-minute rates start
at the 5-minute boundary at which the API read the rain. The answers are the one recorded at Biarritz on 2026-11-22, their rain
replaced by the steps of each case, labelled as such. Drawn by Node outside a browser (``card_render.cjs``).
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from typing import Any

import pytest

from custom_components.vigie.card import CARD_FILE

from .conftest import recorded
from .test_card_season import HARNESS, text

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed here (it is in CI)")

NOW = 1_795_378_800  # 2026-11-22 20:20 UTC, 5 minutes into a quarter-hour
QUARTER = NOW - NOW % 900  # 20:15


def render(rain: dict[str, Any]) -> dict[str, Any]:
    point = copy.deepcopy(recorded("point_biarritz_2026-11-22.json"))
    point["rain_nowcast"].update(rain)
    data = {"point": point, "forest_fire": None, "warnings": None, "earthquakes": None, "tides": None, "normals": None}
    result = subprocess.run(
        ["node", str(HARNESS), str(CARD_FILE)],
        input=json.dumps({"lang": "fr", "width": 374, "now_ms": NOW * 1000, "data": data}),
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_open_meteo_skips_the_quarter_hour_already_over() -> None:
    """Steps from 20:00 (made-up values): the rain of 20:00 to 20:15 is over, the next rain starts at 20:30, in 10 minutes."""
    out = render({"rate_5m_mm_h": None, "mm_per_step": [1.0, 0.0, 0.4, 0.0], "start_utc": QUARTER - 900})
    assert out["nowcast"] == 10
    assert "Pluie dans 10 min" in text(out["home"])


def test_rain_only_in_the_step_already_over_is_no_rain_to_come() -> None:
    out = render({"rate_5m_mm_h": None, "mm_per_step": [1.0, 0.0, 0.0, 0.0], "start_utc": QUARTER - 900})
    assert out["nowcast"] == -1
    assert "Pas de pluie dans l’heure" in text(out["home"])


def test_the_step_in_progress_is_rain_now() -> None:
    out = render({"rate_5m_mm_h": None, "mm_per_step": [0.0, 0.3, 0.0, 0.0], "start_utc": QUARTER - 900})
    assert out["nowcast"] == 0
    assert "Il pleut" in text(out["home"])


def test_piaf_counts_its_five_minute_rates_from_their_start() -> None:
    """Twelve 5-minute rates (made-up values) from 20:20: rain from the fourth, 20:35, in 15 minutes."""
    rates = [0.0, 0.0, 0.0, 1.2] + [0.0] * 8
    out = render({"rate_5m_mm_h": rates, "mm_per_step": [0.0, 0.1, 0.0, 0.0], "start_utc": NOW - NOW % 300})
    assert out["nowcast"] == 15


def test_an_answer_without_the_start_counts_from_its_first_step() -> None:
    """An older API gives no ``start_utc``: the card counts as before, from the first step."""
    out = render({"rate_5m_mm_h": None, "mm_per_step": [0.0, 0.0, 0.4, 0.0], "start_utc": None})
    assert out["nowcast"] == 30
