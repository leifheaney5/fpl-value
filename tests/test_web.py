from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

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
        for path in ["/", "/players", "/transfers", "/diagnostics", "/movers", "/schema"]:
            assert client.get(path).status_code == 200
        login_page = client.get("/login")
        assert login_page.status_code == 200
        assert client.post("/login", data={"username": "x", "password": "y", "csrf_token": "bad"}).status_code == 401
        assert client.get("/exports/current.csv").status_code == 200
        assert client.get("/exports/current.xlsx").headers["content-type"].startswith("application/vnd.openxmlformats")
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
