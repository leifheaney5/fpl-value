"""All-time FPL manager rankings, sourced from Premier Fantasy Tools.

The published table at premierfantasytools.com/best-fpl-managers-list is drawn
client-side from a single JSON endpoint, so this reads that endpoint rather than
scraping the rendered page. The payload is a few megabytes and describes
multi-season rankings that move at most once a day, so it is cached on disk for
a day and served from memory within a process.

The data belongs to Premier Fantasy Tools. Every view that uses it credits the
source and links back to it; see app/templates/managers.html.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import httpx

from app.config import Settings
from app.services.freshness import FreshnessCache, FreshnessPolicy


logger = logging.getLogger(__name__)


class ManagerRanksUnavailable(RuntimeError):
    """No usable rankings: the fetch failed and no cached copy exists."""


# The rankings are recomputed at most daily, so a day-long cache is the natural
# cadence. `stale_if_error` is left on: a day-old table is far more useful than
# an error page.
MANAGER_RANKS_POLICY = FreshnessPolicy("manager_ranks", ttl_seconds=24 * 60 * 60)

_CACHE = FreshnessCache()
_CACHE_KEY = "manager_ranks"

PAGE_SIZE = 100

# The per-manager page the source table links each name to.
MANAGER_PROFILE_URL = "https://www.premierfantasytools.com/whats-my-all-time-rank"

# Sortable columns, mapped to the row field each one reads.
SORT_FIELDS: dict[str, str] = {
    "rank": "rank",
    "change": "rank_change",
    "rank_3": "rank_3",
    "rank_4": "rank_4",
    "rank_5": "rank_5",
    "rank_6": "rank_6",
    "rank_7": "rank_7",
    "rank_10": "rank_10",
}

# The source renders flags from the FPL CDN, which is keyed by ISO alpha-3. Its
# own payload uses alpha-2 plus four non-ISO codes for the home nations and one
# legacy Netherlands spelling, all preserved here so those rows keep their flag.
_ALPHA3: dict[str, str] = {
    "AF": "AFG", "AL": "ALB", "DZ": "DZA", "AS": "ASM", "AD": "AND", "AO": "AGO",
    "AI": "AIA", "AQ": "ATA", "AG": "ATG", "AR": "ARG", "AM": "ARM", "AW": "ABW",
    "AU": "AUS", "AT": "AUT", "AZ": "AZE", "BS": "BHS", "BH": "BHR", "BD": "BGD",
    "BB": "BRB", "BY": "BLR", "BE": "BEL", "BZ": "BLZ", "BJ": "BEN", "BM": "BMU",
    "BT": "BTN", "BO": "BOL", "BA": "BIH", "BW": "BWA", "BR": "BRA", "BN": "BRN",
    "BG": "BGR", "BF": "BFA", "BI": "BDI", "KH": "KHM", "CM": "CMR", "CA": "CAN",
    "CV": "CPV", "CF": "CAF", "TD": "TCD", "CL": "CHL", "CN": "CHN", "CO": "COL",
    "CX": "CXR", "KM": "COM", "CG": "COG", "CD": "COD", "CR": "CRI", "CI": "CIV",
    "HR": "HRV", "CU": "CUB", "CY": "CYP", "CZ": "CZE", "DK": "DNK", "DJ": "DJI",
    "DM": "DMA", "DO": "DOM", "EC": "ECU", "EG": "EGY", "EN": "ENG", "SV": "SLV",
    "GQ": "GNQ", "ER": "ERI", "EE": "EST", "ET": "ETH", "FJ": "FJI", "FI": "FIN",
    "FR": "FRA", "GA": "GAB", "GM": "GMB", "GE": "GEO", "DE": "DEU", "GH": "GHA",
    "GR": "GRC", "GD": "GRD", "GT": "GTM", "GN": "GIN", "GW": "GNB", "GY": "GUY",
    "HT": "HTI", "HN": "HND", "HU": "HUN", "IS": "ISL", "IN": "IND", "ID": "IDN",
    "IR": "IRN", "IQ": "IRQ", "IE": "IRL", "IL": "ISR", "IT": "ITA", "JM": "JAM",
    "JP": "JPN", "JO": "JOR", "KZ": "KAZ", "HK": "HKG", "KE": "KEN", "KI": "KIR",
    "KP": "PRK", "KR": "KOR", "KW": "KWT", "KG": "KGZ", "LA": "LAO", "LV": "LVA",
    "LB": "LBN", "LS": "LSO", "LR": "LBR", "LY": "LBY", "LI": "LIE", "LT": "LTU",
    "LU": "LUX", "MG": "MDG", "MW": "MWI", "MY": "MYS", "MV": "MDV", "ML": "MLI",
    "MT": "MLT", "MH": "MHL", "MR": "MRT", "MU": "MUS", "MX": "MEX", "FM": "FSM",
    "MD": "MDA", "MC": "MCO", "IM": "IMN", "MN": "MNG", "ME": "MNE", "MA": "MAR",
    "MZ": "MOZ", "MM": "MMR", "NA": "NAM", "NR": "NRU", "NP": "NPL", "NL": "NLD",
    "NZ": "NZL", "NI": "NIC", "NE": "NER", "NG": "NGA", "MK": "MKD", "NO": "NOR",
    "OM": "OMN", "PK": "PAK", "PW": "PLW", "PA": "PAN", "PG": "PNG", "PY": "PRY",
    "PS": "PSE", "PE": "PER", "PH": "PHL", "PL": "POL", "PT": "PRT", "QA": "QAT",
    "RO": "ROU", "RU": "RUS", "RW": "RWA", "KN": "KNA", "LC": "LCA", "VC": "VCT",
    "WS": "WSM", "SM": "SMR", "ST": "STP", "S1": "SCO", "SA": "SAU", "SN": "SEN",
    "RS": "SRB", "SC": "SYC", "SL": "SLE", "SG": "SGP", "SK": "SVK", "SI": "SVN",
    "SB": "SLB", "SO": "SOM", "ZA": "ZAF", "SS": "SSD", "ES": "ESP", "LK": "LKA",
    "NN": "NLD", "SD": "SDN", "SR": "SUR", "SE": "SWE", "CH": "CHE", "SY": "SYR",
    "TW": "TWN", "TJ": "TJK", "TZ": "TZA", "TH": "THA", "TL": "TLS", "TG": "TGO",
    "TO": "TON", "TT": "TTO", "TN": "TUN", "TR": "TUR", "TM": "TKM", "TV": "TUV",
    "UG": "UGA", "UA": "UKR", "AE": "ARE", "GB": "GBR", "US": "USA", "UY": "URY",
    "UZ": "UZB", "VU": "VUT", "VE": "VEN", "VN": "VNM", "YE": "YEM", "WA": "WAL",
    "ZM": "ZMB", "ZW": "ZWE",
}


def flag_code(iso: str | None) -> str | None:
    """Return the alpha-3 code the FPL flag CDN expects, if one is known."""
    if not iso:
        return None
    return _ALPHA3.get(iso.upper())


@dataclass(frozen=True)
class ManagerRanks:
    """A ranking table with the provenance needed to display it honestly."""

    rows: tuple[dict[str, Any], ...]
    fetched_at: float
    source_url: str
    # True when the fetch failed and an expired cached copy is being shown.
    stale: bool = False

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.fetched_at)


def _as_int(value: object) -> int | None:
    """Coerce a payload value to int, treating anything unusable as absent."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip().replace(",", ""))
        except ValueError:
            return None
    return None


