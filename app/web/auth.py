from __future__ import annotations

import secrets

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from app.config import Settings


PUBLIC_PREFIXES = ("/health", "/login", "/static")


class AuthenticationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings):
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):
        request.session.setdefault("csrf_token", secrets.token_urlsafe(32))
        if not self.settings.auth_enabled:
            return await call_next(request)

        if any(
            request.url.path.startswith(prefix)
            for prefix in PUBLIC_PREFIXES
        ):
            return await call_next(request)

        if request.session.get("authenticated"):
            return await call_next(request)

        return RedirectResponse(
            url=f"/login?next={request.url.path}",
            status_code=303,
        )


def valid_credentials(
    settings: Settings,
    username: str,
    password: str,
) -> bool:
    if not settings.auth_enabled:
        return True
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
