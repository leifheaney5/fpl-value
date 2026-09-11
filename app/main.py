from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.web.auth import AuthenticationMiddleware
from app.web.routes import router


logger = logging.getLogger(__name__)


def configure_application_logging() -> None:
    application_logger = logging.getLogger("app")
    application_logger.setLevel(logging.INFO)
    if not logging.getLogger().handlers and not application_logger.handlers:
        application_logger.addHandler(logging.StreamHandler())


configure_application_logging()


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


@app.middleware("http")
async def log_request_complete(request: Request, call_next):
    started_at = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        logger.info(
            "request_complete method=%s path=%s status=%s duration_ms=%.1f",
            request.method,
            request.url.path,
            status_code,
            (time.perf_counter() - started_at) * 1000,
        )
