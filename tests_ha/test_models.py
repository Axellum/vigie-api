"""The rules that turn an answer into states: unknown against none, the tracking key, the changes, the bounded attributes."""

from __future__ import annotations

import json
from typing import Any

import pytest

from custom_components.vigie.api import normalize_url
from custom_components.vigie.const import ATTRIBUTES_BUDGET_BYTES, MAX_LISTED_VIGILANCES
from custom_components.vigie.models import ChangeTracker, build_attributes, level_rank, parse_response, parse_vigilance

from .payloads import SERVER_UTC, coverage, response, vigilance

RECORDER_LIMIT_BYTES = 16384  # homeassistant.components.recorder.db_schema.MAX_STATE_ATTRS_BYTES


def read(*vigilances: dict[str, Any], sources: list[dict[str, Any]] | None = None, **kwargs: Any):
    return parse_response(response(list(vigilances), sources, **kwargs))


# ---------------------------------------------------------------- unknown is not none


def test_a_place_that_a_source_answers_for_with_nothing_found_is_none() -> None:
    assert read().max_level == "none"


def test_no_source_covering_the_place_is_unknown_not_none() -> None:
    data = read(sources=[], note="No official warning source covers this point.")
    assert data.max_level is None
    assert not data.has_data


def test_every_covering_source_unavailable_is_unknown_not_none() -> None:
    data = read(sources=[coverage(status="unavailable"), coverage("vigicrues", status="unavailable")])
    assert data.max_level is None


def test_one_source_down_and_the_other_answering_with_nothing_is_none() -> None:
    """The spec: only "every covering source unavailable" is unknown. The sources' statuses stay visible in the attributes."""
    data = read(sources=[coverage(status="ok"), coverage("vigicrues", status="unavailable")])
    assert data.max_level == "none"
    assert [item["status"] for item in build_attributes(data, "en")["sources"]] == ["ok", "unavailable"]


def test_a_stale_answer_still_counts_as_an_answer() -> None:
    assert read(sources=[coverage(status="stale")]).max_level == "none"


def test_an_unknown_coverage_status_is_not_taken_for_an_answer() -> None:
    assert read(sources=[coverage(status="something-new")]).max_level is None


def test_warnings_that_cannot_be_placed_on_a_map_forbid_saying_none() -> None:
    """Italy, Spain... list warnings by codes with no shape: an empty list is not "nothing to fear" (README, "Official warnings")."""
    data = read(sources=[coverage(not_matchable=3)])
    assert data.not_matchable == 3
    assert data.max_level is None


def test_a_found_level_is_kept_even_when_others_cannot_be_placed() -> None:
    data = read(vigilance(level="ORANGE"), sources=[coverage(not_matchable=3)])
    assert data.max_level == "orange"  # a lower bound, shown as is


def test_a_warning_without_a_readable_level_is_not_none() -> None:
    data = read(vigilance(level=None))
    assert data.max_level is None
    assert len(data.vigilances) == 1


def test_a_warning_without_a_level_does_not_hide_a_known_one() -> None:
    assert read(vigilance(level=None), vigilance("b", level="RED")).max_level == "red"


def test_green_alone_is_none() -> None:
    assert read(vigilance(level="GREEN")).max_level == "none"


@pytest.mark.parametrize("level", ["YELLOW", "ORANGE", "RED"])
def test_the_maximum_is_the_highest_level(level: str) -> None:
    data = read(vigilance("a", level="YELLOW"), vigilance("b", level=level), vigilance("c", level="GREEN"))
    assert data.max_level == level.lower()


def test_the_level_of_each_phenomenon() -> None:
    data = read(
        vigilance("a", level="YELLOW", phenomenon="wind"),
        vigilance("b", level="ORANGE", phenomenon="wind"),
        vigilance("c", level="YELLOW", phenomenon="rain"),
        vigilance("d", level="GREEN", phenomenon="fog"),
        vigilance("e", level="RED", phenomenon=None, original=None),
    )
    assert data.by_phenomenon == {"fog": "none", "rain": "yellow", "wind": "orange"}  # no phenomenon, no key: nothing is invented


