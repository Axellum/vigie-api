"""Commercial use: every credit says whether a paid app may show its data, and ``SOURCES.md`` quotes it.

The table is ``app/commercial.py``. A new source gets a credit in ``app/attribution.py``; these tests then fail until it
also gets a line here, so no source joins without saying whether its terms allow commercial use.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.attribution import AIR_QUALITY, CREDITS, FLOOD, MARINE, OPEN_METEO_WEATHER, PREALERT, SURF
from app.commercial import COMMERCIAL_USE, commercial_status

SOURCES_PAGE = (Path(__file__).resolve().parents[1] / "SOURCES.md").read_text(encoding="utf-8")
WORDS = {"allowed": "**allowed**", "forbidden": "**forbidden**", "not_verified": "not verified"}


def test_every_credit_says_whether_commercial_use_is_allowed() -> None:
    assert set(COMMERCIAL_USE) == set(CREDITS)


@pytest.mark.parametrize("key", sorted(COMMERCIAL_USE))
def test_each_line_names_its_key_and_the_pages_read(key: str) -> None:
    use = COMMERCIAL_USE[key]
    assert use.key == key
    assert use.reason.strip()
    assert use.evidence and all(url.startswith("https://") for url in use.evidence)


@pytest.mark.parametrize("key", sorted(COMMERCIAL_USE))
def test_sources_page_quotes_each_line(key: str) -> None:
    use = COMMERCIAL_USE[key]
    row = f"| `{key}` | {CREDITS[key].provider} | {WORDS[use.status]} | {use.reason} |"
    assert row in SOURCES_PAGE, f"SOURCES.md must have the commercial-use row of '{key}' as in app/commercial.py"


def test_a_block_takes_the_most_restrictive_status() -> None:
    assert commercial_status("usgs") == "allowed"
    assert commercial_status("usgs", "gdacs") == "not_verified"
    assert commercial_status("gdacs", "rainviewer", "usgs") == "forbidden"


def test_a_block_with_no_credit_or_an_unknown_one_is_not_allowed() -> None:
    assert commercial_status() == "not_verified"
    assert commercial_status("a_source_with_no_line") == "not_verified"


@pytest.mark.parametrize("block", [OPEN_METEO_WEATHER, AIR_QUALITY, FLOOD, MARINE, SURF])
def test_what_comes_from_the_free_open_meteo_api_is_not_for_a_paid_app(block: tuple[str, ...]) -> None:
    assert commercial_status(*block) == "forbidden"


def test_the_pre_alert_reads_ecmwf_open_data_which_allows_it() -> None:
    assert commercial_status(*PREALERT) == "allowed"
