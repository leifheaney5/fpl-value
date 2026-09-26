"""All-time manager rankings: payload handling, the day-long cache, and the page.

The data comes from a third party's endpoint, so the properties worth pinning
are the ones that decide what a visitor sees when that endpoint misbehaves: a
fresh cache must not produce a request, an expired one must still be shown
rather than discarded, and a total failure must explain itself instead of
raising.
"""

import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.services import manager_ranks as manager_ranks_service
from app.services.freshness import FreshnessCache
from app.services.manager_ranks import (
    ManagerRanksUnavailable,
    countries,
    flag_code,
    manager_ranks,
    normalise_rows,
    read_cache_file,
    select_managers,
    write_cache_file,
)


PAYLOAD = [
    {
        "rank": 1,
        "id": 53517,
        "name": "Ben Crellin",
        "team_name": "Upside Down",
        "player_region_short_iso": "en",
        "twitter_handle": "https://x.com/BenCrellin",
        "rank_prev": 3,
        "rank_3": 434,
        "rank_10": 1,
    },
    {
        "rank": 2,
        "id": 3718868,
        "name": "Fabio Borges",
        "team_name": "Mount Eberechi",
        "player_region_short_iso": "PT",
        "rank_prev": 1,
        "rank_3": 3587,
        # No rank_10: this manager has not played ten seasons.
    },
    # Unusable: no entry id, so it can neither be displayed nor linked.
    {"rank": 3, "name": "Nobody"},
]


def _settings(tmp_path, **overrides):
    return Settings(
        manager_ranks_cache_path=str(tmp_path / "manager_ranks.json"),
        **overrides,
    )


def _client(payload=PAYLOAD, *, status=200):
    """A client that answers from memory and counts the requests made."""
    calls: list[httpx.Request] = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handle)), calls


@pytest.fixture(autouse=True)
def _isolate_process_cache(monkeypatch):
    """Give every test its own in-process cache.

    `clear_manager_ranks_cache` is not enough here: invalidation advances the
    generation but deliberately keeps the last good value, so that a later failed
    reload can still serve it. That is the behaviour production wants and exactly
    what a test asserting "the fetch failed and there was nothing cached" must
    not inherit from the test before it.
    """
    monkeypatch.setattr(manager_ranks_service, "_CACHE", FreshnessCache())
    yield
    app.dependency_overrides.pop(get_settings, None)


def test_payload_is_reduced_to_the_fields_in_use():
    rows = normalise_rows(PAYLOAD)

    assert len(rows) == 2, "the row without an entry id should have been dropped"
    first, second = rows
    assert first["country"] == "EN", "country codes are normalised to upper case"
    # Rank 3 to rank 1 is a climb of two, and a climb reads as positive.
    assert first["rank_change"] == 2
    assert second["rank_change"] == -1
    # A window the manager has not played is absent, not zero.
    assert second["rank_10"] is None
    assert first["rank_10"] == 1


def test_normalising_already_normalised_rows_changes_nothing():
    """The cache holds normalised rows and is re-validated when it is read.

    So a second pass has to be a no-op. It was not: the country arrived as
    `player_region_short_iso` and was stored as `country`, so re-reading the
    cache dropped it and every flag on the page disappeared -- but only after
    the first cache file had been written, never on a fresh fetch.
    """
    once = normalise_rows(PAYLOAD)
    twice = normalise_rows([dict(row) for row in once])

    assert twice == once
    assert twice[0]["country"] == "EN"


def test_rows_survive_a_round_trip_through_the_cache_file(tmp_path):
    settings = _settings(tmp_path)
    rows = normalise_rows(PAYLOAD)
    write_cache_file(settings, rows, time.time())

    restored, _ = read_cache_file(settings)
    assert restored == rows


def test_doubled_quotes_from_the_source_are_unescaped():
    """The upstream data leaks SQL escaping into names, and it is visible."""
    rows = normalise_rows(
        [{"rank": 1, "id": 7, "name": "Angela O''Brien", "team_name": "Angela''s Team"}]
    )
    assert rows[0]["name"] == "Angela O'Brien"
    assert rows[0]["team_name"] == "Angela's Team"


def test_a_handle_that_is_not_a_url_is_discarded():
    """The handle is only ever rendered as an href, so it is not taken on trust."""
    rows = normalise_rows(
        [{"rank": 1, "id": 7, "name": "A", "twitter_handle": "javascript:alert(1)"}]
    )
    assert rows[0]["twitter_handle"] == ""


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"rank": 1},
        "not json at all",
        [{"name": "no rank or id"}],
    ],
)
def test_a_payload_with_no_usable_rows_is_an_error(payload):
    """Serving zero managers would look like a working page, so it must raise."""
    with pytest.raises(ValueError):
        normalise_rows(payload)