def test_the_level_of_each_family() -> None:
    data = read(
        vigilance("a", level="YELLOW", phenomenon="rain"),
        vigilance("b", level="ORANGE", phenomenon="flood"),
        vigilance("c", level="YELLOW", phenomenon="avalanche"),
        vigilance("d", level="GREEN", phenomenon="fog"),
        vigilance("e", level="RED", phenomenon=None, original=None),
        vigilance("f", level=None, phenomenon="wind"),
    )
    # rain and flood are one family; fog is its own; a warning with no phenomenon is "other", as in the API; one without a
    # level is not counted
    assert data.by_family == {"fog": "none", "other": "red", "rain-flood": "orange", "snow-ice": "yellow"}


# ---------------------------------------------------------------- reading the answer


def test_the_real_answer_is_read() -> None:
    from .conftest import FIXTURES

    data = parse_response(json.loads((FIXTURES / "vigilances_haute_corse.json").read_text(encoding="utf-8")))
    assert len(data.vigilances) == 7
    assert data.max_level == "red"
    assert data.by_phenomenon == {"flood": "red", "thunderstorm": "yellow", "wind": "yellow"}
    assert data.by_family == {"rain-flood": "red", "thunderstorm": "yellow", "wind": "yellow"}
    assert data.skipped == 0
    assert data.attribution and "MeteoAlarm" in data.attribution


def test_cancelled_and_expired_warnings_are_not_active() -> None:
    data = read(
        vigilance("cancelled", status="cancel", level="RED"),
        vigilance("expired", expires=SERVER_UTC - 1, level="RED"),
        vigilance("ends-first", expires=SERVER_UTC + 9999, ends=SERVER_UTC - 5, level="RED"),  # the later of the two counts, as in the API
        vigilance("later-end", expires=SERVER_UTC - 5, ends=SERVER_UTC + 50, level="ORANGE"),
        vigilance("no-end", expires=None, level="YELLOW"),
    )
    assert [v.id for v in data.vigilances] == ["ends-first", "later-end", "no-end"]
    assert data.max_level == "red"


def test_an_item_without_id_or_source_is_skipped_and_counted() -> None:
    broken = vigilance()
    del broken["id"]
    data = read(broken, "not a dict", {"id": "x"}, vigilance("ok"))  # type: ignore[arg-type]
    assert [v.id for v in data.vigilances] == ["ok"]
    assert data.skipped == 3
    assert build_attributes(data, "en")["unreadable"] == 3


@pytest.mark.parametrize("payload", [None, [], {}, {"vigilances": {}}, {"vigilances": []}, {"vigilances": [], "server_utc": "x"}])
def test_an_answer_that_is_not_one_is_refused(payload: Any) -> None:
    with pytest.raises(ValueError):
        parse_response(payload)


def test_unknown_fields_and_levels_are_tolerated() -> None:
    raw = vigilance(level="PURPLE")
    raw["something_new"] = {"a": 1}
    data = read(raw)
    assert data.vigilances[0].level is None


def test_parse_vigilance_keeps_what_the_source_gave() -> None:
    parsed = parse_vigilance(vigilance(distance_km=3.93, level="ORANGE"))
    assert parsed is not None and parsed.level == "orange" and parsed.distance_km == 3.93
    assert parsed.geocodes == (("NUTS3", "FR832"),)


def test_level_rank() -> None:
    assert [level_rank(level) for level in ("green", "yellow", "orange", "red", None, "purple")] == [0, 1, 2, 3, -1, -1]


# ---------------------------------------------------------------- the words of the issuer


def test_the_headline_is_in_the_language_of_home_assistant_else_english_else_the_first() -> None:
    v = parse_vigilance(vigilance(texts=[{"language": "de-DE", "headline": "Warnung"}, {"language": "en-GB", "headline": "Warning"}, {"language": "fr-FR", "headline": "Vigilance"}]))
    assert v is not None
    assert v.headline("fr") == "Vigilance"
    assert v.headline("fr-FR") == "Vigilance"
    assert v.headline("en") == "Warning"
    assert v.headline("pl") == "Warning"  # no Polish text: English
    only_german = parse_vigilance(vigilance(texts=[{"language": "de-DE", "headline": "Warnung"}]))
    assert only_german is not None and only_german.headline("fr") == "Warnung"  # the issuer's own words, never a translation


def test_without_texts_the_event_of_the_listing_is_used() -> None:
    v = parse_vigilance(vigilance(texts=[], event="Moderate wind warning"))
    assert v is not None and v.headline("fr") == "Moderate wind warning"


