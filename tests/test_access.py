import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.base import Base
from app.db.models import AuditEvent
from app.db.session import get_db
from app.main import app
from app.web.auth import protection_for


def _client(tmp_path, name="access.db"):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    return TestClient(app, follow_redirects=False), Session


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
    assert settings.require_auth_for("PERSONAL") is True
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
    assert settings.require_auth_for("ANALYTICS") is True


def test_unknown_access_mode_is_rejected():
    with pytest.raises(ValueError):
        Settings(access_mode="everyone")


def test_protection_classes_cover_personal_and_mutation_routes():
    assert protection_for("/health") == "PUBLIC"
    assert protection_for("/static/app.css") == "PUBLIC"
    assert protection_for("/login") == "PUBLIC"
    assert protection_for("/my-team") == "PERSONAL"
    assert protection_for("/admin/refresh") == "MUTATION"
    assert protection_for("/spreadsheet") == "ANALYTICS"
    assert protection_for("/") == "ANALYTICS"


def test_anonymous_visitor_cannot_reach_personal_routes_or_trigger_refresh(tmp_path):
    client, _ = _client(tmp_path)
    try:
        assert client.get("/my-team").status_code == 303
        assert client.post(
            "/admin/refresh", data={"csrf_token": "x"}
        ).status_code in (303, 403)
        assert client.get("/health").status_code == 200
        assert client.get("/spreadsheet").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_dashboard_does_not_leak_personal_data_to_anonymous_visitors(tmp_path):
    client, _ = _client(tmp_path, "leak.db")
    try:
        body = client.get("/").text
        assert "Log out" not in body
        assert "Sign in" in body
        assert "entry_id" not in body.casefold()
    finally:
        app.dependency_overrides.clear()


def test_failed_logins_are_throttled_and_audited(tmp_path):
    from app.web.auth import login_throttle

    login_throttle.clear("testclient")
    client, Session = _client(tmp_path, "throttle.db")
    try:
        token = client.get("/login").cookies  # establishes a session cookie
        assert token is not None
        for _ in range(5):
            client.post(
                "/login",
                data={"username": "a", "password": "b", "csrf_token": "bad"},
            )
        throttled = client.post(
            "/login", data={"username": "a", "password": "b", "csrf_token": "bad"}
        )
        assert throttled.status_code == 429

        with Session() as db:
            actions = {event.action for event in db.scalars(select(AuditEvent)).all()}
        assert "login.failure" in actions
        assert "login.throttled" in actions
    finally:
        login_throttle.clear("testclient")
        app.dependency_overrides.clear()


def test_audit_detail_never_stores_a_password(tmp_path):
    from app.web.audit import _clean

    cleaned = _clean({"username": "admin", "password": "hunter2", "csrf_token": "abc"})
    assert cleaned["username"] == "admin"
    assert cleaned["password"] == "[redacted]"
    assert cleaned["csrf_token"] == "[redacted]"
