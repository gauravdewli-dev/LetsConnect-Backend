from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.constants import AUTH_RATE_LIMIT_REQUESTS, AUTH_RATE_LIMIT_WINDOW_SECONDS
from app.middleware.rate_limit import auth_rate_limiter


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    """Throttle abusive traffic on /auth endpoints."""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path.startswith("/auth") and request.method in {"POST", "PATCH", "DELETE"}:
            key = f"{_client_ip(request)}:{path}"
            if not auth_rate_limiter.is_allowed(
                key,
                max_requests=AUTH_RATE_LIMIT_REQUESTS,
                window_seconds=AUTH_RATE_LIMIT_WINDOW_SECONDS,
            ):
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Please try again later."},
                )
        return await call_next(request)