def test_a_long_headline_is_cut_for_display_only() -> None:
    v = parse_vigilance(vigilance(texts=[{"language": "en", "headline": "x" * 1000}]))
    assert v is not None and len(v.headline("en") or "") == 300 and (v.headline("en") or "").endswith("…")


# ---------------------------------------------------------------- the changes


def tracked(*vigilances: dict[str, Any], sources: list[dict[str, Any]] | None = None):
    return read(*vigilances, sources=sources)


def test_the_first_answer_is_the_baseline_and_changes_nothing() -> None:
    tracker = ChangeTracker()
    assert tracker.update(tracked(vigilance(level="RED"))) == ()


def test_a_warning_that_appears_is_new() -> None:
    tracker = ChangeTracker()
    tracker.update(tracked())
    (change,) = tracker.update(tracked(vigilance("a", level="YELLOW")))
    assert (change.kind, change.vigilance.id) == ("new", "a")


def test_a_warning_that_goes_up_is_upgraded_even_when_the_source_re_issues_it_under_a_new_id() -> None:
    tracker = ChangeTracker()
    tracker.update(tracked(vigilance("doc-1", level="YELLOW")))
    (change,) = tracker.update(tracked(vigilance("doc-2", level="ORANGE")))  # same phenomenon, same area: the same warning
    assert (change.kind, change.vigilance.id, change.previous_level) == ("upgraded", "doc-2", "yellow")
    assert change.event_data("en")["previous_level"] == "yellow"


def test_a_warning_that_goes_down_or_stays_is_silent() -> None:
    tracker = ChangeTracker()
    tracker.update(tracked(vigilance("a", level="ORANGE")))
    assert tracker.update(tracked(vigilance("a", level="ORANGE"))) == ()
    assert tracker.update(tracked(vigilance("b", level="YELLOW"))) == ()


def test_a_warning_that_disappears_is_ended() -> None:
    tracker = ChangeTracker()
    tracker.update(tracked(vigilance("a", level="RED")))
    (change,) = tracker.update(tracked())
    assert (change.kind, change.vigilance.level) == ("ended", "red")


def test_a_warning_that_expires_or_is_cancelled_is_ended() -> None:
    tracker = ChangeTracker()
    tracker.update(tracked(vigilance("a", level="RED"), vigilance("b", phenomenon="rain", original="Rain")))
    changes = tracker.update(tracked(vigilance("a", level="RED", status="cancel"), vigilance("b", phenomenon="rain", original="Rain", expires=SERVER_UTC - 1)))
    assert sorted((c.kind, c.vigilance.id) for c in changes) == [("ended", "a"), ("ended", "b")]


def test_two_warnings_of_the_same_kind_in_one_answer_are_one_warning_the_strongest() -> None:
    """The real answer has a yellow thunderstorm in force and another one tomorrow, for the same area."""
    tracker = ChangeTracker()
    now = vigilance("now", level="YELLOW", onset=SERVER_UTC - 100)
    tomorrow = vigilance("tomorrow", level="ORANGE", onset=SERVER_UTC + 9000)
    assert tracker.update(tracked(now, tomorrow)) == ()
    assert tracker.update(tracked(now)) == ()  # the orange one is gone, the yellow one stays: the same warning, lower, silent


def test_rain_flood_and_flooding_are_two_warnings() -> None:
    tracker = ChangeTracker()
    red = vigilance("rain", level="RED", phenomenon="flood", original="Rain-flood")
    yellow = vigilance("flood", level="YELLOW", phenomenon="flood", original="Flooding")
    tracker.update(tracked(red, yellow))
    (change,) = tracker.update(tracked(yellow))
    assert (change.kind, change.vigilance.id) == ("ended", "rain")  # the red one ended; the yellow one does not hide it


def test_a_source_that_does_not_answer_ends_nothing() -> None:
    tracker = ChangeTracker()
    flood = vigilance("river", source="vigicrues", level="ORANGE", phenomenon="flood", original="1", codes=(("VIGICRUES_SECTION", "GA32"),))
    wind = vigilance("wind", level="YELLOW")
    both = [coverage(), coverage("vigicrues")]
    tracker.update(tracked(flood, wind, sources=both))
    assert tracker.update(tracked(wind, sources=[coverage(), coverage("vigicrues", status="unavailable")])) == ()
    (change,) = tracker.update(tracked(wind, sources=both))  # it answers again, the river is gone: now it ends
    assert (change.kind, change.vigilance.id) == ("ended", "river")


