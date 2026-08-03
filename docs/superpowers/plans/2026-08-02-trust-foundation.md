# Trust Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make FPL Value Studio state truthfully what it knows — fail-closed access control, an explicit season identity, a real distinction between zero and unavailable, a centralised season-state and readiness service, and repairs to the four defects those problems cause.

**Architecture:** Additive changes to the existing FastAPI + SQLAlchemy + Jinja application. One new Alembic revision (`0004`) adds a `season` column, a `metric_status` column, a `gameweeks` table and an `audit_events` table, and relaxes derived metric columns to nullable. Three new pure service modules (`metric_contracts`, `season_state`, `access`) hold logic that is currently scattered or absent. No new web framework, ORM, template engine or optimiser.

**Tech Stack:** Python 3.12, FastAPI, Starlette sessions, Jinja2, SQLAlchemy 2, Alembic, pytest, Docker Compose, Railway.

## Global Constraints

- Reuse the existing FPL client, refresh pipeline, `PlayerSnapshot` table and `team_recommender`. Do not create a second one of any of them.
- Run tests with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q`. An unrelated installed `dash` pytest plugin crashes collection on Windows without it.
- Alembic revision id for this increment is `0004`, `down_revision = "0003"`.
- Migrations must be idempotent and inspect existing columns before adding, matching the style of `alembic/versions/0003_snapshot_detail_indexes.py`.
- SQLite is the local and test database; PostgreSQL is production. Any migration step must work on both. SQLite cannot drop or alter columns in place, so use `batch_alter_table` for nullability changes.
- No secrets in code, logs, templates or committed files.
- Do not weaken existing tests to make them pass.
- Default `ACCESS_MODE` is `demo`. Personal and mutation routes require authentication in every mode.
- Season labels use the FPL format `YYYY/YY`, for example `2026/27`.

---

### Task 1: Access mode configuration

**Files:**
- Modify: `app/config.py:16-51`
- Modify: `.env.example`
- Test: `tests/test_access.py` (create)

**Interfaces:**
- Produces: `Settings.access_mode: str` with values `demo` | `private` | `local`; `Settings.credentials_configured -> bool`; `Settings.require_auth_for(protection: str) -> bool`; `Settings.session_max_age_seconds: int`; `Settings.login_max_attempts: int`; `Settings.login_lockout_seconds: int`; `Settings.current_season: str`.
- The existing `Settings.auth_enabled` property is removed. `tests/test_web.py:47` asserts it and must be updated in this task.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_access.py
import pytest
from app.config import Settings


def test_demo_mode_is_the_default_and_protects_personal_routes():
    settings = Settings(app_username=None, app_password=None)
    assert settings.access_mode == "demo"
    assert settings.require_auth_for("PERSONAL") is True
    assert settings.require_auth_for("MUTATION") is True
    assert settings.require_auth_for("ANALYTICS") is False
    assert settings.require_auth_for("PUBLIC") is False


def test_private_mode_protects_analytics_too():
    settings = Settings(access_mode="private", app_username="a", app_password="b")
    assert settings.require_auth_for("ANALYTICS") is True
    assert settings.require_auth_for("PUBLIC") is False


def test_local_mode_requires_sqlite():
    with pytest.raises(ValueError, match="ACCESS_MODE=local"):
        Settings(access_mode="local", database_url="postgresql://user:pw@host/db")
    settings = Settings(access_mode="local", database_url="sqlite:///./data/fpl.db")
    assert settings.require_auth_for("PERSONAL") is False


def test_missing_credentials_do_not_disable_protection():
    settings = Settings(access_mode="private", app_username=None, app_password=None)
    assert settings.credentials_configured is False
    assert settings.require_auth_for("PERSONAL") is True


def test_unknown_access_mode_is_rejected():
    with pytest.raises(ValueError):
        Settings(access_mode="everyone")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_access.py -q`
Expected: FAIL with `AttributeError` / `ValidationError` for `access_mode`.

- [ ] **Step 3: Implement**

In `app/config.py`, add to `Settings` and delete the `auth_enabled` property:

```python
ACCESS_MODES = ("demo", "private", "local")
PROTECTION_CLASSES = ("PUBLIC", "ANALYTICS", "PERSONAL", "MUTATION")

    access_mode: str = "demo"
    current_season: str = "2026/27"
    session_max_age_seconds: int = Field(default=43200, ge=300)
    login_max_attempts: int = Field(default=5, ge=1)
    login_lockout_seconds: int = Field(default=900, ge=30)
```

Extend `validate_model_configuration` with:

```python
        if self.access_mode not in ACCESS_MODES:
            raise ValueError(f"ACCESS_MODE must be one of {', '.join(ACCESS_MODES)}")
        if self.access_mode == "local" and not self.database_url.startswith("sqlite"):
            raise ValueError(
                "ACCESS_MODE=local disables authentication and is only permitted "
                "with a SQLite database"
            )
```

Add the two properties:

```python
    @property
    def credentials_configured(self) -> bool:
        return bool(self.app_username and self.app_password)

    def require_auth_for(self, protection: str) -> bool:
        if protection == "PUBLIC":
            return False
        if self.access_mode == "local":
            return False
        if protection in ("PERSONAL", "MUTATION"):
            return True
        return self.access_mode == "private"
```

- [ ] **Step 4: Update the existing assertion in `tests/test_web.py:45-52`**

Replace `assert settings.auth_enabled` with `assert settings.credentials_configured`.

- [ ] **Step 5: Add the new variables to `.env.example`**

```
ACCESS_MODE=demo
CURRENT_SEASON=2026/27
SESSION_MAX_AGE_SECONDS=43200
LOGIN_MAX_ATTEMPTS=5
LOGIN_LOCKOUT_SECONDS=900
```

- [ ] **Step 6: Run the full suite**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/config.py .env.example tests/test_access.py tests/test_web.py
git commit -m "Add fail-closed ACCESS_MODE configuration"
```

---

### Task 2: Route protection, privacy by exclusion, login throttling and audit log

**Files:**
- Modify: `app/web/auth.py`
- Modify: `app/main.py:18-24`
- Modify: `app/web/routes.py:59-126,362-392,461-500`
- Modify: `app/templates/base.html:12-30`
- Modify: `app/templates/login.html`
- Create: `app/web/audit.py`
- Modify: `app/db/models.py` (append `AuditEvent`)
- Test: `tests/test_access.py`

**Interfaces:**
- Consumes: `Settings.require_auth_for`, `Settings.credentials_configured`, `Settings.login_max_attempts`, `Settings.login_lockout_seconds`.
- Produces: `app.web.auth.PROTECTION_MAP: tuple[tuple[str, str], ...]`; `app.web.auth.protection_for(path: str) -> str`; `app.web.auth.is_authenticated(request) -> bool`; `app.web.audit.record(db, action, request, actor=None, detail=None) -> None`; `AuditEvent` model with columns `id, occurred_at, action, actor, path, client, detail`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_access.py
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.web.auth import protection_for


def _client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'access.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    return TestClient(app, follow_redirects=False)


def test_protection_classes_cover_personal_and_mutation_routes():
    assert protection_for("/health") == "PUBLIC"
    assert protection_for("/static/app.css") == "PUBLIC"
    assert protection_for("/login") == "PUBLIC"
    assert protection_for("/my-team") == "PERSONAL"
    assert protection_for("/admin/refresh") == "MUTATION"
    assert protection_for("/spreadsheet") == "ANALYTICS"
    assert protection_for("/") == "ANALYTICS"


def test_anonymous_visitor_cannot_reach_personal_routes_or_trigger_refresh(tmp_path):
    client = _client(tmp_path)
    try:
        assert client.get("/my-team").status_code == 303
        assert client.post("/admin/refresh", data={"csrf_token": "x"}).status_code in (303, 403)
        assert client.get("/health").status_code == 200
        assert client.get("/spreadsheet").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_dashboard_does_not_leak_personal_data_to_anonymous_visitors(tmp_path):
    client = _client(tmp_path)
    try:
        body = client.get("/").text
        assert "entry" not in body.lower() or "entry_id" not in body.lower()
        assert "Log out" not in body
        assert "Sign in" in body
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_access.py -q`
Expected: FAIL with `ImportError: cannot import name 'protection_for'`.

