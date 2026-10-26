"""Role checks for the opt-in local API authentication mode."""

import sqlite3

from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from .auth import AuthRepository, auth_enabled

ROLE_LEVEL = {"viewer": 0, "underwriter": 1, "admin": 2}
PUBLIC_AUTH_PATHS = {
    "/api/v1/auth/config",
    "/api/v1/auth/login",
}


class RoleAccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        path = request.url.path
        if (
            not auth_enabled()
            or not path.startswith(("/api/v1/", "/api/v2/"))
            or path in PUBLIC_AUTH_PATHS
            or request.method == "OPTIONS"
        ):
            return await call_next(request)

        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not token.strip():
            return JSONResponse(
                {"detail": "Authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            user = await run_in_threadpool(
                AuthRepository().resolve_session,
                token.strip(),
            )
        except (OSError, RuntimeError, sqlite3.Error):
            return JSONResponse(
                {"detail": "Authentication service unavailable"},
                status_code=503,
            )
        if user is None:
            return JSONResponse(
                {"detail": "Session is invalid or expired"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        required_role = self._required_role(request.method, path)
        if ROLE_LEVEL[user.role] < ROLE_LEVEL[required_role]:
            return JSONResponse(
                {"detail": "Insufficient role"},
                status_code=403,
            )
        request.state.auth_user = user
        return await call_next(request)

    @staticmethod
    def _required_role(method: str, path: str) -> str:
        if path.startswith('/api/v2/cases/'):
            return 'viewer' if method == 'GET' else 'underwriter'
        if path == "/api/v1/auth/logout":
            return "viewer"
        if path.startswith("/api/v1/auth/users"):
            return "admin"
        if path == "/api/v1/strategies/preview":
            return "underwriter"
        if path.startswith("/api/v1/strategies") and method != "GET":
            return "admin"
        if method == "GET":
            return "viewer"
        if method == "POST" and path == "/api/v1/components/agent/runs":
            return "viewer"
        if method == "POST" and path == "/api/v1/weather/history":
            return "viewer"
        if method == "POST" and path in {
            "/api/v1/reports/render",
            "/api/v1/reports/view",
            "/api/v1/reports/interpret",
        }:
            return "viewer"
        if method == "POST" and path == "/api/v1/components/corrections":
            return "underwriter"
        if path.startswith("/api/v1/components/verified"):
            return "underwriter"
        if "/review" in path:
            return "underwriter"
        if path.startswith(
            (
                "/api/v1/underwriting/",
                "/api/v1/rule-selection/",
                "/api/v1/ocr/",
                "/api/v1/vision/",
                "/api/v1/risk/",
                "/api/v1/files/",
                "/api/v1/analyze/",
                "/api/v1/reports/",
            )
        ):
            return "underwriter"
        return "admin"
