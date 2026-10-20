"""The files HACS and hassfest read: manifest, translations, icons, `hacs.json`, and the public text."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "vigie"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def keys(tree: Any, prefix: str = "") -> set[str]:
    """Every path of a nested dict, e.g. `config.step.user.title`."""
    if not isinstance(tree, dict):
        return {prefix}
    found: set[str] = set()
    for key, value in tree.items():
        found |= keys(value, f"{prefix}.{key}" if prefix else key)
    return found


def test_the_manifest() -> None:
    manifest = load(COMPONENT / "manifest.json")
    assert manifest["domain"] == "vigie" == COMPONENT.name
    assert list(manifest)[:2] == ["domain", "name"] and list(manifest)[2:] == sorted(list(manifest)[2:])  # hassfest: sorted keys
    assert manifest["config_flow"] is True and manifest["iot_class"] == "local_push"
    assert manifest["requirements"] == [] and manifest["dependencies"] == []  # what Home Assistant ships (aiohttp, yarl, voluptuous)
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["documentation"].startswith("https://github.com/Axellum/vigie-api")
    assert manifest["issue_tracker"] == "https://github.com/Axellum/vigie-api/issues"


def test_hacs_json() -> None:
    hacs = load(ROOT / "hacs.json")
    assert hacs["name"] == "Vigie" and re.fullmatch(r"\d{4}\.\d+\.\d+", hacs["homeassistant"])
    assert (ROOT / "custom_components").is_dir() and [p.name for p in (ROOT / "custom_components").iterdir() if p.is_dir() and p.name != "__pycache__"] == ["vigie"]


def test_strings_json_is_the_english_translation() -> None:
    assert load(COMPONENT / "strings.json") == load(COMPONENT / "translations" / "en.json")


def test_french_has_every_key_of_english_and_nothing_more() -> None:
    english, french = load(COMPONENT / "translations" / "en.json"), load(COMPONENT / "translations" / "fr.json")
    assert keys(french) == keys(english)


def test_french_keeps_the_placeholders_and_the_accents() -> None:
    english, french = load(COMPONENT / "translations" / "en.json"), load(COMPONENT / "translations" / "fr.json")
    for path in keys(english):
        e, f = english, french
        for part in path.split("."):
            e, f = e[part], f[part]
        assert set(re.findall(r"\{\w+\}", e)) == set(re.findall(r"\{\w+\}", f)), path
    text = json.dumps(french, ensure_ascii=False)
    assert "Terminée" in text and "Aggravée" in text and "Rayon des rivières" in text


def test_every_state_and_event_type_the_code_can_produce_is_translated() -> None:
    from custom_components.vigie.const import EVENT_TYPES, MAX_LEVEL_STATES

    english = load(COMPONENT / "translations" / "en.json")["entity"]
    assert set(english["sensor"]["max_level"]["state"]) == set(MAX_LEVEL_STATES)
    assert set(english["event"]["vigilance"]["state_attributes"]["event_type"]["state"]) == set(EVENT_TYPES)
    icons = load(COMPONENT / "icons.json")["entity"]
    assert set(icons["sensor"]["max_level"]["state"]) == set(MAX_LEVEL_STATES)


@pytest.mark.parametrize("path", sorted(COMPONENT.rglob("*.py")) + sorted(COMPONENT.rglob("*.json")), ids=lambda p: p.name)
def test_nothing_private_in_the_integration(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    assert not re.search(r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b", content), path.name
    assert not re.search(r"@(?:gmail|hotmail|outlook)\.", content, re.IGNORECASE), path.name
    assert not re.search(r"auxfilsdesidees|[A-Z]:\\\\(?:Users|AuxFils)", content, re.IGNORECASE), path.name