- [ ] **Step 3: Rewrite `app/web/auth.py`**

```python
from __future__ import annotations

import secrets
import time
from collections import defaultdict

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from app.config import Settings


PROTECTION_MAP: tuple[tuple[str, str], ...] = (
    ("/health", "PUBLIC"),
    ("/login", "PUBLIC"),
    ("/logout", "PUBLIC"),
    ("/static", "PUBLIC"),
    ("/my-team", "PERSONAL"),
    ("/admin/", "MUTATION"),
)


def protection_for(path: str) -> str:
    for prefix, protection in PROTECTION_MAP:
        if path == prefix or path.startswith(prefix.rstrip("/") + "/") or path.startswith(prefix):
            return protection
    return "ANALYTICS"


def is_authenticated(request: Request) -> bool:
    return bool(request.session.get("authenticated"))


class _LoginThrottle:
    """In-process failed-login counter. Per-worker, which is sufficient for a
    single-container deployment and degrades safely to no throttling if the
    process restarts."""

    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = defaultdict(list)

    def locked_out(self, key: str, max_attempts: int, window: int) -> bool:
        now = time.monotonic()
        recent = [stamp for stamp in self._failures[key] if now - stamp < window]
        self._failures[key] = recent
        return len(recent) >= max_attempts

    def record_failure(self, key: str) -> None:
        self._failures[key].append(time.monotonic())

    def clear(self, key: str) -> None:
        self._failures.pop(key, None)


login_throttle = _LoginThrottle()


class AuthenticationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings):
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):
        request.session.setdefault("csrf_token", secrets.token_urlsafe(32))
        protection = protection_for(request.url.path)
        request.state.protection = protection
        request.state.authenticated = is_authenticated(request)

        if not self.settings.require_auth_for(protection):
            return await call_next(request)
        if request.state.authenticated:
            return await call_next(request)

        return RedirectResponse(
            url=f"/login?next={request.url.path}",
            status_code=303,
        )


def valid_credentials(settings: Settings, username: str, password: str) -> bool:
    if not settings.credentials_configured:
        return False
    return (
        secrets.compare_digest(username, settings.app_username or "")
        and secrets.compare_digest(password, settings.app_password or "")
    )


def valid_csrf(request: Request, token: str) -> bool:
    expected = str(request.session.get("csrf_token") or "")
    return bool(expected and token) and secrets.compare_digest(expected, token)


def safe_next_path(value: str | None) -> str:
    candidate = value or "/"
    return candidate if candidate.startswith("/") and not candidate.startswith("//") else "/"


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"
```

Note the behaviour change in `valid_credentials`: with no credentials configured it now returns `False` instead of `True`. That is the fail-closed fix.

- [ ] **Step 4: Add the `AuditEvent` model to `app/db/models.py`**

```python
class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(100), default="anonymous")
    path: Mapped[str] = mapped_column(String(300), default="")
    client: Mapped[str] = mapped_column(String(60), default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
```

- [ ] **Step 5: Create `app/web/audit.py`**

```python
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.db.models import AuditEvent

logger = logging.getLogger(__name__)

_SENSITIVE = {"password", "csrf_token", "api_key", "session_secret", "token"}


def _clean(detail: dict[str, Any] | None) -> dict[str, Any]:
    return {
        key: ("[redacted]" if key.casefold() in _SENSITIVE else value)
        for key, value in (detail or {}).items()
    }


def record(
    db: Session,
    action: str,
    request: Request,
    actor: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Write an audit row. Never raises: an audit failure must not break a request."""
    payload = _clean(detail)
    try:
        db.add(
            AuditEvent(
                occurred_at=datetime.now(timezone.utc),
                action=action,
                actor=actor or "anonymous",
                path=str(request.url.path),
                client=request.client.host if request.client else "",
                detail=payload,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("audit_write_failed action=%s", action)
    logger.info("audit action=%s path=%s", action, request.url.path)
```

- [ ] **Step 6: Update `app/main.py` for cookie hardening**

```python
app.add_middleware(AuthenticationMiddleware, settings=settings)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=settings.production_cookie_secure,
    max_age=settings.session_max_age_seconds,
)
```

`SessionMiddleware` sets `HttpOnly` on its cookie by default; `https_only` sets `Secure`.

- [ ] **Step 7: Apply privacy by exclusion in `app/web/routes.py`**

In `dashboard`, replace the unconditional personal fetch:

```python
    data = dashboard_data(db)
    data["authenticated"] = is_authenticated(request)
    data["my_team"] = (
        linked_team_data(db, FPLClient(settings), settings)
        if data["authenticated"]
        else None
    )
```

In `my_team_page`, no change is needed because the middleware already refuses anonymous requests, but add the guard as defence in depth:

```python
    if not is_authenticated(request):
        return RedirectResponse("/login?next=/my-team", status_code=303)
```

In `login`, add throttling and auditing:

```python
    key = client_key(request)
    if login_throttle.locked_out(key, settings.login_max_attempts, settings.login_lockout_seconds):
        audit.record(db, "login.throttled", request)
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
```

`login` and `logout` gain a `db: Session = Depends(get_db)` parameter. `logout` records `logout`. `manual_refresh` records `admin.refresh` after the CSRF check.

- [ ] **Step 8: Update `app/templates/base.html`**

Wrap the personal navigation entry and the logout form so anonymous visitors see a sign-in link instead:

```jinja
      <a href="/">Dashboard</a>
      <a href="/spreadsheet">Master Spreadsheet</a>
      {% if request.state.authenticated %}<a href="/my-team">My Team</a>{% endif %}
      <a href="/recommendation">AI Recommender</a>
      <a href="/differentials">Differentials</a>
      <a href="/transfer-market">Transfer Market</a>
      <a href="/templates">Templates</a>
```

and

```jinja
    {% if request.state.authenticated %}
    <form method="post" action="/logout">
      <input type="hidden" name="csrf_token" value="{{ request.session.get('csrf_token', '') }}">
      <button class="ghost small" type="submit">Log out</button>
    </form>
    {% else %}
    <a class="ghost small" href="/login">Sign in</a>
    {% endif %}
```

- [ ] **Step 9: Update `app/templates/login.html`**

Replace the `auth_enabled` reference with `credentials_configured`, and show a notice when it is false: `No credentials are configured. Set APP_USERNAME and APP_PASSWORD to enable sign-in.` Update the `login_page` route context accordingly.

- [ ] **Step 10: Guard the dashboard template**

In `app/templates/dashboard.html`, wrap the block that renders `my_team` in `{% if my_team %}`.

