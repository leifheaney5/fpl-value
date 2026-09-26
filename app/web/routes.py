from __future__ import annotations

import io
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    Response,
)
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.analytics.contracts import MetricValue, describe
from app.analytics.metrics import sample_confidence
from app.api.fpl_client import FPLClient
from app.config import Settings, get_settings
from app.db.models import LinkedTeamSnapshot, Player
from app.db.session import get_db
from app.services.captaincy import captain_candidates
from app.services.data_status import data_status
from app.services.exports import csv_bytes, xlsx_bytes
from app.services.manager_ranks import (
    MANAGER_PROFILE_URL,
    ManagerRanksUnavailable,
    SORT_FIELDS,
    countries,
    flag_code,
    manager_ranks,
    select_managers,
)
from app.services.queries import (
    dashboard_data,
    filtered_players,
    latest_player_options,
    latest_rows,
    movers_data,
    diagnostics_data,
    player_history,
    recent_schema_changes,
)
from app.services.refresh import refresh_data
from app.services.season_history import career_summaries, stored_seasons
from app.services.season_state import Readiness
from app.services.my_team import clear_remote_team_cache, linked_team_data, transfer_plan
from app.services.team_recommender import (
    NotReadyError,
    STRATEGIES,
    compare_recommendations,
    recommend_team_cached,
)
from app.services.player_intelligence import build_player_intelligence
from app.services.template_teams import price_slot_suggestions, template_summaries
from app.services.team_analysis import fixture_analysis, team_performance
from app.web import audit
from app.web.auth import (
    can_access_personal,
    client_key,
    login_throttle,
    safe_next_path,
    valid_credentials,
    valid_csrf,
)
from app.web.column_help import column_help


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["asset_versions"] = {
    name: sha256((Path(__file__).parent.parent / "static" / name).read_bytes()).hexdigest()[:12]
    for name in ("app.css", "app.js")
}


_COMPARE_METRICS = (
    ("Price", "price", True),
    ("Total points", "total_points", False),
    ("Raw value", "value", False),
    ("Reliable value", "reliable_value", False),
    ("Forward value", "forward_value", False),
    ("Projected next fixtures", "projected_points_5", False),
    ("Rotation risk", "rotation_risk", True),
    ("PPG", "points_per_game", False),
    ("Points / 90", "points_per_90", False),
    ("Points / start", "points_per_start", False),
    ("Minutes", "minutes", False),
    ("Start rate", "start_rate", False),
    ("Expected minutes", "expected_minutes", False),
    ("Ownership", "ownership", False),
    ("Average fixture difficulty", "average_fixture_difficulty", True),
)
_COMPARE_RAW_METRICS = {
    "price",
    "total_points",
    "rotation_risk",
    "points_per_game",
    "minutes",
    "ownership",
}


def comparison_scale(
    values: list[float | int | None], *, lower_is_better: bool = False
) -> list[str]:
    """Assign a five-step red-to-green scale across comparable values."""
    available = [float(value) for value in values if value is not None]
    if len(available) < 2 or min(available) == max(available):
        return ["compare-scale-neutral"] * len(values)

    ordered = sorted(set(available), reverse=lower_is_better)
    last_index = len(ordered) - 1
    ranks = {
        value: round(index * 4 / last_index)
        for index, value in enumerate(ordered)
    }
    return [
        "compare-scale-neutral" if value is None
        else f"compare-scale-{ranks[float(value)]}"
        for value in values
    ]


def _comparison_value(snapshot: Any, name: str) -> float | int | None:
    if name in _COMPARE_RAW_METRICS:
        return getattr(snapshot, name, None)
    metric = _metric(snapshot, name)
    return metric.value if metric.is_value else None


def _comparison_cell(snapshot: Any, name: str) -> str:
    value = _comparison_value(snapshot, name)
    if value is None:
        return "—"
    if name == "price":
        return f"£{float(value):.1f}"
    if name == "rotation_risk":
        return f"{float(value):.1f}"
    if name == "points_per_game":
        return f"{float(value):.2f}"
    if name == "ownership":
        return f"{float(value):.1f}%"
    if name in {"total_points", "minutes"}:
        return str(value)
    rendered = _metric_cell(snapshot, name)
    return f"{rendered}%" if name == "start_rate" else rendered