def _as_text(value: object) -> str:
    """Trim a payload string and undo the one escaping artefact it arrives with.

    Names and team names reach the endpoint with single quotes doubled --
    "Angela''s Team" -- which is SQL escaping that was never unwound upstream.
    It is reversed here because it is unambiguous in this data and renders as
    visible breakage otherwise. Nothing else about the text is altered.
    """
    if not isinstance(value, str):
        return ""
    return value.strip().replace("''", "'")


def normalise_rows(payload: object) -> tuple[dict[str, Any], ...]:
    """Validate a payload and reduce it to the fields in use.

    Rows without a rank and an entry id cannot be displayed or linked, so they
    are dropped rather than rendered as blanks. An empty result is an error: it
    means the shape changed, and silently serving nothing would look like a
    working page that believes there are no FPL managers.

    Deliberately idempotent, because the disk cache holds rows that have already
    been through here and is re-validated on read. Any field renamed on the way
    in must therefore be accepted under both spellings -- otherwise a fresh
    fetch and a cache hit produce different rows, and only one of them is right.
    """
    if not isinstance(payload, list):
        raise ValueError("manager ranks payload was not a list")

    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        rank = _as_int(item.get("rank"))
        entry_id = _as_int(item.get("id"))
        if rank is None or entry_id is None:
            continue
        previous = _as_int(item.get("rank_prev"))
        handle = _as_text(item.get("twitter_handle"))
        rows.append(
            {
                "rank": rank,
                "id": entry_id,
                "name": _as_text(item.get("name")) or f"Entry {entry_id}",
                "team_name": _as_text(item.get("team_name")),
                # "player_region_short_iso" upstream, "country" once stored.
                "country": _as_text(
                    item.get("player_region_short_iso") or item.get("country")
                ).upper(),
                # Only ever rendered as a link, so anything that is not an
                # http(s) URL is discarded rather than trusted.
                "twitter_handle": handle if handle.startswith(("http://", "https://")) else "",
                "rank_prev": previous,
                # Positive means the manager climbed, matching the arrow shown
                # upstream: a smaller rank number is a better rank.
                "rank_change": None if previous is None else previous - rank,
                **{
                    f"rank_{window}": _as_int(item.get(f"rank_{window}"))
                    for window in (3, 4, 5, 6, 7, 10)
                },
            }
        )

    if not rows:
        raise ValueError("manager ranks payload contained no usable rows")
    return tuple(rows)