- [ ] **Step 11: Run the suite**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q`
Expected: PASS. `tests/test_web.py` will need `/my-team` moved out of the "expect 200" list into an "expect 303" assertion.

- [ ] **Step 12: Commit**

```bash
git add app/web/auth.py app/web/audit.py app/main.py app/web/routes.py app/db/models.py app/templates tests/
git commit -m "Protect personal and mutation routes and audit access"
```

---

### Task 3: Migration 0004 — season, metric status, gameweeks, audit events, nullable metrics

**Files:**
- Create: `alembic/versions/0004_trust_foundation.py`
- Modify: `app/db/models.py`
- Test: `tests/test_migration.py` (create)

**Interfaces:**
- Produces: `player_snapshots.season` (String(9), indexed), `player_snapshots.metric_status` (JSON), `gameweek_history.season` (String(9)), unique constraint `uq_player_season_gameweek` on `(player_id, season, gameweek)`, tables `gameweeks` and `audit_events`, and nullable derived metric columns.
- The `Gameweek` model: `id, season, number, name, deadline_time, finished, data_checked, is_current, is_next, raw`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migration.py
import sqlalchemy as sa
from alembic import command
from alembic.config import Config


NULLABLE_METRICS = {
    "value", "reliability_factor", "reliable_value", "start_rate", "points_per_90",
    "points_per_start", "points_per_team_match", "points_per_minute",
    "minutes_per_team_match", "average_minutes_per_start", "value_per_90",
    "expected_minutes", "projected_points_5", "forward_value",
    "average_fixture_difficulty",
}


def test_migration_applies_and_relaxes_metric_columns(tmp_path):
    url = f"sqlite:///{tmp_path / 'migrate.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    engine = sa.create_engine(url)
    inspector = sa.inspect(engine)
    assert "gameweeks" in inspector.get_table_names()
    assert "audit_events" in inspector.get_table_names()

    columns = {column["name"]: column for column in inspector.get_columns("player_snapshots")}
    assert "season" in columns
    assert "metric_status" in columns
    for name in NULLABLE_METRICS:
        assert columns[name]["nullable"] is True, f"{name} must be nullable"

    history = {column["name"] for column in inspector.get_columns("gameweek_history")}
    assert "season" in history
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_migration.py -q`
Expected: FAIL — `gameweeks` not in table names.

- [ ] **Step 3: Write `alembic/versions/0004_trust_foundation.py`**

```python
"""Season identity, metric status, gameweeks, audit events, nullable metrics."""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

NULLABLE_METRICS = [
    "value", "reliability_factor", "reliable_value", "start_rate", "points_per_90",
    "points_per_start", "points_per_team_match", "points_per_minute",
    "minutes_per_team_match", "average_minutes_per_start", "value_per_90",
    "expected_minutes", "projected_points_5", "forward_value",
    "average_fixture_difficulty",
]
DEFAULT_SEASON = "2026/27"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "gameweeks" not in tables:
        op.create_table(
            "gameweeks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("season", sa.String(9), nullable=False),
            sa.Column("number", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(60), nullable=False, server_default=""),
            sa.Column("deadline_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("data_checked", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("is_next", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("raw", sa.JSON(), nullable=False, server_default="{}"),
            sa.UniqueConstraint("season", "number", name="uq_gameweek_season_number"),
        )

    if "audit_events" not in tables:
        op.create_table(
            "audit_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("action", sa.String(50), nullable=False),
            sa.Column("actor", sa.String(100), nullable=False, server_default="anonymous"),
            sa.Column("path", sa.String(300), nullable=False, server_default=""),
            sa.Column("client", sa.String(60), nullable=False, server_default=""),
            sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"),
        )
        op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])
        op.create_index("ix_audit_events_action", "audit_events", ["action"])

    snapshot_columns = {column["name"] for column in inspector.get_columns("player_snapshots")}
    if "season" not in snapshot_columns:
        op.add_column(
            "player_snapshots",
            sa.Column("season", sa.String(9), nullable=False, server_default=DEFAULT_SEASON),
        )
        op.create_index("ix_snapshot_season_time", "player_snapshots", ["season", "captured_at"])
    if "metric_status" not in snapshot_columns:
        op.add_column(
            "player_snapshots",
            sa.Column("metric_status", sa.JSON(), nullable=False, server_default="{}"),
        )

    with op.batch_alter_table("player_snapshots") as batch:
        for name in NULLABLE_METRICS:
            if name in snapshot_columns:
                batch.alter_column(name, existing_type=sa.Float(), nullable=True)

    history_columns = {column["name"] for column in inspector.get_columns("gameweek_history")}
    if "season" not in history_columns:
        op.add_column(
            "gameweek_history",
            sa.Column("season", sa.String(9), nullable=False, server_default=DEFAULT_SEASON),
        )
    with op.batch_alter_table("gameweek_history") as batch:
        try:
            batch.drop_constraint("uq_player_gameweek", type_="unique")
        except Exception:
            pass
        batch.create_unique_constraint(
            "uq_player_season_gameweek", ["player_id", "season", "gameweek"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    with op.batch_alter_table("gameweek_history") as batch:
        try:
            batch.drop_constraint("uq_player_season_gameweek", type_="unique")
        except Exception:
            pass
        batch.create_unique_constraint("uq_player_gameweek", ["player_id", "gameweek"])
        batch.drop_column("season")

    with op.batch_alter_table("player_snapshots") as batch:
        for name in NULLABLE_METRICS:
            batch.alter_column(
                name, existing_type=sa.Float(), nullable=False, server_default="0"
            )
        batch.drop_column("metric_status")
        batch.drop_column("season")

    tables = set(inspector.get_table_names())
    if "audit_events" in tables:
        op.drop_table("audit_events")
    if "gameweeks" in tables:
        op.drop_table("gameweeks")
```

- [ ] **Step 4: Update `app/db/models.py` to match**

Add to `PlayerSnapshot`: `season: Mapped[str] = mapped_column(String(9), nullable=False, index=True)` and `metric_status: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)`. Add `Index("ix_snapshot_season_time", "season", "captured_at")` to `__table_args__`. Change each metric in `NULLABLE_METRICS` to `Mapped[float | None] = mapped_column(Float, nullable=True, default=None)`.

Add to `GameweekHistory`: `season: Mapped[str] = mapped_column(String(9), nullable=False)`. Change its unique constraint to `UniqueConstraint("player_id", "season", "gameweek", name="uq_player_season_gameweek")`.

Add the `Gameweek` model:

```python
class Gameweek(Base):
    __tablename__ = "gameweeks"
    __table_args__ = (
        UniqueConstraint("season", "number", name="uq_gameweek_season_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[str] = mapped_column(String(9), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(60), default="")
    deadline_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    data_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    is_next: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
```

- [ ] **Step 5: Run the migration test and the full suite**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Verify the downgrade path**

Run: `python -c "from alembic.config import Config; from alembic import command; c=Config('alembic.ini'); c.set_main_option('sqlalchemy.url','sqlite:///./data/_mig.db'); command.upgrade(c,'head'); command.downgrade(c,'0003'); command.upgrade(c,'head'); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/0004_trust_foundation.py app/db/models.py tests/test_migration.py
git commit -m "Add season identity, metric status, gameweeks and audit tables"
```

---

### Task 4: Metric contracts and null-returning calculations

**Files:**
- Create: `app/analytics/contracts.py`
- Modify: `app/analytics/metrics.py:63-212`
- Test: `tests/test_contracts.py` (create)
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces: `MetricStatus` string constants; `MetricContract` dataclass with fields `name, formula, inputs, input_seasons, target_season, unit, valid_range, minimum_sample, null_behaviour, version`; `CONTRACTS: dict[str, MetricContract]`; `MetricValue` dataclass with `value: float | None, status: str, reason: str, unit: str, precision: int` and a `.display` property.
- Consumes: nothing from earlier tasks.
- `metrics.reliability_factor`, `metrics.expected_minutes` and `metrics.project_next_fixtures` change return type from `float` to `float | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts.py
from app.analytics.contracts import CONTRACTS, MetricStatus, MetricValue


def test_every_contract_declares_its_provenance():
    assert CONTRACTS
    for name, contract in CONTRACTS.items():
        assert contract.name == name
        assert contract.formula
        assert contract.inputs
        assert contract.unit
        assert contract.version
        assert contract.null_behaviour


def test_real_zero_and_missing_render_differently():
    real = MetricValue(0.0, MetricStatus.REAL_ZERO, "Played and scored no points", "pts", 2)
    missing = MetricValue(None, MetricStatus.NOT_YET_AVAILABLE, "No matches played yet", "pts", 2)
    assert real.display == "0.00"
    assert missing.display == "Not available — No matches played yet"
    assert real.is_value is True
    assert missing.is_value is False
```

