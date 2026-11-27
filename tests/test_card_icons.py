"""The weather icons of the Home Assistant card (ADR 0045): the block of drawings in the card is the one scripts/card_icons.py
draws, every icon the card asks for exists in each style, and a drawing carries no colour of its own (the card gives them)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "custom_components" / "vigie" / "frontend" / "vigie-card.js"
SOURCE = CARD.read_text(encoding="utf-8")

_spec = importlib.util.spec_from_file_location("card_icons", ROOT / "scripts" / "card_icons.py")
assert _spec and _spec.loader
card_icons = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(card_icons)


def test_the_block_of_the_card_is_the_one_the_script_draws() -> None:
    assert card_icons.main(["--check"]) == 0, "run python scripts/card_icons.py"


def test_every_style_draws_every_icon_in_its_box() -> None:
    assert list(card_icons.STYLES) == ["meridien", "line", "duo", "badge"]
    for style, (box, icons) in card_icons.STYLES.items():
        assert set(icons) == set(card_icons.NAMES), style
        assert f"  {style}: {{ box: '{box}', icons: {{" in SOURCE


def test_the_drawings_have_no_colour_of_their_own() -> None:
    block = SOURCE.split(card_icons.BEGIN, 1)[1].split(card_icons.END, 1)[0]
    # black and white only inside the masks, which cut and never show
    assert set(re.findall(r'(?:fill|stroke|color)="(#[0-9A-Fa-f]+)"', block)) == {"#fff", "#000"}
    roles = set(re.findall(r'class="(v[a-z])"', block))
    mapped = set(re.findall(r"(v[a-z]): '", SOURCE.split("const ICON_ROLE = {", 1)[1].split("};", 1)[0]))
    assert roles == mapped == {"vs", "vl", "vn", "vm", "ve", "vg", "vx", "vb"}


def test_every_icon_the_card_asks_for_exists() -> None:
    def names(start: str) -> set[str]:
        return set(re.findall(r": '([a-z_]+)'", SOURCE.split(start, 1)[1].split("};", 1)[0]))

    asked = names("const CONDITION_ICON = {") | names("const NIGHT_ICON = {") | names("const ICON_ALIAS = {")
    wmo = SOURCE.split("function wmoKind(code) {", 1)[1].split("\n}", 1)[0]
    asked |= set(re.findall(r"return '([a-z_]+)';", wmo))
    assert asked and asked <= set(card_icons.NAMES), asked - set(card_icons.NAMES)
