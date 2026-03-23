"""
Middleware components for the dynamic API system
"""
import time
import json
import logging
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .settings import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pure ASGI middlewares (no BaseHTTPMiddleware overhead — ~20-80 ms savings
# per request per layer compared to BaseHTTPMiddleware)
# ---------------------------------------------------------------------------

_API_KEY_PUBLIC_ROUTES = {"/docs", "/redoc", "/openapi.json"}

_UNAUTHORIZED_BODY = json.dumps({
    "success": False, "data": None,
    "message": "API key required. Please provide X-API-Key header.",
    "error": "Unauthorized", "timestamp": None,
}).encode()

_FORBIDDEN_BODY = json.dumps({
    "success": False, "data": None,
    "message": "Invalid API key provided.",
    "error": "Forbidden", "timestamp": None,
}).encode()


class APIKeyAuthMiddleware:
    """API Key authentication — pure ASGI, zero BaseHTTPMiddleware overhead."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        if not settings.API_KEY_ENABLED:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in _API_KEY_PUBLIC_ROUTES:
            await self.app(scope, receive, send)
            return

        # Extract key from headers
        headers = dict(scope.get("headers", []))
        api_key = (
            headers.get(b"x-api-key", b"").decode()
            or headers.get(b"authorization", b"").decode()
        )
        if api_key.startswith("Bearer "):
            api_key = api_key[7:]

        client = scope.get("client")
        client_host = client[0] if client else "unknown"

        if not api_key:
            logger.warning("Missing API key for %s from %s", path, client_host)
            response = Response(
                content=_UNAUTHORIZED_BODY,
                status_code=401,
                media_type="application/json",
            )
            await response(scope, receive, send)
            return

        if api_key not in settings.API_KEYS:
            logger.warning("Invalid API key for %s from %s", path, client_host)
            response = Response(
                content=_FORBIDDEN_BODY,
                status_code=403,
                media_type="application/json",
            )
            await response(scope, receive, send)
            return

        logger.debug("Authenticated request: %s", path)
        await self.app(scope, receive, send)


class RequestLoggingMiddleware:
    """Request/response timing logger — pure ASGI, logs slow requests at INFO."""
    SLOW_THRESHOLD_S = 1.0  # log at INFO only when response takes > 1 s

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        path = scope.get("path", "")
        method = scope.get("method", "")
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                elapsed = time.perf_counter() - start
                message = dict(message)
                message.setdefault("headers", [])
                headers = list(message["headers"])
                headers.append((b"x-process-time", f"{elapsed:.4f}".encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            log = logger.info if elapsed >= self.SLOW_THRESHOLD_S else logger.debug
            log("%s %s — %d — %.3fs", method, path, status_code, elapsed)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple rate limiting middleware"""
    
    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.client_requests = {}  # In production, use Redis or similar
        
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        settings = get_settings()
        
        # Skip if rate limiting is disabled
        if not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)
        
        client_ip = request.client.host if request.client else "unknown"
        current_time = time.time()
        
        # Clean old entries (older than 1 minute)
        cutoff_time = current_time - 60
        self.client_requests = {
            ip: timestamps for ip, timestamps in self.client_requests.items()
            if any(t > cutoff_time for t in timestamps)
        }
        
        # Get client's request timestamps
        if client_ip not in self.client_requests:
            self.client_requests[client_ip] = []
        
        # Filter to last minute
        self.client_requests[client_ip] = [
            t for t in self.client_requests[client_ip] if t > cutoff_time
        ]
        
        # Check rate limit
        if len(self.client_requests[client_ip]) >= settings.RATE_LIMIT_REQUESTS:
            logger.warning(f"Rate limit exceeded for {client_ip}")
            return Response(
                content='{"error": "Rate limit exceeded"}',
                status_code=429,
                headers={"Content-Type": "application/json"}
            )
        
        # Add current request timestamp
        self.client_requests[client_ip].append(current_time)
        
        return await call_next(request)


def setup_middleware(app):
    """Set up all middleware for the FastAPI app"""
    settings = get_settings()
    
    # CORS Middleware
    if settings.CORS_MANAGED_BY_PROXY:
        logger.info("CORS is managed by reverse proxy; skipping FastAPI CORSMiddleware")
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ORIGINS,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # API Key Authentication Middleware
    if settings.API_KEY_ENABLED:
        app.add_middleware(APIKeyAuthMiddleware)
        logger.info(f"API Key authentication enabled with {len(settings.API_KEYS)} keys")
    
    # Trusted Host Middleware
    if settings.ALLOWED_HOSTS != ["*"]:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=settings.ALLOWED_HOSTS
        )
    
    # Rate Limiting Middleware
    if settings.RATE_LIMIT_ENABLED:
        app.add_middleware(
            RateLimitMiddleware,
            requests_per_minute=settings.RATE_LIMIT_REQUESTS
        )
    
    # Request Logging Middleware
    app.add_middleware(RequestLoggingMiddleware)
    
    logger.info("All middleware configured")
