from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.web.auth import AuthenticationMiddleware
from app.web.routes import router


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
)

app.add_middleware(AuthenticationMiddleware, settings=settings)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=settings.production_cookie_secure,
    max_age=settings.session_max_age_seconds,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(router)
