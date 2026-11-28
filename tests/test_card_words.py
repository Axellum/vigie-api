"""The dashboard card has words for every value of the API it names (the card is JavaScript: read as text, no browser)."""

from __future__ import annotations

import re
import typing
from pathlib import Path

from app.models.surf import SurfWind

CARD = Path(__file__).resolve().parents[1] / "custom_components" / "vigie" / "frontend" / "vigie-card.js"


def test_the_wind_tile_names_every_relative_wind_of_the_api() -> None:
    """The API says `cross-shore` and the card's words are keyed `cross_shore`: the tile said only « Vent » for a wind along
    the coast (seen on 2026-11-28)."""
    text = CARD.read_text(encoding="utf-8")
    found = re.search(r"const rk = ([^\n]*);\n\s*const rel = \{([^\n]*)\}\[rk\];", text)
    assert found, "the wind tile of beachTiles() changed shape: update this test"
    assert "replace('-', '_')" in found.group(1)
    keys = set(re.findall(r"(\w+): \['wind_", found.group(2)))
    literal = next(
        arg for arg in typing.get_args(SurfWind.model_fields["relative"].annotation) if arg is not type(None)
    )
    values = typing.get_args(literal)
    assert values, "SurfWind.relative is no longer a Literal: update this test"
    assert {value.replace("-", "_") for value in values} <= keys
    for key in keys:
        # One word in French, one in English.
        assert len(re.findall(rf"\bwind_{key}: '", text)) == 2, key
