"""The registry of the WMO services: its parsing, the bundled snapshot, and the setting that picks the services an instance reads.

The registry answer is the recorded one of 2026-10-20, reduced in ``app/data/swic_registry.json.gz`` (140 services). Nothing here
reaches the network.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from app.collectors.swic_cap import CapError
from app.collectors.swic_registry import (
    DEFAULT_WMO_SOURCES,
    REGISTRY_URL,
    SNAPSHOT_PATH,
    SwicRegistryCollector,
    bundled_registry,
    parse_registry,
    snapshot_payload,
    wmo_sources_from_env,
)
from app.services.geoindex import default_countries
from tests import swic_samples as samples
from tests.helpers import SimClock
from tools import build_swic_snapshot

RECORD = {
    "sourceId": "xx-test-en",
    "capAlertFeed": "https://example.org/a.xml, https://example.org/b.xml , not-an-address",
    "countryName": "Testland",
    "authorityCountry": "xx",
    "authorityAbbrev": "TST",
    "metLink": "https://example.org/",
    "sourceIsOfficial": True,
    "isMeteoalarm": False,
    "byLanguage": [{"code": "en", "name": "Test Weather Service", "contactName": "A Person", "email": "a.person@example.org"}],
    "contactEmail": "someone@example.org",
}


def payload(*records: dict) -> dict:  # type: ignore[type-arg]
    return {"sources": [{"source": record} for record in records]}


# ---------------------------------------------------------------------------------------------------- parsing


def test_a_record_is_read_with_its_feeds_split_and_its_country_upper_cased() -> None:
    [source] = parse_registry(payload(RECORD))
    assert source.source_id == "xx-test-en" and source.country == "XX" and source.name == "Test Weather Service"
    assert source.feeds == ("https://example.org/a.xml", "https://example.org/b.xml")  # one per language; what is no address is dropped
    assert source.official is True and source.meteoalarm is False and source.web == "https://example.org/"


def test_a_record_without_an_id_is_skipped_and_one_without_a_country_has_none() -> None:
    nameless = {**RECORD, "sourceId": " "}
    countryless = {**RECORD, "sourceId": "xx-none", "authorityCountry": None}
    found = parse_registry(payload(nameless, countryless, "not a record"))  # type: ignore[arg-type]
    assert [(source.source_id, source.country) for source in found] == [("xx-none", None)]


@pytest.mark.parametrize("answer", [{}, [], {"sources": "x"}, {"sources": []}, {"sources": [{"source": {}}]}, "<html>blocked</html>"])
def test_what_is_not_a_registry_is_an_error_not_an_empty_list(answer: object) -> None:
    with pytest.raises(CapError):
        parse_registry(answer)


# ---------------------------------------------------------------------------------------------------- the snapshot


def test_the_snapshot_holds_the_services_of_the_recording_and_nothing_personal() -> None:
    services = bundled_registry()
    assert len(services) == 140 and len({s.source_id for s in services}) == 140
    assert all(s.official for s in services)
    raw = gzip.decompress(SNAPSHOT_PATH.read_bytes()).decode("utf-8")
    assert "@" not in raw  # no e-mail address of the people in the records
    for entry in json.loads(raw)["sources"]:
        assert set(entry["source"]) <= {
            "sourceId", "byLanguage", "capAlertFeed", "countryName", "authorityCountry", "authorityAbbrev", "metLink",
            "sourceIsOfficial", "isMeteoalarm", "isCountry", "capAlertFeedStatus",
        }
        assert all(set(language) == {"code", "name"} for language in entry["source"]["byLanguage"])


def test_the_snapshot_is_the_registry_reduced_by_the_code_that_reads_it() -> None:
    """Rewriting it from itself gives the same bytes (no date, no file name in the gzip header)."""
    first = gzip.decompress(SNAPSHOT_PATH.read_bytes())
    again = snapshot_payload(json.loads(first))
    body = json.dumps(again, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    assert body == first


def test_writing_the_snapshot_twice_gives_the_same_bytes(tmp_path: Path) -> None:
    source = json.loads(gzip.decompress(SNAPSHOT_PATH.read_bytes()))
    first, second = tmp_path / "a.json.gz", tmp_path / "b.json.gz"
    assert build_swic_snapshot.write_snapshot(source, first) == 140
    build_swic_snapshot.write_snapshot(source, second)
    assert first.read_bytes() == second.read_bytes()
    assert [s.source_id for s in bundled_registry(first)] == [s.source_id for s in bundled_registry()]


def test_the_snapshot_drops_the_people_of_a_record(tmp_path: Path) -> None:
    target = tmp_path / "one.json.gz"
    build_swic_snapshot.write_snapshot(payload(RECORD), target)
    raw = gzip.decompress(target.read_bytes()).decode("utf-8")
    assert "@" not in raw and "A Person" not in raw and "someone" not in raw
    assert [s.source_id for s in bundled_registry(target)] == ["xx-test-en"]


def test_the_snapshot_refuses_to_write_what_the_code_cannot_read(tmp_path: Path) -> None:
    with pytest.raises(CapError):
        build_swic_snapshot.write_snapshot({"sources": []}, tmp_path / "x.json.gz")
    assert not (tmp_path / "x.json.gz").exists()


def test_every_country_a_service_answers_for_has_a_border() -> None:
    """A service whose country has no border could never be asked by a position (the borders are Natural Earth's, public domain)."""
    borders = set(default_countries().countries())
    from app.collectors.swic_terms import EXCLUDED

    missing = sorted(
        {s.country for s in bundled_registry() if s.country and s.source_id not in EXCLUDED and not s.meteoalarm and s.country not in borders}
    )
    assert missing == []


# ---------------------------------------------------------------------------------------------------- the setting


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "verified"), ("", "verified"), ("verified", "verified"), ("  ALL ", "all"), ("all", "all"), ("off", "off"),
        ("0", "off"), ("false", "off"), ("No", "off"), ("none", "off"),
        ("everything", "verified"), ("1", "verified"),  # an unknown word keeps the cautious default
    ],
)
def test_the_setting_has_a_cautious_default(monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: str) -> None:
    if raw is None:
        monkeypatch.delenv("VIGIE_WMO_SOURCES", raising=False)
    else:
        monkeypatch.setenv("VIGIE_WMO_SOURCES", raw)
    assert wmo_sources_from_env() == expected
    assert DEFAULT_WMO_SOURCES == "verified"


