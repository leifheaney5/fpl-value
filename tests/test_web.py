from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from io import BytesIO
from openpyxl import load_workbook

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.config import Settings
from app.web.auth import safe_next_path, valid_credentials


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
        for path in ["/", "/players", "/spreadsheet", "/my-team", "/recommendation", "/transfers", "/movers", "/compare", "/diagnostics", "/schema", "/settings"]:
            assert client.get(path).status_code == 200
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
    assert settings.auth_enabled
    assert valid_credentials(settings, "admin", "secret")
    assert not valid_credentials(settings, "admin", "wrong")
    assert safe_next_path("/players") == "/players"
    assert safe_next_path("https://example.invalid") == "/"
    assert safe_next_path("//example.invalid") == "/"
