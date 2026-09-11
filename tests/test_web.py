from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from io import BytesIO
from openpyxl import load_workbook
from types import SimpleNamespace

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.config import Settings
from app.web.auth import safe_next_path, valid_credentials
from app.web import routes


def test_web_routes_health_exports_and_new_pages(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'web.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        assert client.get("/health").status_code == 200
        for path in ["/", "/players", "/spreadsheet", "/recommendation", "/transfers", "/movers", "/compare", "/diagnostics", "/schema", "/settings", "/differentials", "/transfer-market", "/templates", "/fixtures", "/performance"]:
            assert client.get(path).status_code == 200
        assert "No upcoming fixtures are available yet." in client.get("/fixtures").text
        assert "No scored fixtures are available yet." in client.get("/performance").text
        # Personal routes stay closed to anonymous visitors even in demo mode.
        assert client.get("/my-team", follow_redirects=False).status_code == 303
        assert client.get("/players?position=MID").url.path == "/spreadsheet"
        assert client.get("/players?max_price=&min_minutes=&max_ownership=not-a-number").status_code == 200
        login_page = client.get("/login")
        assert login_page.status_code == 200
        assert client.post("/login", data={"username": "x", "password": "y", "csrf_token": "bad"}).status_code == 401
        csv_response = client.get("/exports/current.csv")
        assert csv_response.status_code == 200
        assert b"Player" in csv_response.content
        xlsx_response = client.get("/exports/current.xlsx")
        assert xlsx_response.headers["content-type"].startswith("application/vnd.openxmlformats")
        workbook = load_workbook(BytesIO(xlsx_response.content), read_only=True)
        assert {"Dashboard", "Value Rankings", "Forward Value", "Rotation Risk", "Movers", "Fixtures", "Schema Changes", "Guide"}.issubset(workbook.sheetnames)
    finally:
        app.dependency_overrides.clear()


def test_credentials_use_constant_time_path_and_safe_redirect():
    settings = Settings(app_username="admin", app_password="secret")
    assert settings.credentials_configured
    assert valid_credentials(settings, "admin", "secret")
    assert not valid_credentials(settings, "admin", "wrong")
    # Fail closed: with no credentials configured nothing authenticates.
    assert not valid_credentials(Settings(), "admin", "secret")
    assert safe_next_path("/players") == "/players"
    assert safe_next_path("https://example.invalid") == "/"
    assert safe_next_path("//example.invalid") == "/"


def test_dashboard_passes_its_loaded_rows_to_my_team(monkeypatch):
    rows = [{"player": object(), "snapshot": object()}]
    captured_rows = []
    monkeypatch.setattr(routes, "dashboard_data", lambda db, season: {"rows": rows})
    monkeypatch.setattr(routes, "linked_team_data", lambda db, client, settings, *, rows=None: captured_rows.append(rows))
    monkeypatch.setattr(routes.templates, "TemplateResponse", lambda **kwargs: kwargs["context"])
    settings = Settings(access_mode="local", fpl_entry_id=123)

    context = routes.dashboard(SimpleNamespace(session={}), db=object(), settings=settings)

    assert captured_rows == [rows]
    assert context["my_team"] is None


def test_recommendation_passes_its_loaded_rows_to_my_team(monkeypatch):
    rows = [{"player": object(), "snapshot": object()}]
    captured_rows = []
    monkeypatch.setattr(routes, "latest_rows", lambda db, season: rows)
    monkeypatch.setattr(routes, "recommend_team_cached", lambda candidate_rows, budget, strategy: None)
    monkeypatch.setattr(routes, "linked_team_data", lambda db, client, settings, *, rows=None: captured_rows.append(rows))
    monkeypatch.setattr(routes.templates, "TemplateResponse", lambda **kwargs: kwargs["context"])
    settings = Settings(access_mode="local", fpl_entry_id=123)

    context = routes.recommendation_page(SimpleNamespace(session={}), db=object(), settings=settings)

    assert captured_rows == [rows]
    assert context["my_team"] is None