def test_flag_codes_cover_the_home_nations_the_source_invents():
    # The source uses non-ISO codes for the home nations; losing them would
    # silently drop the flag from a large share of the table.
    assert flag_code("EN") == "ENG"
    assert flag_code("S1") == "SCO"
    assert flag_code("WA") == "WAL"
    assert flag_code("pt") == "PRT"
    assert flag_code("ZZ") is None
    assert flag_code("") is None


def test_a_fresh_disk_cache_is_served_without_a_request(tmp_path):
    settings = _settings(tmp_path)
    write_cache_file(settings, normalise_rows(PAYLOAD), time.time())

    client, calls = _client()
    with client:
        ranks = manager_ranks(settings, http_client=client)

    assert calls == [], "a cache inside its TTL must not reach the network"
    assert len(ranks.rows) == 2
    assert ranks.stale is False


def test_an_expired_disk_cache_is_refetched_and_rewritten(tmp_path):
    settings = _settings(tmp_path, manager_ranks_ttl_seconds=600)
    stale_rows = normalise_rows([dict(PAYLOAD[0], name="Old Name")])
    write_cache_file(settings, stale_rows, time.time() - 3600)

    client, calls = _client()
    with client:
        ranks = manager_ranks(settings, http_client=client)

    assert len(calls) == 1
    assert ranks.rows[0]["name"] == "Ben Crellin"
    assert ranks.stale is False
    # The refreshed copy is persisted, so the next process start is also free.
    rewritten, fetched_at = read_cache_file(settings)
    assert rewritten[0]["name"] == "Ben Crellin"
    assert time.time() - fetched_at < 60


def test_the_fetch_identifies_itself_and_its_source_page(tmp_path):
    settings = _settings(tmp_path)
    client, calls = _client()
    with client:
        manager_ranks(settings, http_client=client)

    request = calls[0]
    assert request.url == httpx.URL(settings.manager_ranks_url)
    assert "FPL-Value-Studio" in request.headers["user-agent"]
    assert request.headers["referer"] == settings.manager_ranks_source_page


