from __future__ import annotations

import io
from typing import Annotated

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

from app.api.fpl_client import FPLClient
from app.config import Settings, get_settings
from app.db.models import Player
from app.db.session import get_db
from app.services.exports import csv_bytes, xlsx_bytes
from app.services.queries import (
    dashboard_data,
    filtered_players,
    latest_rows,
    movers_data,
    diagnostics_data,
    player_history,
    recent_schema_changes,
)
from app.services.refresh import refresh_data
from app.services.my_team import linked_team_data, transfer_plan
from app.services.team_recommender import STRATEGIES, recommend_team_cached
from app.web.auth import safe_next_path, valid_credentials, valid_csrf


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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
            "auth_enabled": settings.auth_enabled,
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
):
    if valid_csrf(request, csrf_token) and valid_credentials(settings, username, password):
        request.session["authenticated"] = True
        return RedirectResponse(safe_next_path(next), status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "next": safe_next_path(next),
            "error": "Invalid username or password.",
            "auth_enabled": settings.auth_enabled,
        },
        status_code=401,
    )


@router.post("/logout")
def logout(request: Request, csrf_token: Annotated[str, Form()] = ""):
    if not valid_csrf(request, csrf_token):
        raise HTTPException(403, "Invalid CSRF token")
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    data = dashboard_data(db)
    data["my_team"] = linked_team_data(db, FPLClient(settings), settings)
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
    db: Session = Depends(get_db),
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
    rows = filtered_players(db, position=position or None, status=status, sort=effective_sort, **values)
    if view == "transfers":
        rows = [row for row in rows
                if row["snapshot"].availability_factor > 0
                and (advanced["min_expected_minutes"] is None or row["snapshot"].expected_minutes >= advanced["min_expected_minutes"])
                and (advanced["min_reliable_percentile"] is None or (row["snapshot"].reliable_percentile or 0) >= advanced["min_reliable_percentile"])
                and (advanced["min_forward_percentile"] is None or (row["snapshot"].forward_percentile or 0) >= advanced["min_forward_percentile"])]
    title, description, _ = views[view]
    return templates.TemplateResponse(request=request, name="spreadsheet.html", context={
        "rows": rows, "view": view, "view_title": title, "view_description": description,
        "position": position or "", "status": status or "", "sort": effective_sort,
        **values, **advanced,
    })


@router.get("/players/{player_id}", response_class=HTMLResponse)
def player_detail(
    request: Request,
    player_id: int,
    db: Session = Depends(get_db),
):
    player = db.get(Player, player_id)
    if player is None:
        raise HTTPException(404, "Player not found")
    history = player_history(db, player_id)
    if not history:
        raise HTTPException(404, "No player history available")
    return templates.TemplateResponse(
        request=request,
        name="player_detail.html",
        context={
            "player": player,
            "current": history[-1],
            "history": history,
            "comparison_options": [
                row for row in filtered_players(db, sort="reliable_value")
                if row["player"].id != player_id
            ],
        },
    )


@router.get("/forward", response_class=HTMLResponse)
def forward_page(
    request: Request,
    db: Session = Depends(get_db),
):
    rows = filtered_players(db, sort="forward_value")
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
):
    rows = filtered_players(db, sort="rotation_risk")
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
):
    rows = filtered_players(db, position=position, max_price=max_price, max_rotation=max_rotation, sort="forward_value")
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


@router.get("/diagnostics", response_class=HTMLResponse)
def diagnostics(request: Request, db: Session = Depends(get_db)):
    data = diagnostics_data(db)
    return templates.TemplateResponse(request=request, name="diagnostics.html", context=data)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={},
    )


@router.get("/my-team", response_class=HTMLResponse)
def my_team_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    return templates.TemplateResponse(
        request=request,
        name="my_team.html",
        context={"my_team": linked_team_data(db, FPLClient(settings), settings)},
    )


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
    try:
        recommendation = recommend_team_cached(latest_rows(db), budget_value, strategy)
    except ValueError as exc:
        error = str(exc)
    team = linked_team_data(db, FPLClient(settings), settings)
    return templates.TemplateResponse(request=request, name="recommendation.html", context={"recommendation": recommendation, "error": error, "budget": budget_value, "strategy": strategy, "strategies": STRATEGIES, "my_team": team, "transfer_plan": transfer_plan(team, recommendation)})


@router.get("/movers", response_class=HTMLResponse)
def movers_page(
    request: Request,
    period: str = "7D",
    db: Session = Depends(get_db),
):
    if period not in {"1D", "7D", "30D"}:
        period = "7D"
    rows = filtered_players(db, sort="reliable_value")
    comparable = [row for row in rows if row["history"][period]["delta_value"] is not None]
    risers = sorted(
        comparable,
        key=lambda row: row["history"][period]["delta_value"],
        reverse=True,
    )
    fallers = sorted(
        comparable,
        key=lambda row: row["history"][period]["delta_value"],
    )
    return templates.TemplateResponse(
        request=request,
        name="movers.html",
        context={
            "period": period,
            "risers": risers,
            "fallers": fallers,
            "movers": movers_data(db, period),
        },
    )


@router.get("/compare", response_class=HTMLResponse)
def compare(
    request: Request,
    ids: Annotated[list[int] | None, Query()] = None,
    db: Session = Depends(get_db),
):
    all_rows = filtered_players(db, sort="reliable_value")
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
def export_csv(db: Session = Depends(get_db)):
    return Response(
        csv_bytes(db),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                'attachment; filename="fpl_value_rankings.csv"'
            )
        },
    )


@router.get("/exports/current.xlsx")
def export_xlsx(db: Session = Depends(get_db)):
    return Response(
        xlsx_bytes(db),
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
        raise HTTPException(403, "Invalid CSRF token")
    refresh_data(db, settings, FPLClient(settings))
    return RedirectResponse("/", status_code=303)