def _comparison_metrics(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics = []
    for label, name, lower_is_better in _COMPARE_METRICS:
        values = [_comparison_value(row["snapshot"], name) for row in selected]
        metrics.append(
            {
                "label": label,
                "name": name,
                "classes": comparison_scale(values, lower_is_better=lower_is_better),
            }
        )
    return metrics


def _metric(snapshot: Any, name: str) -> MetricValue:
    """Pair a stored metric with the status recorded when it was calculated."""
    return describe(
        name,
        getattr(snapshot, name, None),
        getattr(snapshot, "metric_status", None),
    )


def _previous_season(season: str) -> str:
    """"2026/27" -> "2025/26". Used to name the season carry-over data describes."""
    try:
        start = int(season.split("/")[0])
    except (ValueError, AttributeError, IndexError):
        return "last season"
    return f"{start - 1}/{str(start)[-2:]}"


def _sample_mark(snapshot: Any) -> str:
    """A compact marker for how much sample sits behind this row's rates.

    Rendered next to rate columns so a 90-minute PPG cannot be read with the
    same weight as a 3000-minute one. Deliberately not a number: the exact
    minute count is already a column, and what is needed here is a glance.
    """
    level, _ = sample_confidence(snapshot)
    return {"high": "", "medium": "·", "low": "⚠", "none": ""}[level]


def _sample_title(snapshot: Any) -> str:
    return sample_confidence(snapshot)[1]


def _metric_cell(snapshot: Any, name: str, short: str = "—") -> str:
    """Render a metric for a dense table.

    Tables have no room for a full explanation, so an unavailable metric shows a
    dash and carries its reason in the title attribute. What matters is that it
    never shows a number it does not have.
    """
    value = _metric(snapshot, name)
    return value.display if value.is_value else short


templates.env.filters["metric"] = _metric
templates.env.filters["metric_cell"] = _metric_cell
templates.env.globals["metric"] = _metric
templates.env.globals["metric_cell"] = _metric_cell
templates.env.globals["sample_mark"] = _sample_mark
templates.env.globals["sample_title"] = _sample_title
templates.env.globals["comparison_cell"] = _comparison_cell
templates.env.globals["column_help"] = column_help


def _optional_number(value: str | None, parser):
    """Accept blank form values without turning them into a 422 response."""
    if value is None or not value.strip():
        return None
    try:
        return parser(value.strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    next: str = "/",
    settings: Settings = Depends(get_settings),
):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "next": safe_next_path(next),
            "credentials_configured": settings.credentials_configured,
        },
    )


@router.post("/login")
def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/",
    csrf_token: Annotated[str, Form()] = "",
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    key = client_key(request)
    if login_throttle.locked_out(
        key, settings.login_max_attempts, settings.login_lockout_seconds
    ):
        audit.record(db, "login.throttled", request, actor=username)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "next": safe_next_path(next),
                "error": "Too many failed attempts. Try again later.",
                "credentials_configured": settings.credentials_configured,
            },
            status_code=429,
        )

    if valid_csrf(request, csrf_token) and valid_credentials(settings, username, password):
        login_throttle.clear(key)
        request.session["authenticated"] = True
        audit.record(db, "login.success", request, actor=username)
        return RedirectResponse(safe_next_path(next), status_code=303)

    login_throttle.record_failure(key)
    audit.record(db, "login.failure", request, actor=username)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "next": safe_next_path(next),
            "error": "Invalid username or password.",
            "credentials_configured": settings.credentials_configured,
        },
        status_code=401,
    )


@router.post("/logout")
def logout(
    request: Request,
    csrf_token: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
):
    if not valid_csrf(request, csrf_token):
        raise HTTPException(403, "Invalid CSRF token")
    audit.record(db, "logout", request)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    data = dashboard_data(db, settings.current_season)
    data["data_status"] = data_status(db, settings.current_season)
    # Privacy by exclusion: personal data is never placed in a context an
    # anonymous visitor can receive, rather than being masked at render time.
    data["authenticated"] = can_access_personal(request, settings)
    if data["authenticated"]:
        with FPLClient(settings) as client:
            data["my_team"] = linked_team_data(
                db, client, settings, rows=data["rows"]
            )
    else:
        data["my_team"] = None
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=data,
    )


@router.get("/players", response_class=HTMLResponse)
def players(
    request: Request,
):
    target = "/spreadsheet"
    if request.url.query:
        target += "?" + request.url.query
    return RedirectResponse(target, status_code=307)