```python
# append to tests/test_metrics.py
from app.analytics.metrics import expected_minutes, project_next_fixtures, reliability_factor


def test_metrics_return_none_when_inputs_are_unavailable():
    assert reliability_factor(0, 0, 0) is None
    assert expected_minutes(0, 0, 0, 1.0) is None
    assert project_next_fixtures(
        form=0.0, points_per_game=0.0, points_per_90=0.0,
        expected_minutes_value=None, availability=1.0, fixtures=[],
    ) is None


def test_metrics_return_a_real_zero_when_the_measurement_is_genuine():
    assert reliability_factor(0, 0, 3) == 0.0
    assert expected_minutes(0, 0, 3, 1.0) == 0.0
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_contracts.py tests/test_metrics.py -q`
Expected: FAIL — module `app.analytics.contracts` not found.

- [ ] **Step 3: Create `app/analytics/contracts.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field


class MetricStatus:
    REAL_ZERO = "real_zero"
    VALUE = "value"
    MISSING = "missing"
    NOT_YET_AVAILABLE = "not_yet_available"
    NOT_APPLICABLE = "not_applicable"
    NOT_CALCULATED = "not_calculated"
    FAILED = "failed"
    STALE = "stale"
    INVALID = "invalid"
    SUPPRESSED_LOW_CONFIDENCE = "suppressed_low_confidence"


VALUE_STATUSES = frozenset({MetricStatus.VALUE, MetricStatus.REAL_ZERO})

STATUS_LABELS = {
    MetricStatus.MISSING: "Not available",
    MetricStatus.NOT_YET_AVAILABLE: "Not available",
    MetricStatus.NOT_APPLICABLE: "Not applicable",
    MetricStatus.NOT_CALCULATED: "Not calculated",
    MetricStatus.FAILED: "Calculation failed",
    MetricStatus.STALE: "Stale",
    MetricStatus.INVALID: "Invalid source data",
    MetricStatus.SUPPRESSED_LOW_CONFIDENCE: "Suppressed",
}


@dataclass(frozen=True)
class MetricValue:
    value: float | None
    status: str
    reason: str = ""
    unit: str = ""
    precision: int = 2

    @property
    def is_value(self) -> bool:
        return self.status in VALUE_STATUSES and self.value is not None

    @property
    def display(self) -> str:
        if self.is_value:
            return f"{self.value:.{self.precision}f}"
        label = STATUS_LABELS.get(self.status, "Not available")
        return f"{label} — {self.reason}" if self.reason else label


@dataclass(frozen=True)
class MetricContract:
    name: str
    formula: str
    inputs: tuple[str, ...]
    input_seasons: tuple[str, ...]
    target_season: str
    unit: str
    valid_range: tuple[float, float]
    minimum_sample: int
    null_behaviour: str
    version: str = "1.0.0"


def _contract(**kwargs) -> MetricContract:
    return MetricContract(**kwargs)


CONTRACTS: dict[str, MetricContract] = {
    "value": _contract(
        name="value",
        formula="total_points / price",
        inputs=("total_points", "price"),
        input_seasons=("current",),
        target_season="current",
        unit="pts per £m",
        valid_range=(0.0, 200.0),
        minimum_sample=0,
        null_behaviour="Null when price is not positive.",
    ),
    "reliability_factor": _contract(
        name="reliability_factor",
        formula="min(minutes/sample,1) * (0.60*minutes/(team_matches*90) + 0.40*starts/team_matches)",
        inputs=("minutes", "starts", "team_matches"),
        input_seasons=("current",),
        target_season="current",
        unit="ratio",
        valid_range=(0.0, 1.0),
        minimum_sample=1,
        null_behaviour="Null when the team has played no matches this season.",
    ),
    "reliable_value": _contract(
        name="reliable_value",
        formula="value * reliability_factor",
        inputs=("value", "reliability_factor"),
        input_seasons=("current",),
        target_season="current",
        unit="pts per £m",
        valid_range=(0.0, 200.0),
        minimum_sample=1,
        null_behaviour="Null when either input is null.",
    ),
    "start_rate": _contract(
        name="start_rate",
        formula="100 * starts / team_matches",
        inputs=("starts", "team_matches"),
        input_seasons=("current",),
        target_season="current",
        unit="%",
        valid_range=(0.0, 100.0),
        minimum_sample=1,
        null_behaviour="Null when the team has played no matches this season.",
    ),
    "expected_minutes": _contract(
        name="expected_minutes",
        formula="(0.65*minutes/team_matches + 0.35*90*starts/team_matches) * availability",
        inputs=("minutes", "starts", "team_matches", "availability_factor"),
        input_seasons=("current",),
        target_season="current",
        unit="minutes",
        valid_range=(0.0, 90.0),
        minimum_sample=1,
        null_behaviour="Null before the season starts; a preseason estimator is required instead.",
    ),
    "projected_points_5": _contract(
        name="projected_points_5",
        formula="sum over upcoming fixtures of baseline * minute_factor * availability * difficulty * home",
        inputs=("form", "points_per_game", "points_per_90", "expected_minutes", "upcoming_fixtures"),
        input_seasons=("current",),
        target_season="current",
        unit="pts",
        valid_range=(0.0, 100.0),
        minimum_sample=1,
        null_behaviour="Null when no upcoming fixtures are known or expected minutes are null.",
    ),
    "forward_value": _contract(
        name="forward_value",
        formula="projected_points_5 / price",
        inputs=("projected_points_5", "price"),
        input_seasons=("current",),
        target_season="current",
        unit="pts per £m",
        valid_range=(0.0, 100.0),
        minimum_sample=1,
        null_behaviour="Null when the projection is null or price is not positive.",
    ),
    "rotation_risk": _contract(
        name="rotation_risk",
        formula="100 * weighted mean of (1 - start share) and (1 - minute share)",
        inputs=("minutes", "starts", "team_matches"),
        input_seasons=("current",),
        target_season="current",
        unit="score",
        valid_range=(0.0, 100.0),
        minimum_sample=1,
        null_behaviour="Null when the team has played no matches this season.",
    ),
}
```

- [ ] **Step 4: Change `app/analytics/metrics.py` to return `None`**

`reliability_factor`: replace `return 0.0` in the `team_matches <= 0` branch with `return None`.

`expected_minutes`: change the signature to `-> float | None` and replace `return 0.0` with `return None`; guard `availability` being `None`.

`project_next_fixtures`: change `expected_minutes_value: float | None`, change signature to `-> float | None`, and replace the `if not fixtures: return 0.0` guard with:

```python
    if not fixtures or expected_minutes_value is None:
        return None
```

