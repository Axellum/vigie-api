"""The texts a visitor of the repository reads: a modest tone, no detail of the author's home network, both READMEs complete.

The repository is meant to be opened to the public (the decision is the owner's): nothing in it may promise
what a personal project cannot, name a private address, or leave a setting undocumented.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# What a visitor reads: the READMEs, the documents, the OpenAPI texts and the dashboard (all in app/).
PUBLIC_FILES = sorted(
    [
        *ROOT.glob("*.md"),
        *ROOT.glob("docs/**/*.md"),
        *(ROOT / "app").rglob("*.py"),
        *(ROOT / "tools").glob("*.py"),
        *(ROOT / "scripts").glob("*"),
        ROOT / "Dockerfile",
        ROOT / "pyproject.toml",
        *(ROOT / ".github").rglob("*.yml"),
    ]
)

# Promises and marketing words a personal project does not make (English and French).
FORBIDDEN_WORDS = [
    r"100\s?%\s*(?:free|gratuit)",
    r"100\s?%\s*(?:availab|de disponib|disponib)",
    r"professional",
    r"professionnel",
    r"crisis[- ]room",
    r"salle de crise",
    r"mission[- ]critical",
    r"enterprise[- ]grade",
    r"world[- ]class",
    r"DEFCON",
]

# Details of a home network or of the author's machine.
PRIVATE_DETAILS = [
    r"\b192\.168\.\d{1,3}\.\d{1,3}\b",
    r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
    r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b",
    r"auxfilsdesidees",
    r"\b[A-Z]:\\(?:Users|AuxFils)",
    r"@hotmail\.|@gmail\.|@outlook\.",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("pattern", FORBIDDEN_WORDS)
def test_no_promise_and_no_marketing_word(pattern: str) -> None:
    for path in PUBLIC_FILES:
        if path.name == "CHANGELOG.md":
            continue  # it may say which wording was removed
        match = re.search(pattern, read(path), re.IGNORECASE)
        assert match is None, f"{path.relative_to(ROOT)}: '{match.group(0)}'"


@pytest.mark.parametrize("pattern", PRIVATE_DETAILS)
def test_no_private_address_or_path(pattern: str) -> None:
    for path in PUBLIC_FILES:
        match = re.search(pattern, read(path), re.IGNORECASE if "hotmail" in pattern else 0)
        assert match is None, f"{path.relative_to(ROOT)}: '{match.group(0)}'"


def test_the_readmes_point_at_each_other_from_the_top() -> None:
    assert "README.fr.md" in "\n".join(read(ROOT / "README.md").splitlines()[:4])
    assert "README.md" in "\n".join(read(ROOT / "README.fr.md").splitlines()[:4])


def test_the_tone_is_modest_in_both_languages() -> None:
    english, french = read(ROOT / "README.md"), read(ROOT / "README.fr.md")
    assert "personal project" in english and "made with the help of AI" in english
    assert "shared in case it helps" in english and "no promise" in english
    assert "projet personnel" in french and "l'IA" in french and "au cas où" in french and "sans aucune promesse" in french
    assert "not an official warning service" in english.lower()
    assert "n'est pas un service d'alerte officiel" in french
    assert "do not replace official warnings" in english and "ne remplacent pas les alertes officielles" in french


def test_the_french_readme_is_written_with_its_accents() -> None:
    french = read(ROOT / "README.fr.md")
    for word in ("données", "écran", "météo", "vérifié", "à "):
        assert word in french, word


def test_both_readmes_document_every_setting_the_code_reads() -> None:
    settings = set()
    for path in (ROOT / "app").rglob("*.py"):
        settings |= set(re.findall(r"\bVIGIE_[A-Z_]+\b", read(path)))
    assert {"VIGIE_ENABLE_SIMULATION", "VIGIE_ENABLE_RAINVIEWER", "VIGIE_CONTACT", "VIGIE_DB_PATH"} <= settings
    for name in sorted(settings):
        assert f"`{name}`" in read(ROOT / "README.md"), f"README.md does not document {name}"
        assert f"`{name}`" in read(ROOT / "README.fr.md"), f"README.fr.md does not document {name}"


def test_both_readmes_keep_the_windows_run_instructions_generic() -> None:
    for name in ("README.md", "README.fr.md"):
        text = read(ROOT / name)
        assert "scripts\\run.ps1" in text and "New-NetFirewallRule" in text and "-LogFile" in text
        assert "<PC address>" in text or "<adresse du PC>" in text