@router.get("/spreadsheet", response_class=HTMLResponse)
def spreadsheet(
    request: Request,
    view: str = "all",
    position: str | None = None,
    max_price: str | None = None,
    max_rotation: str | None = None,
    min_minutes: str | None = None,
    min_starts: str | None = None,
    min_expected_minutes: str | None = None,
    min_reliable_percentile: str | None = None,
    min_forward_percentile: str | None = None,
    min_start_rate: str | None = None,
    min_reliable_value: str | None = None,
    min_forward_value: str | None = None,
    max_ownership: str | None = None,
    status: str | None = None,
    sort: str = "reliable_value",
    history: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    views = {
        "all": ("All Players", "The complete player market with every core rating.", "reliable_value"),
        "forward": ("Forward Value", "Projected value over the upcoming fixture window.", "forward_value"),
        "rotation": ("Rotation Risk", "Playing-time security, from safest to riskiest.", "rotation_risk"),
        "movers": ("Movers", "Players with the largest recent value changes.", "value_movement"),
        "transfers": ("Transfer Shortlist", "Available, reliable candidates narrowed by your constraints.", "forward_value"),
    }
    if view not in views:
        view = "all"
    values = {"max_price": _optional_number(max_price, float), "max_rotation": _optional_number(max_rotation, float),
              "min_minutes": _optional_number(min_minutes, int), "min_starts": _optional_number(min_starts, int),
              "min_start_rate": _optional_number(min_start_rate, float), "min_reliable_value": _optional_number(min_reliable_value, float),
              "min_forward_value": _optional_number(min_forward_value, float), "max_ownership": _optional_number(max_ownership, float)}
    advanced = {"min_expected_minutes": _optional_number(min_expected_minutes, int),
                "min_reliable_percentile": _optional_number(min_reliable_percentile, float),
                "min_forward_percentile": _optional_number(min_forward_percentile, float)}
    effective_sort = sort if sort != "reliable_value" or view == "all" else views[view][2]
    rows = filtered_players(db, season=settings.current_season, position=position or None, status=status, sort=effective_sort, **values)
    if view == "transfers":
        rows = [row for row in rows
                if row["snapshot"].availability_factor > 0
                and (advanced["min_expected_minutes"] is None or row["snapshot"].expected_minutes >= advanced["min_expected_minutes"])
                and (advanced["min_reliable_percentile"] is None or (row["snapshot"].reliable_percentile or 0) >= advanced["min_reliable_percentile"])
                and (advanced["min_forward_percentile"] is None or (row["snapshot"].forward_percentile or 0) >= advanced["min_forward_percentile"])]
    title, description, _ = views[view]

    # Counting stats carry over from last season until a match is played, so
    # say which season the sheet is describing rather than letting last
    # season's totals read as this one's.
    carry_over = any(
        (row["snapshot"].team_matches or 0) == 0
        and ((row["snapshot"].total_points or 0) > 0 or (row["snapshot"].minutes or 0) > 0)
        for row in rows
    )

    # Each tab is only a different sort key. When that key is null for every
    # player the tab silently renders the same list in the same order, which
    # reads as a broken control rather than as missing data.
    #
    # Movement sorts do not live on the snapshot: they come from the history
    # deltas, so checking getattr(snapshot, "value_movement") would report the
    # Movers tab as permanently unsortable.
    sort_key = views[view][2]
    if view == "all":
        sortable = True
    elif sort_key == "value_movement":
        sortable = any(
            (row.get("history") or {}).get("1D", {}).get("delta_value") is not None
            for row in rows
        )
    else:
        sortable = any(
            getattr(row["snapshot"], sort_key, None) is not None for row in rows
        )

    # Past seasons are opt-in: the lookup is skipped entirely unless asked for.
    history_on = history == "1"
    history_seasons: list[str] = []
    if history_on:
        history_seasons = stored_seasons(db, exclude_season=settings.current_season)
        careers = career_summaries(
            db,
            [row["player"].code for row in rows],
            exclude_season=settings.current_season,
            sample_minutes=settings.reliability_sample_minutes,
        )
        for row in rows:
            row["career"] = careers.get(row["player"].code)
    toggle = (
        request.url.remove_query_params("history")
        if history_on
        else request.url.include_query_params(history=1)
    )

    return templates.TemplateResponse(request=request, name="spreadsheet.html", context={
        "history_on": history_on, "history_seasons": history_seasons,
        "history_toggle_url": toggle.path + (f"?{toggle.query}" if toggle.query else ""),
        "sample_minutes": settings.reliability_sample_minutes,
        "rows": rows, "view": view, "view_title": title, "view_description": description,
        "carry_over": carry_over, "previous_season": _previous_season(settings.current_season),
        "sortable": sortable, "sort_key_label": sort_key.replace("_", " "),
        "position": position or "", "status": status or "", "sort": effective_sort,
        **values, **advanced,
    })


@router.get("/players/{player_id}", response_class=HTMLResponse)
def player_detail(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    player = db.get(Player, player_id)
    if player is None:
        raise HTTPException(404, "Player not found")
    history = player_history(db, player_id, settings.current_season)
    if not history:
        raise HTTPException(404, "No player history available")
    return templates.TemplateResponse(
        request=request,
        name="player_detail.html",
        context={
            "player": player,
            "current": history[-1],
            "history": history,
            "career": career_summaries(
                db,
                [player.code],
                exclude_season=settings.current_season,
                sample_minutes=settings.reliability_sample_minutes,
            ).get(player.code),
            "comparison_options": latest_player_options(
                db,
                settings.current_season,
                exclude_player_id=player_id,
            ),
        },
    )


@router.get("/forward", response_class=HTMLResponse)
def forward_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    rows = filtered_players(db, season=settings.current_season, sort="forward_value")
    return templates.TemplateResponse(
        request=request,
        name="metric_table.html",
        context={
            "title": "Forward Value",
            "description": (
                "Projected points over the next fixtures divided by price."
            ),
            "rows": rows,
            "primary_metric": "forward_value",
        },
    )


@router.get("/rotation", response_class=HTMLResponse)
def rotation_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    rows = filtered_players(db, season=settings.current_season, sort="rotation_risk")
    return templates.TemplateResponse(
        request=request,
        name="metric_table.html",
        context={
            "title": "Rotation Risk",
            "description": (
                "A transparent 0–100 estimate based on start and minute security."
            ),
            "rows": rows,
            "primary_metric": "rotation_risk",
        },
    )


@router.get("/transfers", response_class=HTMLResponse)
def transfer_finder(
    request: Request,
    position: str | None = None,
    max_price: float | None = None,
    max_rotation: float | None = None,
    min_expected_minutes: int | None = None,
    min_reliable_percentile: float | None = None,
    min_forward_percentile: float | None = None,
    max_ownership: float | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    rows = filtered_players(db, season=settings.current_season, position=position, max_price=max_price, max_rotation=max_rotation, sort="forward_value")
    candidates = []
    for row in rows:
        snapshot = row["snapshot"]
        if min_expected_minutes is not None and snapshot.expected_minutes < min_expected_minutes:
            continue
        if min_reliable_percentile is not None and (snapshot.reliable_percentile or 0) < min_reliable_percentile:
            continue
        if min_forward_percentile is not None and (snapshot.forward_percentile or 0) < min_forward_percentile:
            continue
        if max_ownership is not None and snapshot.ownership > max_ownership:
            continue
        if snapshot.availability_factor <= 0:
            continue
        candidates.append(row)
    return templates.TemplateResponse(request=request, name="transfers.html", context={
        "rows": candidates, "position": position or "", "max_price": max_price,
        "max_rotation": max_rotation, "min_expected_minutes": min_expected_minutes,
        "min_reliable_percentile": min_reliable_percentile,
        "min_forward_percentile": min_forward_percentile, "max_ownership": max_ownership,
    })


@router.get("/differentials", response_class=HTMLResponse)
def differentials(
    request: Request,
    ownership: float = 10.0,
    position: str | None = None,
    max_price: float | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    rows = build_player_intelligence(latest_rows(db, settings.current_season))
    rows = [row for row in rows if row["snapshot"].ownership <= ownership]
    if position:
        rows = [row for row in rows if row["player"].position_short == position]
    if max_price is not None:
        rows = [row for row in rows if row["snapshot"].price <= max_price]

    # Players whose score cannot be calculated are shown separately rather than
    # ranked at the bottom, which would read as "scored, and scored badly".
    scored = [row for row in rows if row["differential"]["score"] is not None]
    unscored = [row for row in rows if row["differential"]["score"] is None]
    scored.sort(key=lambda row: row["differential"]["score"], reverse=True)
    return templates.TemplateResponse(request=request, name="differentials.html", context={
        "rows": scored, "unscored": unscored, "ownership": ownership,
        "position": position or "", "max_price": max_price,
    })


@router.get("/captaincy", response_class=HTMLResponse)
def captaincy(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Who to captain in the next gameweek, and how confident that is."""
    data = dashboard_data(db, settings.current_season)
    readiness = data["readiness"]["captaincy"]
    gameweek = data["season_state"].get("next_gameweek")

    candidates = []
    if readiness["state"] in (Readiness.READY, Readiness.STALE):
        candidates = captain_candidates(
            [row["snapshot"] for row in data["rows"]], gameweek
        )

    return templates.TemplateResponse(request=request, name="captaincy.html", context={
        "candidates": candidates,
        "gameweek": gameweek,
        "readiness": readiness,
        "season_state": data["season_state"],
        "season": data["season"],
    })


@router.get("/fixtures", response_class=HTMLResponse)
def fixtures_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name="fixtures.html",
        context={"rows": fixture_analysis(db, limit=10)},
    )


@router.get("/performance", response_class=HTMLResponse)
def performance_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name="performance.html",
        context={"rows": team_performance(db, limit=10)},
    )


_GRAPH_POSITIONS = ("GKP", "DEF", "MID", "FWD")
_GRAPH_METRICS = {
    "goals": "Goals",
    "assists": "Assists",
    "expected_goals": "xG",
    "expected_assists": "xA",
    "expected_goal_involvements": "xGI",
    "total_points": "Total points",
    "points_per_90": "Points / 90",
    "form": "Form",
    "bonus": "Bonus",
    "bps": "BPS",
    "ict_index": "ICT index",
    "clean_sheets": "Clean sheets",
    "minutes": "Minutes",
    "ownership": "Ownership %",
}


@router.get("/graphs", response_class=HTMLResponse)
def graphs_page(
    request: Request,
    position: str | None = None,
    metric: str = "goals",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if position not in _GRAPH_POSITIONS:
        position = None
    if metric not in _GRAPH_METRICS:
        metric = "goals"
    rows = filtered_players(db, season=settings.current_season, position=position, sort="price")
    # Every metric is embedded once so the toggle redraws without a request.
    points = [
        {
            "id": row["player"].id,
            "name": row["player"].full_name,
            "team": row["team"].short_name,
            "position": row["player"].position_short,
            "price": row["snapshot"].price,
            **{name: getattr(row["snapshot"], name) for name in _GRAPH_METRICS},
        }
        for row in rows
    ]
    # Same carry-over rule as the spreadsheet: totals describe last season
    # until a match is played.
    carry_over = any(
        (row["snapshot"].team_matches or 0) == 0
        and ((row["snapshot"].total_points or 0) > 0 or (row["snapshot"].minutes or 0) > 0)
        for row in rows
    )
    return templates.TemplateResponse(request=request, name="graphs.html", context={
        "points": points, "metrics": _GRAPH_METRICS, "metric": metric,
        "positions": _GRAPH_POSITIONS, "position": position or "",
        "carry_over": carry_over, "previous_season": _previous_season(settings.current_season),
    })


# (sort key, heading, column_help key). Split into two groups so the table can
# put identity between them: a name is what a row is scanned for, and it reads
# badly behind six columns of ranks.
_MANAGER_LEAD_COLUMNS = (
    ("rank", "Rank", "manager_rank"),
    ("change", "Change", "manager_change"),
)
_MANAGER_WINDOW_COLUMNS = (
    ("rank_3", "3-year", "manager_rank_3"),
    ("rank_4", "4-year", "manager_rank_4"),
    ("rank_5", "5-year", "manager_rank_5"),
    ("rank_6", "6-year", "manager_rank_6"),
    ("rank_7", "7-year", "manager_rank_7"),
    ("rank_10", "10-year", "manager_rank_10"),
)
_MANAGER_COLUMNS = _MANAGER_LEAD_COLUMNS + _MANAGER_WINDOW_COLUMNS


@router.get("/managers", response_class=HTMLResponse)
def managers_page(
    request: Request,
    search: str = "",
    country: str = "",
    sort: str = "rank",
    direction: str = "asc",
    # A string for the same reason every other filter here is one: this URL is
    # hand-edited and shared, and a bad page number should land on a page, not
    # on a 422. select_managers clamps whatever survives.
    page: str | None = None,
    settings: Settings = Depends(get_settings),
):
    """All-time manager rankings, read from Premier Fantasy Tools' own endpoint.

    The source page is a third party's, so an outage there must not take this
    page down with it: an unreachable endpoint with no cached copy renders an
    explanation and a link out, not a 500.
    """
    if sort not in SORT_FIELDS:
        sort = "rank"
    descending = direction == "desc"
    context: dict[str, Any] = {
        "search": search,
        "country": country.upper(),
        "sort": sort,
        "direction": "desc" if descending else "asc",
        "columns": _MANAGER_COLUMNS,
        "lead_columns": _MANAGER_LEAD_COLUMNS,
        "window_columns": _MANAGER_WINDOW_COLUMNS,
        "source_page": settings.manager_ranks_source_page,
        "profile_url": MANAGER_PROFILE_URL,
        "flag_code": flag_code,
    }
    try:
        ranks = manager_ranks(settings)
    except ManagerRanksUnavailable:
        # Already logged by the service, which knows the endpoint and the cause.
        return templates.TemplateResponse(
            request=request,
            name="managers.html",
            status_code=503,
            context={**context, "ranks": None, "results": None, "countries": []},
        )

    results = select_managers(
        ranks.rows,
        search=search,
        country=context["country"],
        sort=sort,
        descending=descending,
        page=_optional_number(page, int) or 1,
    )
    return templates.TemplateResponse(
        request=request,
        name="managers.html",
        context={
            **context,
            "ranks": ranks,
            "results": results,
            "countries": countries(ranks.rows),
        },
    )


@router.get("/transfer-market", response_class=HTMLResponse)
def transfer_market(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    rows = build_player_intelligence(latest_rows(db, settings.current_season))
    def totals(row):
        raw = row["snapshot"].raw or {}
        return int(raw.get("transfers_in_event") or 0), int(raw.get("transfers_out_event") or 0)
    incoming = sorted(rows, key=lambda row: totals(row)[0], reverse=True)[:10]
    outgoing = sorted(rows, key=lambda row: totals(row)[1], reverse=True)[:10]
    net = sorted(rows, key=lambda row: row["transfer_trend"]["net"] if row["transfer_trend"]["net"] is not None else -10**9, reverse=True)[:10]
    return templates.TemplateResponse(request=request, name="transfer_market.html", context={
        "incoming": incoming, "outgoing": outgoing, "net": net,
    })


@router.get("/templates", response_class=HTMLResponse)
def templates_page(
    request: Request,
    budget: str = "100",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    budget_value = max(50.0, min(100.0, _optional_number(budget, float) or 100.0))
    rows = latest_rows(db, settings.current_season)
    summary = {"templates": [], "checks": None, "activates_when": None, "error": None}
    if rows:
        summary = template_summaries(rows, budget_value)
        for item in summary["templates"]:
            selected = item["recommendation"]["starting"] + item["recommendation"]["bench"]
            item["slots"] = [
                {"selected": player, "alternatives": price_slot_suggestions(player["row"], rows)}
                for player in selected
            ]
    return templates.TemplateResponse(
        request=request,
        name="templates.html",
        context={
            "templates": summary["templates"],
            "checks": summary["checks"],
            "activates_when": summary["activates_when"],
            "error": summary["error"],
            "budget": budget_value,
        },
    )


@router.get("/diagnostics", response_class=HTMLResponse)
def diagnostics(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    data = diagnostics_data(db, settings.current_season)
    return templates.TemplateResponse(request=request, name="diagnostics.html", context=data)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    snapshot = None
    if can_access_personal(request, settings) and settings.fpl_entry_id:
        snapshot = db.scalar(
            select(LinkedTeamSnapshot).where(
                LinkedTeamSnapshot.entry_id == settings.fpl_entry_id
            )
        )
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={"team_snapshot": snapshot},
    )


@router.get("/my-team", response_class=HTMLResponse)
def my_team_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    # Defence in depth: the middleware already refuses anonymous requests to
    # this path, but the personal fetch stays behind an explicit check so a
    # future change to PROTECTION_MAP cannot silently expose it.
    if not can_access_personal(request, settings):
        return RedirectResponse("/login?next=/my-team", status_code=303)
    with FPLClient(settings) as client:
        team = linked_team_data(db, client, settings)
    return templates.TemplateResponse(
        request=request,
        name="my_team.html",
        context={"my_team": team},
    )


@router.post("/my-team/refresh")
def refresh_my_team(
    request: Request,
    csrf_token: Annotated[str, Form()] = "",
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    if not can_access_personal(request, settings):
        return RedirectResponse("/login?next=/my-team", status_code=303)
    if not valid_csrf(request, csrf_token):
        audit.record(db, "my_team.refresh.denied", request)
        raise HTTPException(403, "Invalid CSRF token")
    clear_remote_team_cache(entry_id=settings.fpl_entry_id)
    audit.record(db, "my_team.refresh", request)
    return RedirectResponse("/my-team", status_code=303)


@router.get("/recommendation", response_class=HTMLResponse)
def recommendation_page(
    request: Request,
    budget: str = "100",
    strategy: str = "best_team",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    budget_value = _optional_number(budget, float) or 100.0
    budget_value = max(50.0, min(100.0, budget_value))
    recommendation = None
    error = None
    checks = None
    activates_when = None
    rows = latest_rows(db, settings.current_season)
    comparisons = compare_recommendations(rows, budget_value)
    try:
        recommendation = recommend_team_cached(rows, budget_value, strategy)
    except NotReadyError as exc:
        # Not an error: the inputs simply cannot support a recommendation yet.
        checks = exc.checks
        activates_when = exc.activates_when
    except ValueError as exc:
        error = str(exc)
    if can_access_personal(request, settings):
        with FPLClient(settings) as client:
            team = linked_team_data(db, client, settings, rows=rows)
    else:
        team = None
    return templates.TemplateResponse(request=request, name="recommendation.html", context={"recommendation": recommendation, "error": error, "checks": checks, "activates_when": activates_when, "budget": budget_value, "strategy": strategy, "strategies": STRATEGIES, "comparisons": comparisons, "my_team": team, "transfer_plan": transfer_plan(team, recommendation) if team and recommendation else None})


@router.get("/movers", response_class=HTMLResponse)
def movers_page(
    request: Request,
    period: str = "7D",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if period not in {"1D", "7D", "30D"}:
        period = "7D"
    # A single source of movement classification, so the headline lists cannot
    # disagree with the per-metric tables below them.
    movers = movers_data(db, settings.current_season, period)
    return templates.TemplateResponse(
        request=request,
        name="movers.html",
        context={
            "period": period,
            "risers": movers["value_risers"],
            "fallers": movers["value_fallers"],
            "window": movers["value_window"],
            "movers": movers,
        },
    )


@router.get("/compare", response_class=HTMLResponse)
def compare(
    request: Request,
    ids: Annotated[list[int] | None, Query()] = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    all_rows = filtered_players(db, season=settings.current_season, sort="reliable_value")
    selected_ids = (ids or [])[:5]
    selected = [
        row for row in all_rows
        if row["player"].id in selected_ids
    ]
    return templates.TemplateResponse(
        request=request,
        name="compare.html",
        context={
            "all_rows": all_rows,
            "selected": selected,
            "selected_ids": selected_ids,
            "comparison_metrics": _comparison_metrics(selected),
        },
    )


@router.get("/schema", response_class=HTMLResponse)
def schema_page(
    request: Request,
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request=request,
        name="schema.html",
        context={"changes": recent_schema_changes(db, 200)},
    )


@router.get("/exports/current.csv")
def export_csv(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    return Response(
        csv_bytes(db, settings.current_season, settings.reliability_sample_minutes),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                'attachment; filename="fpl_value_rankings.csv"'
            )
        },
    )


@router.get("/exports/current.xlsx")
def export_xlsx(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    return Response(
        xlsx_bytes(db, settings.current_season, settings.reliability_sample_minutes),
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                'attachment; filename="fpl_value_analysis.xlsx"'
            )
        },
    )


@router.post("/admin/refresh")
def manual_refresh(
    request: Request,
    csrf_token: Annotated[str, Form()] = "",
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    if not valid_csrf(request, csrf_token):
        audit.record(db, "admin.refresh.denied", request)
        raise HTTPException(403, "Invalid CSRF token")
    audit.record(db, "admin.refresh", request)
    with FPLClient(settings) as client:
        refresh_data(db, settings, client)
    clear_remote_team_cache(entry_id=settings.fpl_entry_id)
    return RedirectResponse("/", status_code=303)