- [ ] **Step 5: Run the tests**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_contracts.py tests/test_metrics.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/analytics/contracts.py app/analytics/metrics.py tests/test_contracts.py tests/test_metrics.py
git commit -m "Declare metric contracts and return null for unavailable metrics"
```

---

### Task 5: Refresh writes nulls, statuses and the season

**Files:**
- Modify: `app/services/refresh.py:275-600`
- Modify: `app/analytics/metrics.py:215-260` (`assign_global_ranks`, `assign_position_ranks`)
- Test: `tests/test_refresh.py`

**Interfaces:**
- Consumes: `CONTRACTS`, `MetricStatus`, `Settings.current_season`.
- Produces: snapshots whose derived metrics are `None` when unavailable, with `metric_status` populated; `assign_global_ranks(rows, metric, rank_key, percentile_key, tier_key, exclusion_key="exclusions") -> dict[str, int]` returning a count of exclusions by reason.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_refresh.py
def test_preseason_refresh_stores_null_metrics_rather_than_zero(preseason_db_and_settings):
    db, settings, client = preseason_db_and_settings
    run = refresh_data(db, settings, client)
    assert run.status == "success"
    snapshot = db.scalars(select(PlayerSnapshot)).first()
    assert snapshot.season == settings.current_season
    assert snapshot.expected_minutes is None
    assert snapshot.start_rate is None
    assert snapshot.reliable_value is None
    assert snapshot.metric_status["expected_minutes"]["status"] == "not_yet_available"
    assert "No matches played" in snapshot.metric_status["expected_minutes"]["reason"]
```

Build `preseason_db_and_settings` as a pytest fixture returning an in-memory SQLite session, `Settings(current_season="2026/27", database_url=...)`, and a stub client whose `bootstrap()` returns two teams, two element types, four players with `minutes=0, starts=0, total_points=0, form="0.0"`, and whose `fixtures()` returns fixtures with `finished=False`. Follow the existing stub-client style already in `tests/test_refresh.py`.

- [ ] **Step 2: Run to verify failure**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_refresh.py -q`
Expected: FAIL — `expected_minutes` is `0.0`, not `None`.

- [ ] **Step 3: Implement in `refresh.py`**

Introduce a helper above `refresh_data`:

```python
def _status(value: float | None, reason_when_null: str, has_sample: bool) -> dict[str, str]:
    if value is None:
        return {"status": MetricStatus.NOT_YET_AVAILABLE, "reason": reason_when_null}
    if value == 0.0:
        return {
            "status": MetricStatus.REAL_ZERO,
            "reason": "Measured as zero" if has_sample else reason_when_null,
        }
    return {"status": MetricStatus.VALUE, "reason": ""}
```

In the per-player loop replace the zero-guarded arithmetic:

```python
            has_matches = matches > 0
            no_matches_reason = "No matches played yet this season"

            value = points / price if price > 0 else None
            p90 = points * 90.0 / minutes if minutes > 0 else None
            ppm = points / minutes if minutes > 0 else None
            pps = points / starts if starts > 0 else None
            pptm = points / matches if has_matches else None
            start_rate = 100.0 * starts / matches if has_matches else None
            mptm = minutes / matches if has_matches else None
            value_p90 = p90 / price if p90 is not None and price > 0 else None
            reliable_factor = reliability_factor(
                minutes, starts, matches, settings.reliability_sample_minutes
            )
            reliable_value = (
                value * reliable_factor
                if value is not None and reliable_factor is not None
                else None
            )
            ...
            exp_minutes = expected_minutes(minutes, starts, matches, availability)
            projected = project_next_fixtures(..., expected_minutes_value=exp_minutes, ...)
            forward_value = projected / price if projected is not None and price > 0 else None
            average_difficulty = (
                round(sum(...) / len(next_fixtures), 2) if next_fixtures else None
            )
```

Every `round(x, n)` in the `computed.append` dict becomes `round(x, n) if x is not None else None`. Add to the dict:

```python
                    "season": settings.current_season,
                    "metric_status": {
                        "value": _status(value, "Price is unavailable", price > 0),
                        "reliability_factor": _status(reliable_factor, no_matches_reason, has_matches),
                        "reliable_value": _status(reliable_value, no_matches_reason, has_matches),
                        "start_rate": _status(start_rate, no_matches_reason, has_matches),
                        "points_per_90": _status(p90, "No minutes played yet this season", minutes > 0),
                        "expected_minutes": _status(exp_minutes, no_matches_reason, has_matches),
                        "projected_points_5": _status(projected, "No upcoming fixtures or expected minutes available", bool(next_fixtures)),
                        "forward_value": _status(forward_value, "No projection available", bool(next_fixtures)),
                        "rotation_risk": _status(risk, no_matches_reason, has_matches),
                    },
```

- [ ] **Step 4: Make ranking record exclusions**

In `metrics.py`, change both rank functions to skip `None` and record why:

```python
def assign_global_ranks(rows, metric, rank_key, percentile_key, tier_key) -> dict[str, int]:
    exclusions: dict[str, int] = {}
    ranked = []
    for row in rows:
        raw = row.get(metric)
        if raw is None:
            exclusions["Metric unavailable"] = exclusions.get("Metric unavailable", 0) + 1
            continue
        if float(raw) <= 0:
            exclusions["No positive score"] = exclusions.get("No positive score", 0) + 1
            continue
        ranked.append(row)
    ranked.sort(
        key=lambda row: (
            -safe_float(row.get(metric)),
            -safe_int(row.get("total_points")),
            str(row.get("player_name", "")).casefold(),
        )
    )
    count = len(ranked)
    for rank, row in enumerate(ranked, start=1):
        row[rank_key] = rank
        row[percentile_key] = percentile(rank, count)
        row[tier_key] = cumulative_tier(rank, count)
    return exclusions
```

Apply the same `None`-skip to `assign_position_ranks` (no return value needed).

In `refresh_data`, capture the returned dict from the `value` ranking and store it on the run:

```python
        value_exclusions = assign_global_ranks(computed, "value", "value_rank", "value_percentile", "value_tier")
        ...
        run.details = {
            "captured_at": captured_at.isoformat(),
            "fixture_count": len(fixtures_payload),
            "season": settings.current_season,
            "ranked_count": sum(1 for row in computed if row.get("value_rank") is not None),
            "ranking_exclusions": value_exclusions,
        }
```

- [ ] **Step 5: Persist gameweeks from the bootstrap `events` payload**

After the fixtures loop in `refresh_data`:

```python
        for item in bootstrap.get("events", []):
            if not isinstance(item, dict):
                continue
            number = safe_int(item.get("id"))
            existing = db.scalar(
                select(Gameweek).where(
                    Gameweek.season == settings.current_season, Gameweek.number == number
                )
            )
            values = {
                "name": str(item.get("name") or f"Gameweek {number}"),
                "deadline_time": _parse_datetime(item.get("deadline_time")),
                "finished": bool(item.get("finished")),
                "data_checked": bool(item.get("data_checked")),
                "is_current": bool(item.get("is_current")),
                "is_next": bool(item.get("is_next")),
                "raw": item,
            }
            if existing is None:
                db.add(Gameweek(season=settings.current_season, number=number, **values))
            else:
                for key, value in values.items():
                    setattr(existing, key, value)
        db.flush()
```

- [ ] **Step 6: Run the suite**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/refresh.py app/analytics/metrics.py tests/test_refresh.py
git commit -m "Store null metrics with status and record ranking exclusions"
```

---

### Task 6: Season-scoped queries and cross-season rejection

**Files:**
- Modify: `app/services/queries.py`
- Modify: `app/services/history_import.py:35-89`
- Modify: `app/web/routes.py` (pass season through)
- Test: `tests/test_season_isolation.py` (create)
- Test: `tests/test_history.py`

**Interfaces:**
- Produces: `CrossSeasonError(Exception)` in `app/services/queries.py`; `latest_rows(db, season)`; `filtered_players(db, *, season, ...)`; `movers_data(db, season, period)`; `dashboard_data(db, season)`; `player_history(db, player_id, season, limit=180)`; `import_history_directory(db, directory, season)`.
- Consumes: `Settings.current_season`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_season_isolation.py
import pytest
from app.services.queries import CrossSeasonError, _history_comparison


