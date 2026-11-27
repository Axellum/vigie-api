"""The dashboard card shipped with the integration: served by Home Assistant, loaded by its frontend (a dashboard resource where
it can, ADR 0044), and in step with the commands that give it its data. Its look is checked in a real Home Assistant by the
`card` job of the workflow."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vigie import async_remove_entry
from custom_components.vigie.card import (
    CARD_FILE,
    CARD_URL,
    LOCAL_URL,
    async_card_back,
    async_register_card,
    async_remove_card,
)
from custom_components.vigie.const import DOMAIN

from .conftest import NORMALS, POINT, TIDES, recorded

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "vigie"
SOURCE = CARD_FILE.read_text(encoding="utf-8")


async def test_nothing_is_served_without_the_frontend(hass: HomeAssistant) -> None:
    assert "frontend" not in hass.config.components
    assert await async_register_card(hass) is False


async def test_the_card_is_served_and_loaded_with_the_version(hass: HomeAssistant) -> None:
    """Without dashboards keeping their resources in storage: loaded from the integration's own path, as before."""
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
    assert asked == {"vigie/card/data", "vigie/card/entries", "vigie/card/spots", "vigie/card/mountain", "vigie/card/beach"} and asked <= registered


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


# --------------------------------------------------------------------------------------------- a dashboard resource (ADR 0044)
DIGEST = hashlib.sha256(CARD_FILE.read_bytes()).hexdigest()[:12]


@pytest.fixture
def www(hass: HomeAssistant, tmp_path: Path) -> Path:
    """The www folder of a configuration of the test's own."""
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "www").mkdir()
    return tmp_path / "www"


async def dashboards(hass: HomeAssistant, www: Path, config: dict[str, Any] | None = None, served: bool = True) -> None:
    """The dashboards set up, then a frontend serving the www folder as /local (or not); its HTTP server is a mock, set after
    the dashboards since their setup starts the real one."""
    assert await async_setup_component(hass, "lovelace", config or {})
    hass.config.components.add("frontend")
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    hass.http.app.router.resources.return_value = [web.StaticResource("/local", str(www))] if served else []


async def ours(hass: HomeAssistant) -> list[tuple[str, str]]:
    """The card's resources: (url, type)."""
    resources = hass.data[LOVELACE_DATA].resources
    await resources.async_get_info()
    return [(item["url"], item["type"]) for item in resources.async_items() if item["url"].startswith("/local/vigie/")]


async def test_listed_as_a_resource_where_local_is_served(hass: HomeAssistant, www: Path) -> None:
    await dashboards(hass, www)
    with patch("homeassistant.components.frontend.add_extra_js_url") as add:
        assert await async_register_card(hass) is True
    add.assert_not_called()  # loaded once, by the resource
    card = CARD_FILE.read_bytes()
    assert (www / "vigie" / "vigie-card.js").read_bytes() == card
    assert gzip.decompress((www / "vigie" / "vigie-card.js.gz").read_bytes()) == card  # served to browsers that accept gzip
    assert await ours(hass) == [(f"{LOCAL_URL}?v={DIGEST}", "module")]
    (config,) = hass.http.async_register_static_paths.call_args.args[0]
    assert config.url_path == CARD_URL  # still served at the integration's path, for a resource added there by hand


async def test_a_new_card_replaces_the_old_url_and_a_double_goes(hass: HomeAssistant, www: Path) -> None:
    await dashboards(hass, www)
    resources = hass.data[LOVELACE_DATA].resources
    await resources.async_get_info()
    await resources.async_create_item({"res_type": "module", "url": f"{LOCAL_URL}?v=old"})
    await resources.async_create_item({"res_type": "js", "url": LOCAL_URL})
    await resources.async_create_item({"res_type": "module", "url": "/local/community/other-card.js"})
    (www / "vigie").mkdir()
    (www / "vigie" / "vigie-card.js").write_text("an old card", encoding="utf-8")
    with patch("homeassistant.components.frontend.add_extra_js_url"):
        assert await async_register_card(hass) is True
    assert await ours(hass) == [(f"{LOCAL_URL}?v={DIGEST}", "module")]
    assert "/local/community/other-card.js" in [item["url"] for item in resources.async_items()]  # someone else's: kept
    copy = www / "vigie" / "vigie-card.js"
    assert copy.read_bytes() == CARD_FILE.read_bytes()
    before = copy.stat().st_mtime_ns
    with patch("homeassistant.components.frontend.add_extra_js_url"):
        assert await async_register_card(hass) is True
    assert copy.stat().st_mtime_ns == before and len(await ours(hass)) == 1  # an unchanged card is not written again


