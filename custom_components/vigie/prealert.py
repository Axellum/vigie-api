"""The answer of ``GET /v1/experimental/prealert`` as the integration reads it: an experimental, unofficial pre-alert for Turkey.

**Not an official warning.** Levels computed by Vigie from the ECMWF forecast with thresholds of its own (ADR 0011 of the Vigie
API and its amendment), read only when the user ticks the option, which is off by default. Nothing here reads or changes the
warnings of ``models.py``; the state of its sensor is a word of Vigie's (``watch``, ``strong``, ``severe``), never an official
colour. Nothing here needs Home Assistant.

The principle is the same as for the warnings: **a value nobody supplied is unknown**. The level of a day is ``None`` when the
run gave no level for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import PREALERT_LEVEL_STATES
from .models import _int, _number, _text


@dataclass(frozen=True, slots=True)
class PrealertDay:
    """One local day of the province."""

    day: str
    max_level: int | None
    phenomena: dict[str, dict[str, Any]]

    @property
    def level_word(self) -> str | None:
        """``none``, ``watch``, ``strong`` or ``severe``; ``None`` when no phenomenon of the day has a level."""
        if self.max_level is None or not 0 <= self.max_level < len(PREALERT_LEVEL_STATES):
            return None
        return PREALERT_LEVEL_STATES[self.max_level]


@dataclass(frozen=True, slots=True)
class PrealertData:
    """What one read of ``/v1/experimental/prealert`` gave."""

    province_code: str
    province_name: str | None
    run_utc: str | None
    days: tuple[PrealertDay, ...]
    notice: str | None
    quality: str | None
    official_warnings_url: str | None
    attribution: str | None

    @property
    def today(self) -> PrealertDay | None:
        """The first day of the answer (the API gives the days from today on, local time of Turkey)."""
        return self.days[0] if self.days else None


def _phenomenon(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    item = {
        "value": _number(raw.get("value")),
        "unit": _text(raw.get("unit")),
        "level": _int(raw.get("level")),
        "level_word": _text(raw.get("level_word")),
    }
    thresholds = raw.get("thresholds")
    if isinstance(thresholds, list) and all(_number(v) is not None for v in thresholds):
        item["thresholds"] = [_number(v) for v in thresholds]
    return item


def parse_prealert(payload: Any) -> PrealertData:
    """Read the answer. ``ValueError`` when it is not one: not experimental, said to be official, or without a province."""
    if not isinstance(payload, dict):
        raise ValueError("the answer is not an object")
    if payload.get("experimental") is not True or payload.get("official") is not False:
        raise ValueError("the answer does not say it is experimental and unofficial")
    code = _text(payload.get("province_code"))
    if code is None:
        raise ValueError("the answer names no province")
    days = []
    for raw in payload.get("days") or []:
        if not isinstance(raw, dict) or _text(raw.get("day")) is None:
            continue
        phenomena = raw.get("phenomena")
        if not isinstance(phenomena, dict):
            phenomena = {}
        parsed = {name: item for name, value in phenomena.items() if (item := _phenomenon(value)) is not None}
        days.append(PrealertDay(day=str(raw["day"]), max_level=_int(raw.get("max_level")), phenomena=parsed))
    return PrealertData(
        province_code=code,
        province_name=_text(payload.get("province_name")),
        run_utc=_text(payload.get("run_utc")),
        days=tuple(days),
        notice=_text(payload.get("notice")),
        quality=_text(payload.get("quality")),
        official_warnings_url=_text(payload.get("official_warnings_url")),
        attribution=_text(payload.get("attribution")),
    )


def build_prealert_attributes(data: PrealertData) -> dict[str, Any]:
    """What the sensor shows besides its state: the notice first, the province, the run, each day and where the official
    warnings are. Absent values are left out."""
    attributes: dict[str, Any] = {
        "experimental": True,
        "official": False,
        "notice": data.notice,
        "province": data.province_name,
        "province_code": data.province_code,
        "run_utc": data.run_utc,
        "day": data.today.day if data.today else None,
        "days": [
            {"day": d.day, "level": d.level_word, "phenomena": {name: {k: v for k, v in p.items() if v is not None} for name, p in d.phenomena.items()}}
            for d in data.days
        ],
        "quality": data.quality,
        "official_warnings_url": data.official_warnings_url,
    }
    return {key: value for key, value in attributes.items() if value is not None}