def test_an_unreachable_source_still_serves_the_expired_cache(tmp_path):
    """A day-old table beats an error page, but it has to be labelled as old."""
    settings = _settings(tmp_path, manager_ranks_ttl_seconds=600)
    write_cache_file(settings, normalise_rows(PAYLOAD), time.time() - 7200)

    def fail(request):
        raise httpx.ConnectError("source is down", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        ranks = manager_ranks(settings, http_client=client)

    assert ranks.stale is True
    assert len(ranks.rows) == 2
    assert ranks.age_seconds > 3600


def test_a_malformed_response_falls_back_to_the_cache_as_well(tmp_path):
    """A shape change upstream is as survivable as an outage, and as loud."""
    settings = _settings(tmp_path, manager_ranks_ttl_seconds=600)
    write_cache_file(settings, normalise_rows(PAYLOAD), time.time() - 7200)

    client, _ = _client(payload={"unexpected": "shape"})
    with client:
        ranks = manager_ranks(settings, http_client=client)

    assert ranks.stale is True
    assert len(ranks.rows) == 2


def test_an_unreachable_source_with_no_cache_is_reported(tmp_path):
    settings = _settings(tmp_path)

    def fail(request):
        raise httpx.ConnectError("source is down", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(ManagerRanksUnavailable):
            manager_ranks(settings, http_client=client)


def test_a_corrupt_cache_file_is_ignored_rather_than_trusted(tmp_path):
    settings = _settings(tmp_path)
    path = tmp_path / "manager_ranks.json"
    path.write_text("{ not json", encoding="utf-8")
    assert read_cache_file(settings) is None

    path.write_text(json.dumps({"rows": []}), encoding="utf-8")
    assert read_cache_file(settings) is None, "a cache without a timestamp has no age"


def test_missing_windows_sort_last_in_both_directions():
    """An unplayed season is not a good rank and not a bad one, so it sorts out."""
    rows = normalise_rows(PAYLOAD)

    ascending = select_managers(rows, sort="rank_10")
    assert [row["rank_10"] for row in ascending.rows] == [1, None]

    descending = select_managers(rows, sort="rank_10", descending=True)
    assert [row["rank_10"] for row in descending.rows] == [1, None]


def test_sorting_by_rank_change_separates_climbers_from_fallers():
    rows = normalise_rows(PAYLOAD)
    best_first = select_managers(rows, sort="change", descending=True)
    assert [row["rank_change"] for row in best_first.rows] == [2, -1]


def test_filters_narrow_the_table_by_name_team_and_country():
    rows = normalise_rows(PAYLOAD)

    assert [row["name"] for row in select_managers(rows, search="crellin").rows] == ["Ben Crellin"]
    # The search covers the team name too, which is how most managers are known.
    assert [row["name"] for row in select_managers(rows, search="eberechi").rows] == ["Fabio Borges"]
    assert select_managers(rows, country="PT").total == 1
    assert select_managers(rows, country="ZZ").total == 0
    assert countries(rows) == ["EN", "PT"]


def test_pagination_reports_its_window_and_clamps_out_of_range_pages():
    rows = normalise_rows(PAYLOAD)

    first = select_managers(rows, page=1, page_size=1)
    assert (first.total, first.pages, first.first_index, first.last_index) == (2, 2, 1, 1)

    # A page number past the end shows the last page rather than an empty table.
    beyond = select_managers(rows, page=99, page_size=1)
    assert beyond.page == 2
    assert beyond.rows[0]["name"] == "Fabio Borges"

    empty = select_managers(rows, search="nobody here", page_size=1)
    assert (empty.total, empty.pages, empty.first_index, empty.last_index) == (0, 1, 0, 0)


def _override_settings(settings):
    app.dependency_overrides[get_settings] = lambda: settings


def test_the_managers_page_renders_the_cached_table(tmp_path):
    settings = _settings(tmp_path)
    write_cache_file(settings, normalise_rows(PAYLOAD), time.time())
    _override_settings(settings)

    body = TestClient(app).get("/managers").text

    assert "Ben Crellin" in body
    assert "Mount Eberechi" in body
    # The flag is resolved through the alpha-3 map, not the raw payload code.
    assert "/img/flags/ENG.svg" in body
    # A window the manager has not played renders as absent, never as a rank.
    assert "—" in body
    # The data is not ours, so the page has to say whose it is.
    assert "Premier Fantasy Tools" in body
    assert "premierfantasytools.com/best-fpl-managers-list" in body
    # Sorting is server-side, so each heading has to carry the other filters.
    assert "sort=rank_10" in body


def test_the_managers_page_keeps_filters_when_it_offers_a_new_sort(tmp_path):
    settings = _settings(tmp_path)
    write_cache_file(settings, normalise_rows(PAYLOAD), time.time())
    _override_settings(settings)

    body = TestClient(app).get("/managers?country=PT&search=borges").text

    assert "Fabio Borges" in body
    assert "Ben Crellin" not in body
    assert "country=PT" in body and "search=borges" in body


def test_an_unknown_sort_column_falls_back_rather_than_failing(tmp_path):
    settings = _settings(tmp_path)
    write_cache_file(settings, normalise_rows(PAYLOAD), time.time())
    _override_settings(settings)

    response = TestClient(app).get("/managers?sort=drop+table&direction=sideways")
    assert response.status_code == 200
    assert "Ben Crellin" in response.text


@pytest.mark.parametrize("page", ["0", "-4", "abc", "", "99999999"])
def test_any_page_number_lands_on_a_page(tmp_path, page):
    """This URL gets hand-edited and shared, so it must not return a 422."""
    settings = _settings(tmp_path)
    write_cache_file(settings, normalise_rows(PAYLOAD), time.time())
    _override_settings(settings)

    response = TestClient(app).get(f"/managers?page={page}")

    assert response.status_code == 200
    assert "of 2 managers" in response.text


def test_the_managers_page_explains_an_unavailable_source(tmp_path):
    """A third party's outage is a degraded page here, not a server error."""
    settings = _settings(tmp_path, manager_ranks_url="not-a-reachable-url")
    _override_settings(settings)

    response = TestClient(app).get("/managers")

    assert response.status_code == 503
    assert "could not be loaded" in response.text
    # The way out is a link to the source, which may well be working for them.
    assert settings.manager_ranks_source_page in response.text


def test_every_column_carries_an_explanation():
    """The glossary test in test_web.py scans templates for literal help keys.

    This table supplies them from the route instead, so they are invisible to
    that scan and are checked here instead of going unchecked.
    """
    from app.web.column_help import COLUMN_HELP
    from app.web.routes import _MANAGER_COLUMNS

    help_keys = {help_key for _, _, help_key in _MANAGER_COLUMNS}
    help_keys |= {"manager_country", "manager_name", "manager_team", "manager_links"}

    assert help_keys <= COLUMN_HELP.keys()
    assert all(COLUMN_HELP[key].strip() for key in help_keys)


def test_the_page_is_linked_from_every_page_of_the_app(tmp_path):
    """A tab nobody can reach is not a tab."""
    settings = _settings(tmp_path)
    write_cache_file(settings, normalise_rows(PAYLOAD), time.time())
    _override_settings(settings)

    body = TestClient(app).get("/managers").text
    assert 'href="/managers"' in body
