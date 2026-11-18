"""Call budget of the providers: the published quotas, and the sliding windows that enforce them.

Every collector counts the upstream requests it really sends (and, for Open-Meteo, their weight, see
``weights.py``) over a sliding window of 24 hours. A provider that publishes a quota also has a budget:
a request that would exceed it is **not sent**; the collector serves its cache, even expired, and the
responses that carry that data say it is stale. A provider with no published quota has no budget, but
its calls are still counted. The one exception is ``wmo_swic`` (the national services of the WMO registry, which
publish no quota): it has a ceiling of our own, said to be ours wherever it is shown.

The quotas are declared in ``QUOTAS``, the only place, each with the page it comes from. Open-Meteo
applies one quota to its four APIs (forecast, air quality, river discharge, marine), so those four
collectors share one budget (``share_budgets``).

``VIGIE_BUDGET_FRACTION`` (default 0.9) is the part of each published limit the budget lets through. It
keeps a margin for what the API cannot see: another program on the same IP address, the redirects, the
difference between this count and the provider's.
"""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Optional

from app.models.schemas import QuotaLimitStatus, QuotaStatus

DAY_S = 86_400
DEFAULT_BUDGET_FRACTION = 0.9


class BudgetExhaustedError(RuntimeError):
    """The request was not sent: it would exceed the call budget of the provider."""


@dataclass(frozen=True)
class Limit:
    """At most ``calls`` (in weight units) in any window of ``window_s`` seconds."""

    window_s: int
    calls: float


@dataclass(frozen=True)
class Quota:
    """A published quota, shared by every collector of the same ``group``."""

    group: str
    limits: tuple[Limit, ...]
    source_url: str


# Open-Meteo, free API: "Less than 10'000 API calls per day, 5'000 per hour and 600 per minute"
# (the monthly limit, 300,000 calls, is the daily one over 30 days). The limits are on the weighted calls.
OPEN_METEO_QUOTA = Quota(
    group="open-meteo",
    limits=(Limit(DAY_S, 10_000), Limit(3_600, 5_000), Limit(60, 600)),
    source_url="https://open-meteo.com/en/terms",
)