class _Snap:
    def __init__(self, season, value, price, ownership, rank, captured_at=None):
        self.season, self.value, self.price = season, value, price
        self.ownership, self.value_rank, self.captured_at = ownership, rank, captured_at


def test_comparing_two_seasons_raises_rather_than_returning_a_number():
    current = _Snap("2026/27", 5.0, 7.0, 10.0, 4)
    previous = _Snap("2025/26", 9.0, 6.5, 22.0, 1)
    with pytest.raises(CrossSeasonError):
        _history_comparison(current, previous)


def test_same_season_comparison_still_works():
    current = _Snap("2026/27", 5.0, 7.0, 10.0, 4)
    previous = _Snap("2026/27", 4.0, 6.5, 8.0, 9)
    result = _history_comparison(current, previous)
    assert result["delta_value"] == 1.0
    assert result["delta_rank"] == 5
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_season_isolation.py -q`
Expected: FAIL — `CrossSeasonError` does not exist.

- [ ] **Step 3: Implement**

In `queries.py`:

```python
class CrossSeasonError(ValueError):
    """Raised when a calculation would mix observations from different seasons."""
```

Add to `_history_comparison`, immediately after the `previous is None` guard:

```python
    current_season = getattr(current, "season", None)
    previous_season = getattr(previous, "season", None)
    if current_season and previous_season and current_season != previous_season:
        raise CrossSeasonError(
            f"Refusing to compare {previous_season} against {current_season}"
        )
```

Handle `None` metric values in the deltas:

```python
    delta_value = (
        round(current.value - previous.value, 3)
        if current.value is not None and previous.value is not None
        else None
    )
```

Add `season: str` as a required keyword to `latest_snapshot_time`, `_historical_map`, `latest_rows`, `filtered_players`, `player_history`, `movers_data`, `diagnostics_data` and `dashboard_data`, and add `.where(PlayerSnapshot.season == season)` to each query. Update `filtered_players` comparisons that use `>=` / `<=` on possibly-null metrics to skip `None` rather than coerce.

In `routes.py`, add `settings: Settings = Depends(get_settings)` where missing and pass `settings.current_season` to every query call.

- [ ] **Step 4: Require an explicit season for history import**

Change the signature to `import_history_directory(db, directory, season)`, pass `season=season` into the `PlayerSnapshot(...)` construction, and update `app/cli.py` to take a `--season` argument for the import command. Update `tests/test_history.py` call sites.

- [ ] **Step 5: Run the suite**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/queries.py app/services/history_import.py app/web/routes.py app/cli.py tests/
git commit -m "Scope queries by season and reject cross-season comparisons"
```

---

### Task 7: Season state and readiness service

**Files:**
- Create: `app/services/season_state.py`
- Test: `tests/test_season_state.py` (create)

**Interfaces:**
- Produces: `SeasonState` constants; `season_state(gameweeks, latest_snapshot_at, now) -> dict`; `Readiness` constants; `readiness_for(feature, inputs) -> dict`; `FEATURES: dict[str, FeatureRequirement]`.
- The `season_state` return shape: `{"state", "label", "current_gameweek", "next_gameweek", "next_deadline", "seconds_to_deadline", "explanation"}`.
- The `readiness_for` return shape: `{"state", "missing", "stale", "invalid", "fallback", "last_success", "activates_when", "explanation"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_season_state.py
from datetime import datetime, timedelta, timezone

from app.services.season_state import (
    FEATURES, Readiness, SeasonState, readiness_for, season_state,
)


class _GW:
    def __init__(self, number, deadline, finished=False, is_current=False, is_next=False):
        self.number, self.deadline_time = number, deadline
        self.finished, self.is_current, self.is_next = finished, is_current, is_next
        self.data_checked = finished


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def test_uninitialized_when_no_gameweeks_exist():
    assert season_state([], None, NOW)["state"] == SeasonState.UNINITIALIZED


def test_preseason_before_the_first_deadline():
    result = season_state([_GW(1, NOW + timedelta(days=12), is_next=True)], NOW, NOW)
    assert result["state"] == SeasonState.PRESEASON
    assert result["next_gameweek"] == 1
    assert result["seconds_to_deadline"] > 0


def test_pre_deadline_within_the_warning_window():
    result = season_state([_GW(3, NOW + timedelta(hours=6), is_next=True)], NOW, NOW)
    assert result["state"] == SeasonState.PRE_DEADLINE


def test_deadline_passed_before_results_are_final():
    gameweeks = [_GW(3, NOW - timedelta(hours=2), is_current=True)]
    assert season_state(gameweeks, NOW, NOW)["state"] == SeasonState.DEADLINE_PASSED


def test_postseason_when_every_gameweek_is_finished():
    gameweeks = [_GW(number, NOW - timedelta(days=40), finished=True) for number in range(1, 39)]
    assert season_state(gameweeks, NOW, NOW)["state"] == SeasonState.POSTSEASON


def test_readiness_reports_missing_inputs_and_activation_condition():
    result = readiness_for("projections", {"projections_available": 0, "player_count": 500})
    assert result["state"] == Readiness.NOT_READY
    assert "projections" in " ".join(result["missing"]).casefold()
    assert result["activates_when"]


def test_readiness_is_ready_when_requirements_are_met():
    result = readiness_for("projections", {"projections_available": 480, "player_count": 500})
    assert result["state"] == Readiness.READY


def test_every_feature_declares_its_requirements():
    for name, feature in FEATURES.items():
        assert feature.required_inputs
        assert feature.supported_states
        assert feature.activates_when
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_season_state.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `app/services/season_state.py`**

Constants:

```python
class SeasonState:
    UNINITIALIZED = "uninitialized"
    FPL_UNAVAILABLE = "fpl_unavailable"
    PRE_LAUNCH = "pre_launch"
    PRESEASON = "preseason"
    GAMEWEEK_OPEN = "gameweek_open"
    PRE_DEADLINE = "pre_deadline"
    DEADLINE_PASSED = "deadline_passed"
    LIVE = "live"
    PROVISIONAL = "provisional"
    FINALIZED = "finalized"
    INTERNATIONAL_BREAK = "international_break"
    POSTSEASON = "postseason"
    HISTORICAL_SEASON = "historical_season"


class Readiness:
    READY = "ready"
    DEGRADED = "degraded"
    FALLBACK = "fallback"
    NOT_READY = "not_ready"
    STALE = "stale"
    ERROR = "error"
```

`season_state` logic, in order: no gameweeks → `UNINITIALIZED`; all finished → `POSTSEASON`; no finished gameweek and the next deadline is in the future → `PRESEASON`; next deadline within `PRE_DEADLINE_HOURS` (default 24) → `PRE_DEADLINE`; a current gameweek whose deadline has passed and which is not finished → `DEADLINE_PASSED`; finished but not data-checked → `PROVISIONAL`; finished and data-checked with a next deadline more than `BREAK_DAYS` (default 10) away → `INTERNATIONAL_BREAK`; otherwise → `GAMEWEEK_OPEN`. `latest_snapshot_at` being `None` returns `FPL_UNAVAILABLE`. Each branch sets a plain-language `explanation`.

`FeatureRequirement` dataclass: `required_inputs: tuple[str, ...]`, `optional_inputs: tuple[str, ...]`, `supported_states: tuple[str, ...]`, `minimum_sample: int`, `freshness_hours: int`, `fallback: str`, `activates_when: str`.

`FEATURES` declares at least `projections`, `expected_minutes`, `movers`, `recommendations` and `differentials`. `readiness_for(feature, inputs)` compares supplied input counts against `minimum_sample` and returns `NOT_READY` with the missing names and `activates_when` when unmet, `DEGRADED` when a fallback is active, `STALE` when the freshness window is exceeded, and `READY` otherwise.

- [ ] **Step 4: Run the tests, then the suite**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/season_state.py tests/test_season_state.py
git commit -m "Add centralised season-state and readiness service"
```