# ---------------------------------------------------------------------------------------------------- the collector


async def test_the_registry_is_asked_once_a_day_and_conditionally(sim_clock: SimClock) -> None:
    server = samples.FixtureServer()
    server.answers[REGISTRY_URL] = samples.registry_json()
    collector = SwicRegistryCollector(http_client=server.client())
    first = await collector.fetch_registry()
    assert len(first.value) == 140 and not first.stale
    await collector.fetch_registry()
    assert server.count(REGISTRY_URL) == 1  # cached
    sim_clock.advance(24 * 3600 + 1)
    await collector.fetch_registry()
    assert server.count(REGISTRY_URL) == 2  # a day later


async def test_a_registry_that_is_gone_keeps_the_last_answer_and_says_so(sim_clock: SimClock) -> None:
    server = samples.FixtureServer()
    server.answers[REGISTRY_URL] = samples.registry_json()
    collector = SwicRegistryCollector(http_client=server.client())
    await collector.fetch_registry()
    server.answers[REGISTRY_URL] = 503
    sim_clock.advance(24 * 3600 + 1)
    kept = await collector.fetch_registry()
    assert len(kept.value) == 140 and kept.stale


async def test_a_registry_page_that_is_not_json_is_an_error(sim_clock: SimClock) -> None:
    server = samples.FixtureServer()
    server.answers[REGISTRY_URL] = b"<html>Request blocked</html>"
    collector = SwicRegistryCollector(http_client=server.client())
    with pytest.raises(Exception):  # noqa: B017,PT011
        await collector.fetch_registry()