# provider_id -> published quota. A provider that is not listed publishes none (not verified): it has no
# budget, and its calls are still counted.
QUOTAS: dict[str, Quota] = {
    "open_meteo_hd": OPEN_METEO_QUOTA,
    "open_meteo_aqi": OPEN_METEO_QUOTA,
    "open_meteo_flood": OPEN_METEO_QUOTA,
    "open_meteo_marine": OPEN_METEO_QUOTA,
    "open_meteo_surf": OPEN_METEO_QUOTA,  # the swell (MFWAM) and the wind of the surf spots: the same free API
    # "Anything over 20 requests/second per application (total, not per client) requires special agreement."
    "met_norway_fallback": Quota("met-norway", (Limit(1, 20),), "https://api.met.no/doc/TermsOfService"),
    # "Rate limiting set to 100 requests/IP/minute" (since 2026-01-01).
    "rainviewer_radar": Quota("rainviewer", (Limit(60, 100),), "https://rainviewer.com/api/transition-faq.html"),
    # MSC Open Data Service Usage Policy: "If you or your application's usage, involves 86,400 requests per day or
    # higher (about 1 request per second), contact us" (read 2026-10-20). A threshold above which the MSC asks to be
    # told, not a quota that refuses: it is kept as the limit all the same (this API sends under 300 a day).
    "eccc_alerts": Quota("eccc-msc", (Limit(DAY_S, 86_400),), "https://eccc-msc.github.io/open-data/usage-policy/readme_en/"),
    # The national services of the WMO registry publish no quota, and neither does the WMO: this is a ceiling of our own, not a
    # published limit, against a loop or an instance that follows the whole world. The worst case measured by the simulation
    # (every country of the registry followed all day, tests/test_swic_sobriety.py) is about 37,000 requests: the ceiling is 50,000.
    # The page it points to is the one of this project that states it (SOURCES.md).
    "wmo_swic": Quota("wmo-swic", (Limit(DAY_S, 50_000),), "https://github.com/Axellum/vigie-api/blob/main/SOURCES.md"),
    # NASA FIRMS: "MAP_KEY limit is 5000 transactions / 10-minute interval. Larger transactions may count as multiple requests
    # (ex. requesting 7 days)" (https://firms.modaps.eosdis.nasa.gov/api/area/, read 2026-10-20). The limit is the one of the area API,
    # which this API reads only as a fallback, with a key, when a static file could not be read: a world read of one day is weighed
    # 36 transactions (measured with /mapserver/mapkey_status/), see ``WORLD_DAY_TRANSACTIONS`` in ``firms.py``. The static files
    # have no key and no transaction: they weigh 0 and are counted as calls only.
    "nasa_firms": Quota("nasa-firms", (Limit(600, 5_000),), "https://firms.modaps.eosdis.nasa.gov/api/area/"),
    # ECMWF open data on its AWS mirror (the unofficial pre-alert pilot for Turkey, off by default): no request limit is published
    # for the mirror (ECMWF's own server allows 500 to 2,000 simultaneous connections). A ceiling of our own: 2 runs of 144 requests
    # (an index and five byte ranges per lead) and a check every 30 minutes (one or two HEAD) make about 400 a day; 1,000 leaves room
    # for a run read again after a failure.
    "ecmwf_opendata": Quota("ecmwf-opendata", (Limit(DAY_S, 1_000),), "https://github.com/Axellum/vigie-api/blob/main/SOURCES.md"),
    # GOV.UK Content API (the FCDO's travel advice): "There is a maximum limit of 10 requests per second per client"
    # (https://content-api.publishing.service.gov.uk/, read 2026-10-28). The pages are read one after the other, 0.2 s apart.
    "travel_gb": Quota("gov-uk-content-api", (Limit(1, 10),), "https://content-api.publishing.service.gov.uk/"),
    # CANDHIS (Cerema): "150 requêtes par jour et 12 mois de données par requête maxi", in the e-mail of the Candhis team that came with
    # the key (2026-11-09). The guide (https://candhis.cerema.fr/doc/04_Candhis_API_v1_Utilisateur.pdf, read 2026-11-09) states no number: it
    # only says that a 429 answers once the daily quota is reached. This API sends at most 72 a day (three kinds, once an hour).
    "candhis_tr": Quota("candhis", (Limit(DAY_S, 150),), "https://candhis.cerema.fr/doc/04_Candhis_API_v1_Utilisateur.pdf"),
    # Météo-France Portail API (DPVigilance v1): the free plan allows 50 requests per minute
    # (https://portail-api.meteofrance.fr/web/fr/api/DonneesPubliquesVigilance, read 2026-11-15). This API sends at most
    # two requests (the map and the texts) every 3 minutes.
    "meteofrance_vigilance": Quota(
        "meteofrance-vigilance",
        (Limit(60, 50),),
        "https://portail-api.meteofrance.fr/web/fr/api/DonneesPubliquesVigilance",
    ),
    # Météo-France Portail API (DPPaquetObs v2): the free plan allows at least 50 requests per minute
    # (https://portail-api.meteofrance.fr/web/fr/api/PaquetObs, read 2026-11-15). This API sends at most one request
    # every 6 minutes (10 an hour).
    "meteofrance_obs": Quota(
        "meteofrance-obs",
        (Limit(60, 50),),
        "https://portail-api.meteofrance.fr/web/fr/api/PaquetObs",
    ),
    # Météo-France Portail API (DPBRA v1): 50 requests per minute, as stated by the owner for this subscription on 2026-11-16 (not
    # re-read on https://portail-api.meteofrance.fr/web/fr/api/DonneesPubliquesBRA; the same plan as DPVigilance).
    # This API sends at most one request per massif every 30 minutes (35 massifs: 70 an hour at the very most).
    "meteofrance_bra": Quota(
        "meteofrance-bra",
        (Limit(60, 50),),
        "https://portail-api.meteofrance.fr/web/fr/api/DonneesPubliquesBRA",
    ),
    # Météo-France Portail API (DPObs v2, buoys): 50 requests a minute, the plan of the portal's public data (not re-read on the
    # page of this API, rendered by script). This API sends at most one request every 30 minutes (48 a day).
    "meteofrance_buoys": Quota(
        "meteofrance-buoys",
        (Limit(60, 50),),
        "https://portail-api.meteofrance.fr/web/fr/api/DonneesPubliquesObservation",
    ),
    # Météo-France PIAF, read through a vigie-meteo gateway (``app/collectors/meteo_gateway.py``). Since vigie-meteo #3 the gateway
    # downloads one map of France per run (about 18 PIAF requests every 5 minutes, whatever the number of points; Météo-France's
    # free plan allows 50 a minute) and reads each point from it, so a point costs a gateway call, not a Météo-France request.
    # A ceiling of our own, per instance, that protects the gateway: 3,000 points every 5 minutes (held to 90 %: 2,700). A point
    # over the ceiling keeps Open-Meteo's rain. The page it points to is the one of this project that states it (SOURCES.md).
    "meteofrance_piaf": Quota("vigie-meteo-piaf", (Limit(300, 3000),), "https://github.com/Axellum/vigie-api/blob/main/SOURCES.md"),
    # Météo-France Portail API (PaquetENVIRONNEMENT, PHEALTH daily UV index): 50 requests a minute, as the key of the application says
    # for this API (the portal's free plan). This API sends three requests an hour (the runs, the newest run, its file when new).
    "meteofrance_uv": Quota(
        "meteofrance-uv",
        (Limit(60, 50),),
        "https://portail-api.meteofrance.fr/web/fr/api/PaquetENVIRONNEMENT",
    ),
    # VigiEau (https://api.vigieau.gouv.fr): MTES-MCT/vigieau-api configures ThrottlerModule at 300 requests per 15 minutes
    # per IP (``x-ratelimit-limit: 300`` observed on 2026-11-15, alongside a 1s reset header from throttler v6 ms units).
    # Enforcing 300 / 900s (held to 90% = 270 / 15 min) protects the upstream and is backed by 1-hour grid/commune caching.
    "vigieau": Quota(
        "vigieau",
        (Limit(900, 300),),
        "https://api.vigieau.gouv.fr/swagger-json",
    ),
    # Not listed, no published quota: usgs_eq, emsc_seismic, gdacs_global, noaa_nws (a limit exists but "is
    # not public information"), usgs_volcano_gvp, noaa_swpc, and the other warning services: dwd_cap, metservice_cap and
    # vigicrues_flow publish none (nothing about a request rate on their terms or documentation pages, 2026-10-20: not
    # verified), meteoalarm_atom and meteoalarm_cap neither. The other travel advice sources (travel_us, travel_ca, travel_au,
    # travel_nl) publish none on the pages read on 2026-10-28 (not verified that none exists).
    # slf_avalanche and euregio_avalanche publish no numeric rate limit on their open-data pages (2026-11-15: SLF terms
    # forbid impairing/overloading infrastructure; both use a 1800s TTL + conditional ETag/304).
}