---

### Task 8: Movers threshold correction

**Files:**
- Modify: `app/services/queries.py:253-268`
- Modify: `app/web/routes.py:395-423`
- Modify: `app/templates/movers.html`
- Test: `tests/test_movers.py` (create)

**Interfaces:**
- Produces: `movers_data(db, season, period="7D", thresholds=None) -> dict` whose values are lists and which also returns `f"{label}_window"` metadata `{"start", "end", "observations", "threshold"}`.
- Default thresholds: `{"value": 0.01, "price": 0.05, "ownership": 0.10, "rank": 1}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_movers.py
from app.services.queries import classify_movement


def test_a_zero_delta_is_neither_a_riser_nor_a_faller():
    assert classify_movement(0.0, 0.01) == "unchanged"
    assert classify_movement(0.005, 0.01) == "unchanged"
    assert classify_movement(-0.005, 0.01) == "unchanged"


def test_movement_beyond_the_threshold_is_classified_by_sign():
    assert classify_movement(0.5, 0.01) == "riser"
    assert classify_movement(-0.5, 0.01) == "faller"


def test_a_missing_delta_is_not_movement():
    assert classify_movement(None, 0.01) == "no_history"
```

Plus a database-backed test asserting that for a population whose deltas are all zero, `movers_data` returns empty riser and faller lists and that no player identifier appears in both lists for any metric.

- [ ] **Step 2: Run to verify failure**

Expected: FAIL — `classify_movement` does not exist.

- [ ] **Step 3: Implement**

```python
MOVEMENT_THRESHOLDS = {"value": 0.01, "price": 0.05, "ownership": 0.10, "rank": 1}


def classify_movement(delta: float | None, threshold: float) -> str:
    if delta is None:
        return "no_history"
    if delta > threshold:
        return "riser"
    if delta < -threshold:
        return "faller"
    return "unchanged"
```

Rewrite `movers_data` so each metric partitions rows by `classify_movement`, sorts risers descending and fallers ascending, truncates to 20, and adds the window metadata. Rewrite the `movers_page` route to use `movers_data` for its `risers`/`fallers` instead of building its own unfiltered lists. Add an honest empty state to `movers.html`: `No player moved more than {{ threshold }} in this window.`

- [ ] **Step 4: Run the suite and commit**

```bash
git add app/services/queries.py app/web/routes.py app/templates/movers.html tests/test_movers.py
git commit -m "Exclude unchanged players from movers and report the window"
```

---

### Task 9: Recommender readiness guardrails and budget explanation

**Files:**
- Modify: `app/services/team_recommender.py:31-229`
- Modify: `app/templates/recommendation.html`
- Test: `tests/test_recommender.py`

**Interfaces:**
- Produces: `NotReadyError(ValueError)` with attributes `checks: list[dict]` and `activates_when: str`; `validate_pool(rows, strategy) -> list[dict]`; `recommend_team` raises `NotReadyError` when validation fails; the returned dict gains `budget_explanation`, `best_excluded`, `marginal_gain` and `tie_breakers`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_recommender.py
import pytest
from app.services.team_recommender import NotReadyError, recommend_team


def test_recommender_refuses_when_every_projection_is_zero(zero_projection_rows):
    with pytest.raises(NotReadyError) as excinfo:
        recommend_team(zero_projection_rows, 100.0, "best_team")
    failed = [check["name"] for check in excinfo.value.checks if not check["passed"]]
    assert "objective_variation" in failed
    assert excinfo.value.activates_when


def test_recommender_spends_the_budget_when_projections_vary(varied_projection_rows):
    result = recommend_team(varied_projection_rows, 100.0, "best_team")
    assert result["remaining"] <= 5.0
    assert result["budget_explanation"]
    assert result["best_excluded"] is not None
```

Build both fixtures from the existing row-shaping helper in `tests/test_recommender.py`: `zero_projection_rows` sets `projected_points_5=None`, `points_per_game=0`, `expected_minutes=None` on every snapshot; `varied_projection_rows` gives a spread of projections across at least 30 players covering all four positions with enough clubs to satisfy the three-per-club limit.

- [ ] **Step 2: Run to verify failure**

Expected: FAIL — `NotReadyError` does not exist; the zero case currently returns a cheap squad.

- [ ] **Step 3: Implement**

```python
class NotReadyError(ValueError):
    def __init__(self, checks: list[dict[str, Any]], activates_when: str):
        self.checks = checks
        self.activates_when = activates_when
        failed = ", ".join(check["detail"] for check in checks if not check["passed"])
        super().__init__(f"Recommendations are not available: {failed}")


MIN_PROJECTED_PLAYERS = 40
MIN_OBJECTIVE_STDEV = 0.05


def validate_pool(rows: list[dict[str, Any]], strategy: str) -> list[dict[str, Any]]:
    projections = [
        value for value in (_projected_output(row) for row in rows) if value is not None
    ]
    positive = [value for value in projections if value > 0]
    mean = sum(projections) / len(projections) if projections else 0.0
    variance = (
        sum((value - mean) ** 2 for value in projections) / len(projections)
        if projections else 0.0
    )
    stdev = variance ** 0.5
    by_position = Counter(row["player"].position_short for row in rows)
    return [
        {
            "name": "player_pool",
            "passed": len(rows) >= 15,
            "detail": f"{len(rows)} players available, 15 required",
        },
        {
            "name": "position_pools",
            "passed": all(by_position.get(p, 0) >= c for p, c in POSITION_COUNTS.items()),
            "detail": "Every position needs enough players to fill its squad slots",
        },
        {
            "name": "projections_available",
            "passed": len(positive) >= MIN_PROJECTED_PLAYERS,
            "detail": f"{len(positive)} players have a positive projection, {MIN_PROJECTED_PLAYERS} required",
        },
        {
            "name": "objective_variation",
            "passed": stdev > MIN_OBJECTIVE_STDEV,
            "detail": f"Projection spread is {stdev:.3f}; the objective cannot distinguish squads below {MIN_OBJECTIVE_STDEV}",
        },
    ]
```

Change `_projected_output` to return `float | None` and propagate `None` when both `projected_points_5` and `points_per_game` are unavailable. Everywhere it feeds a sum, treat `None` as excluded rather than zero.

At the top of `recommend_team`, after the empty-rows guard:

```python
    checks = validate_pool(rows, strategy)
    if not all(check["passed"] for check in checks):
        raise NotReadyError(
            checks,
            "Recommendations activate once the season has started and projections "
            "differ between players. Use the preseason template view until then.",
        )
```

Replace `team_objective` so the final tie-breaker prefers **spending** rather than saving, and add the remaining tie-breakers from the spec:

```python
    def team_objective(state):
        _, lineup, lineup_score = _best_lineup(state[2], strategy)
        starting_ids = {row["player"].id for row in lineup}
        bench_output = sum(
            value
            for value in (
                _projected_output(row)
                for row in state[2]
                if row["player"].id not in starting_ids
            )
            if value is not None
        )
        expected_minutes_total = sum(
            float(row["snapshot"].expected_minutes or 0) for row in lineup
        )
        return (
            lineup_score + (bench_output * 0.05),
            expected_minutes_total,
            state[1],
            state[0],  # ascending remaining budget == descending spend
        )
