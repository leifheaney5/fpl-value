from __future__ import annotations

import secrets
import time
from collections import defaultdict

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from app.config import Settings


# Longest-prefix wins, so order matters only in that more specific prefixes
# must appear before the general ones. Anything unlisted is ANALYTICS, which
# means a new route is public in demo mode by default -- acceptable because
# analytics data is impersonal, while anything personal must be added here.
PROTECTION_MAP: tuple[tuple[str, str], ...] = (
    ("/health", "PUBLIC"),
    ("/login", "PUBLIC"),
    ("/logout", "PUBLIC"),
    ("/static", "PUBLIC"),
    ("/my-team", "PERSONAL"),
    ("/admin", "MUTATION"),
)


def protection_for(path: str) -> str:
    for prefix, protection in PROTECTION_MAP:
        if path == prefix or path.startswith(prefix + "/"):
            return protection
    return "ANALYTICS"


def is_authenticated(request: Request) -> bool:
    return bool(request.session.get("authenticated"))


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class _LoginThrottle:
    """In-process failed-login counter.

    Per-worker rather than shared, which is sufficient for the single-container
    deployment this application targets. A process restart clears the counters,
    so this slows credential guessing rather than defeating it; it is a
    supplement to a strong password, not a replacement for one.
    """

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
        request.state.access_mode = self.settings.access_mode

        if not self.settings.require_auth_for(protection):
            return await call_next(request)
        if request.state.authenticated:
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
    """Verify credentials in constant time.

    Returns False when no credentials are configured. Previously this returned
    True in that case, which meant an unconfigured deployment authenticated
    everyone.
    """
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
