"""The answer of ``GET /v1/experimental/prealert``: the stored levels of the province a position is in (ADR 0011, amendment)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.attribution import PREALERT, credit_text
from app.models.prealert import PrealertDay, PrealertPhenomenon, PrealertResponse
from app.prealert.gfs import LOCAL_OFFSET
from app.prealert.levels import LEVEL_WORDS, PHENOMENA, frozen_thresholds
from app.prealert.provinces import province_at
from app.prealert.shadow import ShadowStore

NOTICE = (
    "EXPERIMENTAL and UNOFFICIAL: a test of Vigie, being improved. These levels are computed by Vigie from the ECMWF forecast with "
    "thresholds of its own; they are not the warnings of the Turkish State Meteorological Service (MGM) and must not be taken for "
    "them. Follow the official warnings."
)
QUALITY = (
    "Measured on 2026-10-25 against the orange warnings the press reported: with the frozen thresholds it reached level 2 on the day "
    "for 63 % of the warned provinces of 2026 (71 % the day before), and it gives level 2 on about 4 % of the province-days. It "
    "missed every wind warning of the test. See ADR 0011 of the Vigie API."
)
OFFICIAL_WARNINGS_URL = "https://www.mgm.gov.tr/meteouyari/turkiye.aspx"
UNITS = {"rain": "mm in 24 h", "wind": "km/h (strongest gust)", "snow": "mm of water in 24 h", "heat": "degC (maximum)", "cold": "degC (minimum)"}


class OutsideTurkey(ValueError):
    """The position is in no province of Turkey."""


def prealert_at(store: ShadowStore, lat: float, lon: float, now: Optional[datetime] = None) -> PrealertResponse:
    """The pre-alert of the province at ``(lat, lon)``: the days of the newest run from today (local) on."""
    found = province_at(lat, lon)
    if found is None:
        raise OutsideTurkey("the position is not in a province of Turkey: the experimental pre-alert covers Turkey only")
    code, name = found
    run, rows = store.latest_for(code)
    today = ((now or datetime.now(timezone.utc)) + LOCAL_OFFSET).date().isoformat()
    table = frozen_thresholds().get(code, {})
    by_day: dict[str, dict[str, PrealertPhenomenon]] = {}
    for row in rows:
        if row.day < today or row.phenomenon not in PHENOMENA:
            continue
        threshold = table.get(row.phenomenon)  # type: ignore[call-overload]
        by_day.setdefault(row.day, {})[row.phenomenon] = PrealertPhenomenon(
            value=None if row.value is None else round(row.value, 1),
            unit=UNITS[row.phenomenon],
            level=row.level,
            level_word=None if row.level is None else LEVEL_WORDS[row.level],  # type: ignore[arg-type]
            thresholds=None if threshold is None else [round(v, 1) for v in threshold.levels],
        )
    days = []
    for day in sorted(by_day):
        levels = [p.level for p in by_day[day].values() if p.level is not None]
        days.append(PrealertDay(day=day, max_level=max(levels) if levels else None, phenomena=by_day[day]))
    return PrealertResponse(
        notice=NOTICE,
        province_code=code,
        province_name=name,
        run_utc=run,
        days=days,
        quality=QUALITY,
        official_warnings_url=OFFICIAL_WARNINGS_URL,
        attribution=credit_text(*PREALERT),
    )