def budget_fraction_from_env() -> float:
    """The part of each limit the budget lets through: ``VIGIE_BUDGET_FRACTION``, in ]0, 1], else 0.9."""
    raw = os.environ.get("VIGIE_BUDGET_FRACTION", "").strip()
    if not raw:
        return DEFAULT_BUDGET_FRACTION
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_BUDGET_FRACTION
    return value if 0.0 < value <= 1.0 else DEFAULT_BUDGET_FRACTION


class UsageWindow:
    """Calls and weight over the last ``seconds`` seconds (a sliding window)."""

    def __init__(self, seconds: int = DAY_S) -> None:
        self.seconds = seconds
        self._items: deque[tuple[float, float]] = deque()
        self._weight = 0.0

    def prune(self, now: float) -> None:
        """Forget what left the window."""
        while self._items and self._items[0][0] <= now - self.seconds:
            self._weight -= self._items.popleft()[1]
        if not self._items:
            self._weight = 0.0

    def add(self, now: float, weight: float) -> None:
        self.prune(now)
        self._items.append((now, weight))
        self._weight += weight

    def calls(self, now: float) -> int:
        self.prune(now)
        return len(self._items)

    def weight(self, now: float) -> float:
        self.prune(now)
        return round(max(0.0, self._weight), 4)


class CallBudget:
    """The budget of one provider, or of the group of providers that share a quota."""

    def __init__(self, quota: Optional[Quota], fraction: float = DEFAULT_BUDGET_FRACTION) -> None:
        self.quota = quota
        self.fraction = fraction
        self._windows: list[tuple[Limit, UsageWindow]] = (
            [(limit, UsageWindow(limit.window_s)) for limit in quota.limits] if quota is not None else []
        )

    def try_spend(self, weight: float, now: float) -> bool:
        """Count a request of ``weight`` if it fits in every limit; ``False`` (nothing counted) if it does not."""
        for limit, window in self._windows:
            if window.weight(now) + weight > limit.calls * self.fraction + 1e-9:
                return False
        for _, window in self._windows:
            window.add(now, weight)
        return True

    def is_exhausted(self, now: float) -> bool:
        """True when a request of weight 1 would not fit."""
        return any(window.weight(now) + 1.0 > limit.calls * self.fraction + 1e-9 for limit, window in self._windows)

    def status(self, now: float) -> Optional[QuotaStatus]:
        """The state of the quota for ``GET /v1/providers``; ``None`` for a provider with no published quota."""
        if self.quota is None:
            return None
        return QuotaStatus(
            group=self.quota.group,
            source_url=self.quota.source_url,
            exhausted=self.is_exhausted(now),
            limits=[
                QuotaLimitStatus(
                    window_s=limit.window_s,
                    limit=limit.calls,
                    budget=round(limit.calls * self.fraction, 2),
                    used=window.weight(now),
                )
                for limit, window in self._windows
            ],
        )


def share_budgets(collectors: Iterable[Any]) -> None:
    """Make the collectors that share a quota share one budget (each one keeps its own call counts)."""
    shared: dict[str, CallBudget] = {}
    for collector in collectors:
        quota = collector.budget.quota
        if quota is not None:
            collector.budget = shared.setdefault(quota.group, collector.budget)
