"""Two sensors in the format of Home Assistant's own Météo-France integration, for the dashboard cards of others (ADR 0046).

Cards written for that integration (the Météo-France weather card of HACF, Weather Plus Card and others) look for a "next rain"
sensor with a ``1_hour_forecast`` attribute and a "weather alert" sensor with one attribute per phenomenon. They read the words
of Météo-France, not values: ``"Pluie faible"``, ``"Vent violent": "Jaune"``. So these two sensors carry those words exactly,
in French whatever the language of Home Assistant, as Météo-France's integration does. The names a card shows are its own.

Nothing is made up here either: a step the API did not give leaves the forecast out, a warning without a level or with a
phenomenon Météo-France has no word for adds no attribute (it still counts in the state).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from .models import LEVEL_ORDER, Vigilance, level_rank

# The words of Météo-France's "rain in the hour", from the rate (mm/h). The bounds are the gateway's (vigie-meteo,
# ``rain_slice_from_mm_5min`` and its Open-Meteo fallback), so a rate gets the same word there and here.
DRY = "Temps sec"
RAIN_WORDS: tuple[tuple[float, str], ...] = ((0.2, DRY), (1.6, "Pluie faible"), (6.0, "Pluie modérée"))
HEAVY = "Pluie forte"

# The states of the "weather alert" sensor: the ones of Météo-France's integration (an enum, translated by Home Assistant).
ALERT_STATES = ("green", "yellow", "orange", "red")
# The colour words of its attributes, by level.
COLOUR_WORDS = {"green": "Vert", "yellow": "Jaune", "orange": "Orange", "red": "Rouge"}
# Météo-France's nine phenomena, by the API's group. "flood" is split by the issuer's own word: a warning for rain that floods
# is Météo-France's "Pluie-inondation", a river flood its "Inondation". Fire, fog and the rest have no word there.
PHENOMENON_WORDS = {
    "wind": "Vent violent",
    "rain": "Pluie-inondation",
    "thunderstorm": "Orages",
    "flood": "Inondation",
    "snow-ice": "Neige-verglas",
    "heat": "Canicule",
    "cold": "Grand-froid",
    "avalanche": "Avalanches",
    "coastal": "Vagues-submersion",
}
RAIN_FLOOD = "Pluie-inondation"


def rain_word(mm_h: float) -> str:
    """Météo-France's word for a rain rate (mm/h)."""
    for bound, word in RAIN_WORDS:
        if mm_h < bound:
            return word
    return HEAVY


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def one_hour_forecast(point: dict[str, Any] | None, slots: list[dict[str, Any]] | None) -> tuple[int, dict[str, str]] | None:
    """The start of the hour (UTC epoch seconds) and Météo-France's word for each part of it, keyed ``"<minutes> min"`` from
    that start.

    In metropolitan France the nine slots of the former Météo-France API (``slots``, from PIAF: 5 minutes for the first half
    hour, then 10). Elsewhere the four 15-minute steps of the answer's nowcast (a forecast model there, not a radar), whose
    first is a quarter-hour already under way or over: the start says so, and the cards count from it. ``None`` when a slot or
    a step is unknown: never a dry hour made up.
    """
    if slots:
        first = datetime.fromisoformat(slots[0]["start"])
        forecast: dict[str, str] = {}
        for slot in slots:
            offset = int((datetime.fromisoformat(slot["start"]) - first).total_seconds()) // 60
            forecast[f"{offset} min"] = rain_word(slot["mm_h"])
        return int(first.timestamp()), forecast
    nowcast = point.get("rain_nowcast") if isinstance(point, dict) else None
    if not isinstance(nowcast, dict):
        return None
    steps = nowcast.get("mm_per_step")
    interval = _number(nowcast.get("interval_minutes"))
    start = _number(nowcast.get("start_utc"))
    if not isinstance(steps, list) or not steps or not interval or interval <= 0 or start is None:
        return None
    values = [_number(step) for step in steps]
    if any(value is None for value in values):
        return None
    return int(start), {
        f"{int(index * interval)} min": rain_word(value * 60 / interval)  # type: ignore[operator]
        for index, value in enumerate(values)
    }


def next_rain_at(start_utc: int, forecast: dict[str, str]) -> datetime | None:
    """The start of the first part of the hour that is not dry, as Météo-France's integration gives it; ``None`` (unknown)
    when the whole hour is dry."""
    for key, word in forecast.items():
        if word != DRY:
            return datetime.fromtimestamp(start_utc + int(key.split()[0]) * 60, tz=UTC)
    return None


def alert_state(max_level: str | None) -> str | None:
    """The state of the "weather alert" sensor from the highest level of the place: ``green`` when a source answered and found
    nothing, ``None`` (unknown) when it cannot be told."""
    if max_level is None:
        return None
    return max_level if max_level in ALERT_STATES else "green"


def phenomenon_word(vigilance: Vigilance) -> str | None:
    """Météo-France's word for the phenomenon of a warning, ``None`` when it has none."""
    word = PHENOMENON_WORDS.get(vigilance.phenomenon or "")
    if word == "Inondation" and any(key in (vigilance.phenomenon_original or "").lower() for key in ("rain", "pluie")):
        return RAIN_FLOOD
    return word


def alert_attributes(vigilances: tuple[Vigilance, ...]) -> dict[str, str]:
    """One attribute per phenomenon of the warnings in force or announced, Météo-France's word for it and the colour word of its
    highest level: ``{"Vent violent": "Jaune", "Inondation": "Rouge"}``. A warning without a level is left out."""
    best: dict[str, int] = {}
    for vigilance in vigilances:
        word = phenomenon_word(vigilance)
        rank = level_rank(vigilance.level)
        if word is not None and rank >= 0:
            best[word] = max(best.get(word, -1), rank)
    return {word: COLOUR_WORDS[LEVEL_ORDER[rank]] for word, rank in sorted(best.items())}