def fetch_manager_ranks(
    settings: Settings, *, http_client: httpx.Client | None = None
) -> tuple[dict[str, Any], ...]:
    """Fetch and validate the rankings from the upstream endpoint."""
    url = settings.manager_ranks_url
    client = http_client or httpx.Client(timeout=60.0, follow_redirects=True)
    # Set per request rather than on the client, so an injected client -- a test
    # double, or a shared client later -- sends exactly what production sends.
    headers = {
        "Accept": "application/json",
        "User-Agent": "FPL-Value-Studio/1.0",
        # The endpoint backs the public page below, which is sent so the request
        # is attributable rather than anonymous.
        "Referer": settings.manager_ranks_source_page,
    }
    started_at = time.perf_counter()
    try:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        rows = normalise_rows(response.json())
    finally:
        if http_client is None:
            client.close()
    logger.info(
        "manager_ranks_fetch url=%s rows=%s duration_ms=%.1f",
        url,
        len(rows),
        (time.perf_counter() - started_at) * 1000,
    )
    return rows


def _cache_path(settings: Settings) -> Path:
    return Path(settings.manager_ranks_cache_path)


def read_cache_file(settings: Settings) -> tuple[tuple[dict[str, Any], ...], float] | None:
    """Return cached rows and when they were fetched, or None if unusable."""
    path = _cache_path(settings)
    try:
        with path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    fetched_at = document.get("fetched_at")
    if not isinstance(fetched_at, (int, float)) or isinstance(fetched_at, bool):
        return None
    try:
        rows = normalise_rows(document.get("rows"))
    except ValueError:
        return None
    return rows, float(fetched_at)