def test_a_source_that_answers_for_the_first_time_sets_its_own_baseline() -> None:
    tracker = ChangeTracker()
    tracker.update(tracked(sources=[coverage(), coverage("vigicrues", status="unavailable")]))
    flood = vigilance("river", source="vigicrues", level="ORANGE", phenomenon="flood", original="1", codes=(("VIGICRUES_SECTION", "GA32"),))
    assert tracker.update(tracked(flood, sources=[coverage(), coverage("vigicrues")])) == ()  # already in force: not "new"
    (change,) = tracker.update(tracked(flood, vigilance("wind"), sources=[coverage(), coverage("vigicrues")]))
    assert change.kind == "new"


def test_event_data_is_a_small_summary() -> None:
    tracker = ChangeTracker()
    tracker.update(tracked())
    (change,) = tracker.update(tracked(vigilance("a", level="RED", distance_km=2.5)))
    data = change.event_data("fr")
    assert data["level"] == "red" and data["phenomenon"] == "wind" and data["headline"] == "Vigilance wind"
    assert data["source"] == "meteoalarm" and data["distance_km"] == 2.5 and data["area"] == "Haute-Corse"
    assert data["onset"].endswith("+00:00") and "previous_level" not in data
    assert len(json.dumps(data)) < 1000  # no long text: the API's own answer has them


# ---------------------------------------------------------------- the bounded attributes


def test_the_list_is_cut_to_the_cap_and_says_how_many_it_left_out() -> None:
    many = [vigilance(f"w{i}", area=f"Area {i}", codes=(("NUTS3", f"X{i}"),)) for i in range(50)]
    attributes = build_attributes(read(*many), "en")
    assert attributes["count"] == 50
    assert len(attributes["vigilances"]) == MAX_LISTED_VIGILANCES
    assert attributes["omitted"] == 30


def test_the_list_is_cut_further_when_the_text_would_pass_the_budget_and_stays_under_the_recorder_limit() -> None:
    fat = [
        vigilance(f"w{i}", area=f"Area {i}", codes=(("NUTS3", f"X{i}"),), texts=[{"language": "en", "headline": "h" * 5000}])
        for i in range(30)
    ]
    attributes = build_attributes(read(*fat, disclaimer="d" * 5000, note="n" * 5000), "en")
    size = len(json.dumps(attributes, ensure_ascii=False, separators=(",", ":")).encode())
    assert size <= ATTRIBUTES_BUDGET_BYTES < RECORDER_LIMIT_BYTES
    assert attributes["count"] == 30
    assert len(attributes["vigilances"]) + attributes["omitted"] == 30
    assert attributes["omitted"] > 10  # the cap of the text, not only the cap of the count
    assert len(attributes["disclaimer"]) <= 500 and len(attributes["note"]) <= 500


def test_the_real_answer_fits_without_cutting() -> None:
    from .conftest import FIXTURES

    data = parse_response(json.loads((FIXTURES / "vigilances_haute_corse.json").read_text(encoding="utf-8")))
    attributes = build_attributes(data, "fr")
    assert attributes["omitted"] == 0 and len(attributes["vigilances"]) == 7
    assert attributes["vigilances"][0]["level"] == "red" and attributes["vigilances"][0]["headline"] == "Vigilance rouge pluie-inondation"
    assert len(json.dumps(attributes, ensure_ascii=False)) < 6000


def test_the_attributes_leave_out_what_the_source_did_not_give() -> None:
    attributes = build_attributes(read(vigilance(expires=None, onset=None, area=None)), "en")
    item = attributes["vigilances"][0]
    assert "onset" not in item and "expires" not in item and "area" not in item and "distance_km" not in item


# ---------------------------------------------------------------- the address of the instance


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://vigie.local:8080", "http://vigie.local:8080"),
        ("  vigie.local:8080/ ", "http://vigie.local:8080"),
        ("https://example.invalid/vigie/", "https://example.invalid/vigie"),
        ("HTTP://Vigie.Local", "http://vigie.local"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "ftp://vigie.local", "http://", "http://user:pass@vigie.local", "http://vigie.local/?a=b", "http://vigie.local/#x"])
def test_normalize_url_refuses(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_url(raw)