async def test_resources_in_yaml_keep_the_old_way(hass: HomeAssistant, www: Path) -> None:
    await dashboards(hass, www, {"lovelace": {"resource_mode": "yaml"}})
    with patch("homeassistant.components.frontend.add_extra_js_url") as add:
        assert await async_register_card(hass) is True
    add.assert_called_once()
    assert not (www / "vigie").exists()


async def test_no_local_folder_keeps_the_old_way(hass: HomeAssistant, www: Path) -> None:
    """A www folder made after the frontend started is served only from the next start: a resource there would answer 404."""
    await dashboards(hass, www, served=False)
    with patch("homeassistant.components.frontend.add_extra_js_url") as add:
        assert await async_register_card(hass) is True
    add.assert_called_once()
    assert not (www / "vigie").exists() and await ours(hass) == []


async def test_a_copy_that_fails_keeps_the_old_way(hass: HomeAssistant, www: Path) -> None:
    (www / "vigie").write_text("a file where the folder goes", encoding="utf-8")
    await dashboards(hass, www)
    with patch("homeassistant.components.frontend.add_extra_js_url") as add:
        assert await async_register_card(hass) is True
    add.assert_called_once()
    assert await ours(hass) == []


async def test_the_last_entry_removed_takes_the_card_and_an_entry_added_again_brings_it_back(hass: HomeAssistant, www: Path) -> None:
    await dashboards(hass, www)
    with patch("homeassistant.components.frontend.add_extra_js_url"):
        await async_register_card(hass)
    first, second = MockConfigEntry(domain=DOMAIN, entry_id="first"), MockConfigEntry(domain=DOMAIN, entry_id="second")
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    await async_remove_entry(hass, first)  # another entry is left: the card stays
    assert len(await ours(hass)) == 1 and (www / "vigie" / "vigie-card.js").is_file()
    await hass.config_entries.async_remove(first.entry_id)
    await async_remove_entry(hass, second)
    assert await ours(hass) == [] and not (www / "vigie").exists()
    await async_card_back(hass)  # what the setup of an entry added again does
    assert len(await ours(hass)) == 1 and (www / "vigie" / "vigie-card.js").is_file()
    (www / "vigie" / "notes.txt").write_text("someone else's", encoding="utf-8")
    await async_remove_card(hass)
    assert (www / "vigie" / "notes.txt").is_file() and not (www / "vigie" / "vigie-card.js").exists()


# ------------------------------------------------------------------------------------------------- the last answer, in the card
@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed here (it is in CI)")
def test_the_card_draws_its_last_answer_at_once(tmp_path: Path) -> None:
    """The card keeps the last answer of its request, draws it at once while it asks again, never past half an hour, and a
    request that fails shows its error (cases in `card_harness.mjs`)."""
    answer: dict[str, Any] = {
        "entry_id": "e1", "title": "Vigie Biarritz", "language": "fr", "read_utc": "2026-11-22T13:10:00Z",
        "point": recorded(POINT), "tides": recorded(TIDES), "normals": recorded(NORMALS), "station_wind": None,
        "warnings": {"max_level": "green", "server_utc": "2026-11-22T13:10:00Z", "note": None, "attribution": "Vigie", "vigilances": []},
        "earthquakes": None, "spot": None, "activities": None,
    }  # fmt: skip
    (tmp_path / "answer.json").write_text(json.dumps(answer), encoding="utf-8")
    harness = Path(__file__).with_name("card_harness.mjs")
    run = subprocess.run(["node", str(harness), str(CARD_FILE), str(tmp_path / "answer.json")], capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout.strip().splitlines()[-1])

    def loading(html: str) -> bool:
        return "Chargement" in html or "Loading" in html

    assert loading(out["first_before"]) and out["asked"] == 1
    assert len(out["first_after"]) > 1000 and not loading(out["first_after"])
    assert len(out["second_before"]) > 1000 and not loading(out["second_before"])  # drawn before it has Home Assistant
    assert 0 <= out["stored_age_ms"] < 60_000
    assert loading(out["other_spot_before"])
    assert "Aucune entrée Vigie" in out["failed_after"]
    assert loading(out["old_before"])
