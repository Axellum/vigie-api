"""The dashboard card shipped with the integration: served by Home Assistant, loaded by its frontend, and in step with the
commands that give it its data. Its look is checked in a real Home Assistant by the `card` job of the workflow."""

from __future__ import annotations

import re
import shutil
import struct
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.vigie.card import CARD_FILE, CARD_URL, async_register_card

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "vigie"
SOURCE = CARD_FILE.read_text(encoding="utf-8")


async def test_nothing_is_served_without_the_frontend(hass: HomeAssistant) -> None:
    assert "frontend" not in hass.config.components
    assert await async_register_card(hass) is False


async def test_the_card_is_served_and_loaded_with_the_version(hass: HomeAssistant) -> None:
    hass.config.components.add("frontend")
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    with patch("homeassistant.components.frontend.add_extra_js_url") as add:
        assert await async_register_card(hass) is True
    (config,) = hass.http.async_register_static_paths.call_args.args[0]
    assert config.url_path == CARD_URL and Path(config.path) == CARD_FILE and CARD_FILE.is_file()
    assert config.cache_headers is False  # the version in the URL is what makes a browser read a new card
    version = (await hass.async_add_executor_job((COMPONENT / "manifest.json").read_text)).split('"version": "')[1].split('"')[0]
    add.assert_called_once_with(hass, f"{CARD_URL}?v={version}")


def test_the_card_asks_only_commands_that_exist() -> None:
    asked = set(re.findall(r"type: '(vigie/[a-z/_]+)'", SOURCE))
    registered = set(re.findall(r'vol\.Required\("type"\): "(vigie/[a-z/_]+)"', (COMPONENT / "websocket.py").read_text(encoding="utf-8")))
    assert asked == {"vigie/card/data", "vigie/card/entries", "vigie/card/spots"} and asked <= registered


def test_the_card_defines_its_elements_once() -> None:
    for tag in ("vigie-card", "vigie-card-editor"):
        assert f"const {'EDITOR_TAG' if tag.endswith('editor') else 'TAG'} = '{tag}';" in SOURCE
    assert "if (!customElements.get(TAG)) customElements.define(TAG, VigieCard);" in SOURCE  # also added as a resource by hand: no error
    assert "window.customCards" in SOURCE  # listed in the card picker


def test_the_card_has_both_languages_for_every_word() -> None:
    def keys(block: str) -> set[str]:
        # a key starts a line or follows the end of the previous value (a quote or a bracket, then ", ")
        return set(re.findall(r"(?:^\s+|['\]], )([a-z_0-9]+): ", block, re.MULTILINE))

    fr = SOURCE.split("  fr: {", 1)[1].split("\n  en: {", 1)[0]
    en = SOURCE.split("\n  en: {", 1)[1].split("\n};", 1)[0]
    assert keys(fr) == keys(en)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed here (it is in CI)")
def test_the_card_is_valid_javascript() -> None:
    result = subprocess.run(["node", "--check", str(CARD_FILE)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(("name", "size"), [("icon.png", 256), ("icon@2x.png", 512)])
def test_the_brand_images(name: str, size: int) -> None:
    """The images Home Assistant shows for a custom integration (its `brands` integration reads `brand/`)."""
    data = (COMPONENT / "brand" / name).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (size, size)
