"""The recorded answers of the tsunami warning centres and of the National Hurricane Center, as the tests replay them.

Everything here is a real recording (see the README of ``tests/fixtures/tsunami`` and ``tests/fixtures/nhc``); nothing is
made up. Two things are built from them:

- the list of the latest tsunami bulletins **as it was at a given moment** (``tsunami_listing_at``): the recorded list of
  2026-10-20 holds 40 bulletins, from 2026-09-28 to 2026-10-20, with the full Panama earthquake of 2026-10-09 in it; cut at
  the time a test plays, it grows as the bulletins were issued, and its ``Last-Modified`` moves with it;
- the documents behind it (``tsunami_document``): the CAP message and the text bulletin of a bulletin, when they were recorded.

The NHC side is the file of the active storms of 2026-10-09 (``isaias_only``: Hurricane Isaias, over the Florida
Panhandle) and the ZIP of shapefiles of its advisory 13A.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

TSUNAMI = Path(__file__).resolve().parent / "fixtures" / "tsunami"
NHC = Path(__file__).resolve().parent / "fixtures" / "nhc"

TSUNAMI_LISTING_PATH = "/events/js/previous.js"
TSUNAMI_BULLETIN = re.compile(r"^/events/(PHEB|PAAQ)/\d{4}/\d{2}/\d{2}/([A-Za-z0-9]+)/(\d+)/([A-Z]{4}\d{2})/(?:\1CAP\.xml|\4\.txt)$")
_CREATED = re.compile(r"'createdDB'\s*:\s*'(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})")

# The Panama earthquake of 2026-10-09, magnitude 7.6 at 17:56:08 UTC, and the first message of the Pacific centre.
PANAMA_QUAKE_UTC = datetime(2026, 10, 9, 17, 56, 8, tzinfo=timezone.utc).timestamp()


def at(day: str, clock: str) -> float:
    """``at('2026-10-09', '18:10')`` or ``at('2026-10-09', '18:10:30')``: a UTC instant as an epoch time."""
    return datetime.fromisoformat(f"{day}T{clock}{'' if clock.count(':') == 2 else ':00'}+00:00").timestamp()


def tsunami_fixture(name: str) -> bytes:
    return (TSUNAMI / name).read_bytes()


def nhc_fixture(name: str) -> bytes:
    return (NHC / name).read_bytes()


def tsunami_listing_at(now: float) -> tuple[bytes, float]:
    """The recorded list of the latest bulletins as it was at ``now`` (UTC epoch), and when it was last modified (0 if empty).

    An entry is in the list once its ``createdDB`` time has passed; the list is modified 3 seconds after the last one
    (2026-10-20: created 10:45:23, list modified 10:45:26).
    """
    text = (TSUNAMI / "previous.js").read_text(encoding="utf-8")
    first, last = text.index("{", text.index("[")), text.rindex("}")
    kept: list[str] = []
    modified = 0.0
    for entry in re.findall(r"\{[^{}]*\}", text[first : last + 1]):
        found = _CREATED.search(entry)
        assert found is not None
        created = datetime(*(int(part) for part in found.groups()), tzinfo=timezone.utc).timestamp()
        if created <= now:
            kept.append(entry)
            modified = max(modified, created + 3)
    return (text[:first] + ",\n".join(kept) + text[last + 1 :]).encode(), modified


def tsunami_document(path: str) -> Optional[bytes]:
    """The recorded CAP message or text bulletin a path of tsunami.gov names, or ``None`` when it was not recorded."""
    found = TSUNAMI_BULLETIN.match(path)
    if not found:
        return None
    twc, event, number, wmo = found.groups()
    name = f"{twc}_{event}_{number}_{wmo}" + ("_CAP.xml" if path.endswith("CAP.xml") else ".txt")
    recorded = TSUNAMI / name
    return recorded.read_bytes() if recorded.exists() else None


def current_storms(name: str = "CurrentStorms_2026-10-09-2358.json") -> dict:
    return json.loads(nhc_fixture(name))


def isaias_only() -> bytes:
    """The file of the active storms of 2026-10-09 23:58 UTC with only Hurricane Isaias (the one whose bundle was recorded)."""
    payload = current_storms()
    payload["activeStorms"] = [storm for storm in payload["activeStorms"] if storm["id"] == "al092026"]
    return json.dumps(payload).encode()