```

Add to the returned dict:

```python
        "checks": checks,
        "tie_breakers": [
            "Expected starting-XI points", "Expected minutes",
            "Strategy score", "Budget utilisation",
        ],
        "budget_explanation": _budget_explanation(budget, spent, best_excluded, marginal_gain),
        "best_excluded": best_excluded,
        "marginal_gain": marginal_gain,
```

where `best_excluded` is the highest-scoring candidate not selected that would fit the remaining funds, `marginal_gain` is its projected output minus that of the cheapest selected player in its position, and `_budget_explanation` returns a sentence such as `£1.5m remains because no affordable upgrade improved projected output; the closest was <name> at +0.3 pts.`

- [ ] **Step 4: Handle `NotReadyError` in the route**

In `recommendation_page`, catch `NotReadyError` separately from `ValueError` and pass `checks` and `activates_when` into the template. Render the failed checks and the activation condition in `recommendation.html` instead of a squad.

- [ ] **Step 5: Run the suite and commit**

```bash
git add app/services/team_recommender.py app/web/routes.py app/templates/recommendation.html tests/test_recommender.py
git commit -m "Refuse recommendations without usable projections and explain budget use"
```

---

### Task 10: Schema baseline and dashboard readiness surface

**Files:**
- Modify: `app/services/refresh.py:77-142`
- Modify: `app/services/queries.py` (`dashboard_data`)
- Modify: `app/templates/dashboard.html`
- Modify: `app/templates/schema.html`
- Test: `tests/test_refresh.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: `_update_schema(db, schema, captured_at) -> dict` returning `{"baseline": bool, "added": int, "removed": int}`; `SchemaChange.change_type` gains the value `Baseline`; `dashboard_data` gains `season_state`, `readiness`, `ranked_breakdown` and `exclusion_reasons`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_refresh.py
def test_first_observation_is_recorded_as_a_baseline_not_hundreds_of_additions(db):
    result = _update_schema(db, {"players": {"id", "web_name", "now_cost"}}, utcnow())
    db.commit()
    assert result["baseline"] is True
    assert result["added"] == 0
    types = {change.change_type for change in db.scalars(select(SchemaChange)).all()}
    assert types == {"Baseline"}


def test_a_later_new_field_is_recorded_as_an_addition(db):
    _update_schema(db, {"players": {"id", "web_name"}}, utcnow())
    db.commit()
    result = _update_schema(db, {"players": {"id", "web_name", "new_field"}}, utcnow())
    db.commit()
    assert result["baseline"] is False
    assert result["added"] == 1
```

- [ ] **Step 2: Run to verify failure**

Expected: FAIL — `_update_schema` returns an `int`.

- [ ] **Step 3: Implement**

At the top of `_update_schema`:

```python
    existing = {...}
    is_baseline = not existing
    added = removed = 0
```

Use `change_type="Baseline" if is_baseline else "Added"` for new fields, and only increment `added` when not a baseline. Return `{"baseline": is_baseline, "added": added, "removed": removed}`. Update the caller to use `result["added"] + result["removed"]` for `run.schema_change_count` and to store `run.details["schema_baseline"] = result["baseline"]`.

In `schema.html`, render `Baseline` rows in a distinct, non-alarming style and add a heading note: `The first observation of the FPL schema is recorded as a baseline, not as a change.`

- [ ] **Step 4: Surface season state, readiness and exclusion accounting on the dashboard**

In `dashboard_data`, compute the ranked breakdown from `metric_status` rather than a bare count:

```python
    exclusion_reasons: dict[str, int] = {}
    for row in rows:
        snapshot = row["snapshot"]
        if snapshot.value_rank is not None:
            continue
        status = (snapshot.metric_status or {}).get("value", {})
        reason = status.get("reason") or "No positive value score"
        exclusion_reasons[reason] = exclusion_reasons.get(reason, 0) + 1
```

Return `season_state(...)`, `readiness_for("projections", ...)`, `ranked_count`, `excluded_count` and `exclusion_reasons`. Render a readiness panel in `dashboard.html` showing season state, current gameweek, next deadline, countdown, last refresh, player count, ranked count, excluded count and the reason breakdown, plus the active fallback when readiness is not `ready`.

- [ ] **Step 5: Run the suite and commit**

```bash
git add app/services/refresh.py app/services/queries.py app/templates tests/
git commit -m "Record a schema baseline and explain ranking coverage on the dashboard"
```

---

### Task 11: Documentation and runtime evidence

**Files:**
- Modify: `README.md`
- Modify: `ANALYTICS.md`
- Modify: `DEPLOYMENT.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/FEATURE_GAP_REPORT.md`
- Create: `docs/SECURITY.md`
- Create: `docs/SEASON_STATE.md`
- Create: `docs/METRICS.md`

- [ ] **Step 1: Write `docs/SECURITY.md`**

Cover the three access modes and how to choose one, the four protection classes and their members, the Railway environment variables required (`ACCESS_MODE`, `APP_USERNAME`, `APP_PASSWORD`, `SESSION_SECRET`), cookie flags, CSRF, login throttling, the audit event table and how to query it, and a Tailscale section: run with `ACCESS_MODE=private`, bind to the tailnet interface, use `tailscale serve` to terminate TLS, and never expose the container port publicly.

- [ ] **Step 2: Write `docs/SEASON_STATE.md`**

Document every season state, the transition rules, the six readiness states, the feature requirement registry, and how a page should present each readiness state to the user.

- [ ] **Step 3: Write `docs/METRICS.md`**

Render the `CONTRACTS` registry as a table with formula, inputs, seasons, unit, range, minimum sample, null behaviour and version. Document the nine metric statuses and what each one means to a reader.

- [ ] **Step 4: Update the existing documents**

`README.md` gets the new environment variables and a security note at the top. `DEPLOYMENT.md` gets the Railway variable checklist and the demo-mode explanation. `ARCHITECTURE.md` gets the three new service modules and the four new tables. `docs/FEATURE_GAP_REPORT.md` gets a "Trust Foundation delivered" section listing D1–D7 as resolved with evidence.

- [ ] **Step 5: Capture runtime evidence**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
python -c "from alembic.config import Config; from alembic import command; c=Config('alembic.ini'); c.set_main_option('sqlalchemy.url','sqlite:///./data/evidence.db'); command.upgrade(c,'head')"
python -m uvicorn app.main:app --port 8099 &
curl -s -o /dev/null -w '%{http_code} ' http://127.0.0.1:8099/health
curl -s -o /dev/null -w '%{http_code} ' http://127.0.0.1:8099/
curl -s -o /dev/null -w '%{http_code} ' http://127.0.0.1:8099/my-team
docker compose build
```

Record the actual status codes. `/my-team` must be `303`, not `200`.

- [ ] **Step 6: Commit**

```bash
git add README.md ANALYTICS.md DEPLOYMENT.md ARCHITECTURE.md docs/
git commit -m "Document access modes, season states and metric contracts"
```

---

## Self-review notes

Spec coverage check against `2026-08-02-trust-foundation-design.md`:

- Section A (access control) → Tasks 1, 2. Audit log, throttling, cookie flags, privacy by exclusion all covered.
- Section B (season identity) → Tasks 3, 6. Migration, model, query scoping, import season, `CrossSeasonError`.
- Section C (missing vs zero) → Tasks 3, 4, 5. Nullable columns, contracts, `MetricValue`, statuses, refresh writing nulls. Template and export rendering of `MetricValue` is folded into Tasks 5 and 10 where the affected pages are touched.
- Section D (season state and readiness) → Tasks 5 (gameweeks persistence), 7 (service), 10 (dashboard surface).
- Section E (four repairs) → Tasks 8, 9, 10.
- Section F (validation) → every task carries its own tests; Task 11 captures runtime and Docker evidence.
