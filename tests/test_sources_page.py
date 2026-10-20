"""``SOURCES.md`` is the single list of licences, terms, credits and quotas: it must match the code."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.attribution import CREDITS
from app.collectors.budget import QUOTAS
from app.collectors.swic_terms import EXCLUDED, TERMS, SwicTerms
from tests.helpers import make_service

SOURCES_PAGE = (Path(__file__).resolve().parents[1] / "SOURCES.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("key", sorted(CREDITS))
def test_sources_page_quotes_each_credit_exactly(key: str) -> None:
    assert CREDITS[key].text in SOURCES_PAGE, f"SOURCES.md must quote the credit text of '{key}' exactly"


def test_sources_page_has_a_row_for_each_collector(tmp_path: Path) -> None:
    for collector in make_service(tmp_path / "all.db").get_all_collectors():
        assert f"`{collector.provider_id}`" in SOURCES_PAGE, collector.provider_id


@pytest.mark.parametrize("provider_id", sorted(QUOTAS))
def test_every_quota_the_budget_enforces_is_in_the_sources_page(provider_id: str) -> None:
    quota = QUOTAS[provider_id]
    assert quota.source_url.split("/")[2] in SOURCES_PAGE  # the page that publishes it is linked


def test_sources_page_says_what_it_does_not_know() -> None:
    assert "not verified" in SOURCES_PAGE.lower()
    assert "not an official warning service" in SOURCES_PAGE.lower()
    assert "GDACS alerts are automatic and do not replace official warnings" in SOURCES_PAGE


def test_the_licence_file_is_mit_and_the_page_says_the_data_is_not() -> None:
    licence = (Path(__file__).resolve().parents[1] / "LICENSE").read_text(encoding="utf-8")
    assert licence.startswith("MIT License") and "Axel Duviella" in licence
    assert "MIT" in SOURCES_PAGE and "data" in SOURCES_PAGE and "belongs to" in SOURCES_PAGE


@pytest.mark.parametrize("terms", TERMS, ids=lambda terms: terms.source_id)
def test_sources_page_has_a_row_for_each_service_whose_terms_were_looked_at(terms: SwicTerms) -> None:
    """One row a service of the WMO registry: its id, the address that was read, the credit it carries, and whether it is verified."""
    [row] = [line for line in SOURCES_PAGE.splitlines() if f"(`{terms.source_id}`)" in line]
    assert terms.evidence in row and terms.credit in row, terms.source_id
    assert ("**verified**" in row) == (terms.status == "verified"), terms.source_id


def test_sources_page_says_which_services_are_never_read() -> None:
    for source_id in EXCLUDED:
        assert f"`{source_id}`" in SOURCES_PAGE
