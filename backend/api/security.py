"""Opt-in API authentication and rate limiting helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

JsonHandler = Callable[[Request], Awaitable[Response]]


def issue_access_token(
    subject: str,
    roles: list[str] | None = None,
    *,
    secret: str | None = None,
    expires_in: int = 3600,
) -> str:
    """Issue a compact HS256 JWT for tests and trusted local tooling."""
    now = int(time.time())
    payload = {"sub": subject, "roles": roles or ["viewer"], "iat": now, "exp": now + expires_in}
    return _encode_jwt(payload, _jwt_secret(secret))


class AuthMiddleware(BaseHTTPMiddleware):
    """Validate bearer tokens and enforce simple role-based endpoint access."""

    def __init__(self, app: Any, *, secret: str | None = None) -> None:
        super().__init__(app)
        self.secret = _jwt_secret(secret)

    async def dispatch(self, request: Request, call_next: JsonHandler) -> Response:
        if request.method == "OPTIONS" or request.url.path == "/health":
            return await call_next(request)
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            return _json_error(401, "missing bearer token")
        try:
            claims = _decode_jwt(header.split(" ", 1)[1], self.secret)
        except ValueError:
            return _json_error(401, "invalid or expired token")
        roles = {str(role).lower() for role in claims.get("roles", [])}
        if not _is_allowed(request.method, request.url.path, roles):
            return _json_error(403, "insufficient role")
        request.state.user = claims
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory per-client, per-endpoint fixed-window request limiter."""

    def __init__(self, app: Any, *, requests: int = 60, window_seconds: int = 60) -> None:
        super().__init__(app)
        self.requests = max(1, requests)
        self.window_seconds = max(1, window_seconds)
        self._hits: dict[tuple[str, str, str], deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: JsonHandler) -> Response:
        key = (request.client.host if request.client else "local", request.method, request.url.path)
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] >= self.window_seconds:
            hits.popleft()
        if len(hits) >= self.requests:
            return _json_error(429, "rate limit exceeded")
        hits.append(now)
        return await call_next(request)


def _jwt_secret(secret: str | None = None) -> str:
    return secret or os.environ.get("PARIS_JWT_SECRET") or os.environ.get("PARIS_SECRET_KEY") or "paris-dev-jwt-secret"


def _encode_jwt(payload: dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = f"{_b64_json(header)}.{_b64_json(payload)}"
    signature = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(signature)}"


def _decode_jwt(token: str, secret: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("invalid token")
    signing_input = f"{parts[0]}.{parts[1]}"
    expected = _b64(hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, parts[2]):
        raise ValueError("invalid token signature")
    try:
        header = json.loads(_b64_decode(parts[0]))
        payload = json.loads(_b64_decode(parts[1]))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid token payload") from exc
    if header.get("alg") != "HS256":
        raise ValueError("unsupported token algorithm")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("token expired")
    return payload


def _is_allowed(method: str, path: str, roles: set[str]) -> bool:
    if "admin" in roles:
        return True
    if method == "GET" and roles & {"viewer", "operator"}:
        return True
    if "operator" in roles and method in {"POST", "PATCH"}:
        return path.startswith(("/campaigns", "/compose", "/warmup"))
    return False


def _json_error(status_code: int, detail: str) -> Response:
    return Response(json.dumps({"detail": detail}), status_code=status_code, media_type="application/json")


def _b64_json(value: dict[str, Any]) -> str:
    return _b64(json.dumps(value, separators=(",", ":")).encode("utf-8"))


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64_decode(value: str) -> str:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