def write_cache_file(
    settings: Settings, rows: Sequence[dict[str, Any]], fetched_at: float
) -> None:
    """Persist rows so a restart does not force a refetch.

    Written to a temporary file in the same directory and renamed, so a reader
    never observes a half-written cache. A failure here is logged and swallowed:
    the rankings are already in hand, and an unwritable cache directory should
    degrade to refetching, not break the page.
    """
    path = _cache_path(settings)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "fetched_at": fetched_at,
                    "source_url": settings.manager_ranks_url,
                    "rows": list(rows),
                },
                handle,
            )
        os.replace(temporary, path)
    except OSError as error:
        logger.warning("manager_ranks_cache_write_failed path=%s error=%s", path, error)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _load(settings: Settings, http_client: httpx.Client | None) -> ManagerRanks:
    """Serve the disk cache while it is fresh, otherwise refetch.

    The freshness decision uses the wall clock rather than the in-process cache,
    because the point of the disk copy is to survive restarts, and a monotonic
    clock says nothing about how old a file is.
    """
    ttl = settings.manager_ranks_ttl_seconds
    cached = read_cache_file(settings)
    if cached is not None and time.time() - cached[1] < ttl:
        rows, fetched_at = cached
        logger.info(
            "manager_ranks_disk_hit rows=%s age_s=%.0f",
            len(rows),
            time.time() - fetched_at,
        )
        return ManagerRanks(rows, fetched_at, settings.manager_ranks_source_page)

    try:
        rows = fetch_manager_ranks(settings, http_client=http_client)
    except (httpx.HTTPError, ValueError) as error:
        # An expired cache still beats an error page, so fall back to it and say
        # so in the view rather than hiding the staleness.
        if cached is not None:
            logger.warning("manager_ranks_serving_stale error=%s", error)
            return ManagerRanks(
                cached[0], cached[1], settings.manager_ranks_source_page, stale=True
            )
        logger.warning(
            "manager_ranks_unavailable url=%s error=%s", settings.manager_ranks_url, error
        )
        raise ManagerRanksUnavailable(
            f"could not fetch manager ranks from {settings.manager_ranks_url}: {error}"
        ) from error

    fetched_at = time.time()
    write_cache_file(settings, rows, fetched_at)
    return ManagerRanks(rows, fetched_at, settings.manager_ranks_source_page)


def manager_ranks(
    settings: Settings,
    *,
    force: bool = False,
    http_client: httpx.Client | None = None,
) -> ManagerRanks:
    """Return the rankings, loading them at most once per TTL per process."""
    record = _CACHE.get(
        _CACHE_KEY,
        lambda: _load(settings, http_client),
        MANAGER_RANKS_POLICY,
        force=force,
    )
    value = record.value
    if record.stale and not value.stale:
        # The in-process copy outlived its TTL and the reload failed.
        return ManagerRanks(value.rows, value.fetched_at, value.source_url, stale=True)
    return value


def countries(rows: Sequence[dict[str, Any]]) -> list[str]:
    """The country codes present in the data, so the filter offers only those."""
    return sorted({row["country"] for row in rows if row["country"]})


@dataclass(frozen=True)
class ManagerPage:
    """One page of filtered, sorted rankings plus what the view needs to page it."""

    rows: tuple[dict[str, Any], ...]
    total: int
    page: int
    pages: int
    page_size: int

    @property
    def first_index(self) -> int:
        """1-based index of the first row shown, or 0 when there are none."""
        return 0 if not self.rows else (self.page - 1) * self.page_size + 1

    @property
    def last_index(self) -> int:
        return (self.page - 1) * self.page_size + len(self.rows)


def select_managers(
    rows: Sequence[dict[str, Any]],
    *,
    search: str = "",
    country: str = "",
    sort: str = "rank",
    descending: bool = False,
    page: int = 1,
    page_size: int = PAGE_SIZE,
) -> ManagerPage:
    """Filter, sort and paginate the rankings for one request.

    Sorting happens over the whole filtered set rather than the visible page,
    which is why it is done here rather than in the browser: a page-local sort
    of a 16,000-row table would answer a different question than the one the
    column heading appears to ask.
    """
    field = SORT_FIELDS.get(sort, "rank")

    needle = search.strip().lower()
    selected = [
        row
        for row in rows
        if (not country or row["country"] == country)
        and (not needle or needle in row["name"].lower() or needle in row["team_name"].lower())
    ]

    # A missing rank means the manager has not played that many seasons, which
    # is not the same as a bad rank. Absent values therefore sort last in both
    # directions rather than being ordered as zero. Negating the value rather
    # than reversing the sort keeps that rule independent of direction.
    direction = -1 if descending else 1
    selected.sort(key=lambda row: (row[field] is None, direction * (row[field] or 0)))

    total = len(selected)
    pages = max(1, -(-total // page_size))
    page = min(max(1, page), pages)
    start = (page - 1) * page_size
    return ManagerPage(
        rows=tuple(selected[start : start + page_size]),
        total=total,
        page=page,
        pages=pages,
        page_size=page_size,
    )
