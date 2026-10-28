"""The answer of ``GET /v1/vigilances`` as the integration reads it, and the rules that turn it into states.

Nothing here needs Home Assistant, so the rules can be read (and tested) on their own.

The principle is the API's own (``docs/decisions/0001``): **a value nobody supplied is unknown, never a plausible
default.** The maximum level is ``None`` (unknown) when no source covers the place, when every covering source is
unavailable, when a warning exists that cannot be graded, and when warnings exist in a feed that cannot be matched
to a point. Only a place that a source answers for, with nothing found and nothing hidden, is ``none``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .const import (
    ATTRIBUTES_BUDGET_BYTES,
    EVENT_ENDED,
    EVENT_NEW,
    EVENT_UPGRADED,
    MAX_LISTED_VIGILANCES,
    MAX_TEXT_CHARS,
    STATE_NONE,
)

# The levels of the API, lowest first (`GREEN` is "a source says nothing to report").
LEVEL_ORDER = ("green", "yellow", "orange", "red")
COVERAGE_STATUSES = ("ok", "stale", "unavailable")
# The family of each phenomenon of the API (``GET /v1/families``, ADR 0012); a phenomenon this table does not know is ``other``, and a
# message of the tsunami warning centres is ``tsunami`` whatever its phenomenon. A test of the API keeps the table equal to the API's.
FAMILY_OF_PHENOMENON = {
    "wind": "wind",
    "rain": "rain-flood",
    "flood": "rain-flood",
    "thunderstorm": "thunderstorm",
    "snow-ice": "snow-ice",
    "avalanche": "snow-ice",
    "heat": "heat",
    "cold": "cold",
    "fire": "fire",
    "coastal": "coastal",
    "fog": "other",
    "other": "other",
}


def level_rank(level: str | None) -> int:
    """Rank of a level (0 green ... 3 red); -1 for an unknown one, which is below every known level."""
    return LEVEL_ORDER.index(level) if level in LEVEL_ORDER else -1


def _text(value: Any) -> str | None:
    """A non-empty string, stripped, or ``None``."""
    return value.strip() or None if isinstance(value, str) else None


def _int(value: Any) -> int | None:
    """An integer (a bool is not one), or ``None``."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(value: Any) -> float | None:
    """A number (a bool is not one), or ``None``."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _iso(timestamp: int | None) -> str | None:
    """A UTC epoch as an ISO 8601 string, or ``None``."""
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(timestamp, UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _shorten(value: str | None, limit: int = MAX_TEXT_CHARS) -> str | None:
    """A text cut to ``limit`` characters with an ellipsis (display only: the source's words stay in the API)."""
    if value is None or len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class WarningText:
    """The words of the issuer in one language (the long texts are left to the API's own answer)."""

    language: str | None
    headline: str | None
    event: str | None


@dataclass(frozen=True, slots=True)
class Vigilance:
    """One official warning, as far as the integration needs it."""

    id: str
    source: str
    country: str | None
    level: str | None  # "green" | "yellow" | "orange" | "red" | None (no table covers the source's word)
    phenomenon: str | None  # the API's own group ("wind", "rain", "snow-ice"...) or None
    phenomenon_original: str | None
    event: str | None
    status: str | None
    sent_utc: int | None
    onset_utc: int | None
    expires_utc: int | None
    ends_utc: int | None
    issuer: str | None
    area: str | None
    geocodes: tuple[tuple[str, str], ...]
    texts: tuple[WarningText, ...]
    web: str | None
    link: str | None
    distance_km: float | None

    @property
    def track_key(self) -> str:
        """What identifies "the same warning" from one answer to the next: source, country, phenomenon and area codes.

        Close to the key of the API's own push (``app/services/vigilance.py``, ``track_key``), with the source's own
        word for the phenomenon added: MeteoAlarm's "rain-flood" and "flooding" are both the group `flood`, and the
        end of a red rain-flood warning must not hide behind a yellow flooding one. The ``id`` cannot be the key:
        MeteoAlarm re-issues a warning as a new document with a new identifier when its level changes, so an upgrade
        would look like one warning ending and another one starting.
        """
        codes = ",".join(sorted(f"{scheme}:{value}" for scheme, value in self.geocodes))
        if not codes:
            codes = f"area:{self.area or self.id}"
        what = f"{self.phenomenon or '?'}/{(self.phenomenon_original or '?').lower()}"
        return f"{self.source}|{self.country}|{what}|{codes}"

    def is_active(self, now_utc: int) -> bool:
        """Not cancelled, and not past its end (the later of ``ends`` and ``expires``), by the server's clock."""
        if self.status == "cancel":
            return False
        ends = [t for t in (self.ends_utc, self.expires_utc) if t is not None]
        return not ends or max(ends) >= now_utc

    def best_text(self, language: str) -> WarningText | None:
        """The text in the language asked for, else English, else the first one the issuer gave."""
        if not self.texts:
            return None
        wanted = language.lower()
        primary = wanted.split("-")[0]
        tags = [(text, (text.language or "").lower()) for text in self.texts]
        for accept in (
            lambda tag: tag == wanted,
            lambda tag: tag.split("-")[0] == primary,
            lambda tag: tag.split("-")[0] == "en",
        ):
            for text, tag in tags:
                if tag and accept(tag):
                    return text
        return self.texts[0]

    def headline(self, language: str) -> str | None:
        """What to call the warning: the issuer's headline (or event) in the best language, else the source's event."""
        text = self.best_text(language)
        return _shorten((text.headline or text.event) if text else None) or _shorten(self.event)

    def summary(self, language: str) -> dict[str, Any]:
        """A small, JSON-ready description (an item of the sensor's list, the data of an event). Absent values are left out."""
        item: dict[str, Any] = {
            "id": self.id,
            "source": self.source,
            "country": self.country,
            "level": self.level,
            "phenomenon": self.phenomenon,
            "headline": self.headline(language),
            "event": _shorten(self.event),
            "area": _shorten(self.area, 120),
            "onset": _iso(self.onset_utc),
            "expires": _iso(self.expires_utc),
            "issuer": self.issuer,
            "web": self.web,
            "distance_km": self.distance_km,
        }
        if item["event"] == item["headline"]:
            del item["event"]
        return {key: value for key, value in item.items() if value is not None}


def parse_vigilance(raw: Any) -> Vigilance | None:
    """One warning of the answer; ``None`` when it lacks its identifier or its source (it is skipped, and counted)."""
    if not isinstance(raw, dict):
        return None
    identifier, source = _text(raw.get("id")), _text(raw.get("source"))
    if identifier is None or source is None:
        return None
    level = (_text(raw.get("level")) or "").lower()
    area = raw.get("area") if isinstance(raw.get("area"), dict) else {}
    geocodes = tuple(
        (scheme, value)
        for code in area.get("geocodes") or []
        if isinstance(code, dict) and (scheme := _text(code.get("scheme"))) and (value := _text(code.get("value")))
    )
    texts = tuple(
        WarningText(_text(text.get("language")), _text(text.get("headline")), _text(text.get("event")))
        for text in raw.get("texts") or []
        if isinstance(text, dict)
    )
    return Vigilance(
        id=identifier,
        source=source,
        country=_text(raw.get("country")),
        level=level if level in LEVEL_ORDER else None,
        phenomenon=_text(raw.get("phenomenon")),
        phenomenon_original=_text(raw.get("phenomenon_original")),
        event=_text(raw.get("event")),
        status=_text(raw.get("status")),
        sent_utc=_int(raw.get("sent_utc")),
        onset_utc=_int(raw.get("onset_utc")),
        expires_utc=_int(raw.get("expires_utc")),
        ends_utc=_int(raw.get("ends_utc")),
        issuer=_text(raw.get("issuer")),
        area=_text(area.get("description")),
        geocodes=geocodes,
        texts=texts,
        web=_text(raw.get("web")),
        link=_text(raw.get("link")),
        distance_km=_number(raw.get("distance_km")),
    )


@dataclass(frozen=True, slots=True)
class Coverage:
    """What the API says of one source for the country of the place."""

    source: str
    country: str | None
    status: str  # "ok" | "stale" (the last answer is served) | "unavailable"
    warnings_not_matchable: int


def parse_coverage(raw: Any) -> Coverage | None:
    """One line of ``coverage``. A status the integration does not know counts as ``unavailable`` (no data is claimed)."""
    if not isinstance(raw, dict) or (source := _text(raw.get("source"))) is None:
        return None
    status = _text(raw.get("status"))
    return Coverage(
        source=source,
        country=_text(raw.get("country")),
        status=status if status in COVERAGE_STATUSES else "unavailable",
        warnings_not_matchable=max(0, _int(raw.get("warnings_not_matchable")) or 0),
    )


@dataclass(frozen=True, slots=True)
class Change:
    """A change between two answers: a warning that appeared, went up, or ended."""

    kind: str  # EVENT_NEW | EVENT_UPGRADED | EVENT_ENDED
    vigilance: Vigilance
    previous_level: str | None = None

    def event_data(self, language: str) -> dict[str, Any]:
        """The data of the event entity: the warning's summary (and, for an upgrade, the level it came from)."""
        data = self.vigilance.summary(language)
        if self.kind == EVENT_UPGRADED and self.previous_level is not None:
            data["previous_level"] = self.previous_level
        return data


@dataclass(frozen=True, slots=True)
class VigieData:
    """What one read of the instance gave, and the changes since the read before."""

    server_utc: int
    vigilances: tuple[Vigilance, ...]  # active (not cancelled, not past), in the API's order: highest level first
    coverage: tuple[Coverage, ...]
    attribution: str | None
    disclaimer: str | None
    note: str | None
    skipped: int = 0  # items of the answer that could not be read
    changes: tuple[Change, ...] = ()
    sequence: int = 0

    @property
    def has_data(self) -> bool:
        """A source answered (even with its last answer, `stale`) for this place."""
        return any(item.status != "unavailable" for item in self.coverage)

    @property
    def not_matchable(self) -> int:
        """Warnings the sources list for the country that the API cannot place on a map: the answer may hide some."""
        return sum(item.warnings_not_matchable for item in self.coverage if item.status != "unavailable")

    @property
    def max_level(self) -> str | None:
        """``yellow`` | ``orange`` | ``red`` | ``none``, or ``None`` when it cannot be told (see the module docstring)."""
        if not self.has_data:
            return None
        best = max((level_rank(v.level) for v in self.vigilances), default=-1)
        if best >= level_rank("yellow"):
            return LEVEL_ORDER[best]  # a lower bound when something could not be graded or placed
        if self.not_matchable > 0 or any(v.level is None for v in self.vigilances):
            return None
        return STATE_NONE

    @property
    def by_phenomenon(self) -> dict[str, str]:
        """The highest level of each phenomenon the warnings name (a warning with no phenomenon is not in it)."""
        best: dict[str, int] = {}
        for vigilance in self.vigilances:
            if vigilance.phenomenon is not None and vigilance.level is not None:
                best[vigilance.phenomenon] = max(best.get(vigilance.phenomenon, -1), level_rank(vigilance.level))
        return {phenomenon: (STATE_NONE if rank == 0 else LEVEL_ORDER[rank]) for phenomenon, rank in sorted(best.items())}

    @property
    def by_family(self) -> dict[str, str]:
        """The highest level of each family of phenomena of the API the warnings fall in (a warning without a level is not counted)."""
        best: dict[str, int] = {}
        for vigilance in self.vigilances:
            if vigilance.level is None:
                continue
            if vigilance.source == "tsunami":
                family = "tsunami"
            else:
                family = FAMILY_OF_PHENOMENON.get(vigilance.phenomenon or "other", "other")
            best[family] = max(best.get(family, -1), level_rank(vigilance.level))
        return {family: (STATE_NONE if rank == 0 else LEVEL_ORDER[rank]) for family, rank in sorted(best.items())}


def parse_response(payload: Any) -> VigieData:
    """The answer of ``GET /v1/vigilances``. Raises ``ValueError`` when it is not one (the caller treats it as a failed read)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("vigilances"), list):
        raise ValueError("not an answer of /v1/vigilances")
    server_utc = _int(payload.get("server_utc"))
    if server_utc is None:
        raise ValueError("the answer has no server_utc")
    parsed = [parse_vigilance(raw) for raw in payload["vigilances"]]
    vigilances = tuple(v for v in parsed if v is not None and v.is_active(server_utc))
    coverage = tuple(c for raw in payload.get("coverage") or [] if (c := parse_coverage(raw)) is not None)
    return VigieData(
        server_utc=server_utc,
        vigilances=vigilances,
        coverage=coverage,
        attribution=_text(payload.get("attribution")),
        disclaimer=_text(payload.get("disclaimer")),
        note=_text(payload.get("note")),
        skipped=parsed.count(None),
    )


def keep_the_strongest(vigilances: tuple[Vigilance, ...]) -> dict[str, Vigilance]:
    """One warning per ``track_key``: the highest level, then the earliest onset (as the API does for its pushes)."""
    kept: dict[str, Vigilance] = {}
    for vigilance in vigilances:
        key = vigilance.track_key
        current = kept.get(key)
        if current is None or (level_rank(vigilance.level), -(vigilance.onset_utc or 0)) > (
            level_rank(current.level),
            -(current.onset_utc or 0),
        ):
            kept[key] = vigilance
    return kept


class ChangeTracker:
    """Compares each answer with the one before and says what appeared, went up or ended.

    The rules are the API's own for its pushes (``VigilanceService.poll_changes``):

    - the first answer only sets the baseline: nothing is reported for what was already in force;
    - a warning is "the same" by its ``track_key``; ``upgraded`` is a higher level, both known;
    - a source that does not answer ends nothing: what it said before stands until it answers again;
    - a source that answers for the first time after the baseline sets its own baseline.
    """

    def __init__(self) -> None:
        self._tracked: dict[str, Vigilance] | None = None
        self._answered: set[str] = set()

    def update(self, data: VigieData) -> tuple[Change, ...]:
        """Take a new answer and return its changes."""
        current = keep_the_strongest(data.vigilances)
        silent = {item.source for item in data.coverage if item.status == "unavailable"}
        answered = {item.source for item in data.coverage if item.status != "unavailable"}
        previous, known = self._tracked, self._answered
        if previous is not None and silent:
            current = {**{key: v for key, v in previous.items() if v.source in silent}, **current}
        self._tracked, self._answered = current, known | answered
        if previous is None:
            return ()
        changes: list[Change] = []
        for key, vigilance in current.items():
            before = previous.get(key)
            if before is None:
                if vigilance.source in known:
                    changes.append(Change(EVENT_NEW, vigilance))
            elif level_rank(vigilance.level) > level_rank(before.level) >= 0:
                changes.append(Change(EVENT_UPGRADED, vigilance, before.level))
        changes.extend(Change(EVENT_ENDED, vigilance) for key, vigilance in previous.items() if key not in current)
        return tuple(changes)


def _json_size(value: Any) -> int:
    """Bytes of the JSON the recorder would store for ``value``."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))


def build_attributes(
    data: VigieData,
    language: str,
    *,
    max_listed: int = MAX_LISTED_VIGILANCES,
    budget_bytes: int = ATTRIBUTES_BUDGET_BYTES,
) -> dict[str, Any]:
    """The attributes of the maximum-level sensor, bounded: the list of warnings is cut, and says how many it left out."""
    attributes: dict[str, Any] = {
        "count": len(data.vigilances),
        "by_phenomenon": data.by_phenomenon,
        "by_family": data.by_family,
        "sources": [
            {"source": item.source, "country": item.country, "status": item.status} for item in data.coverage
        ],
        "not_matchable": data.not_matchable,
        "server_time": _iso(data.server_utc),
    }
    if data.note:
        attributes["note"] = _shorten(data.note, 500)
    if data.disclaimer:
        attributes["disclaimer"] = _shorten(data.disclaimer, 500)
    if data.skipped:
        attributes["unreadable"] = data.skipped
    items = [vigilance.summary(language) for vigilance in data.vigilances]
    listed = items[:max_listed]
    while listed and _json_size({**attributes, "vigilances": listed, "omitted": len(items) - len(listed)}) > budget_bytes:
        listed.pop()
    attributes["vigilances"] = listed
    attributes["omitted"] = len(items) - len(listed)
    return attributes
