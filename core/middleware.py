"""
Middleware components for the dynamic API system
"""
import time
import logging
from typing import Callable
from fastapi import Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from .settings import get_settings

logger = logging.getLogger(__name__)

class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Simple API Key authentication middleware"""
    
    # Routes that don't require authentication
    PUBLIC_ROUTES = [
        "/docs",
        "/redoc",
        "/openapi.json",
    ]
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        settings = get_settings()
        
        # Skip if API key auth is disabled
        if not settings.API_KEY_ENABLED:
            return await call_next(request)
        
        # Skip for public routes
        if request.url.path in self.PUBLIC_ROUTES:
            return await call_next(request)
        
        # Get API key from header
        api_key = request.headers.get("X-API-Key") or request.headers.get("Authorization")
        
        # Remove 'Bearer ' prefix if present
        if api_key and api_key.startswith("Bearer "):
            api_key = api_key[7:]
        
        # Validate API key
        if not api_key:
            logger.warning(f"Missing API key for {request.method} {request.url.path} from {request.client.host if request.client else 'unknown'}")
            return Response(
                content='{"success": false, "data": null, "message": "API key required. Please provide X-API-Key header.", "error": "Unauthorized", "timestamp": null}',
                status_code=401,
                headers={"Content-Type": "application/json"}
            )
        
        if api_key not in settings.API_KEYS:
            logger.warning(f"Invalid API key attempted for {request.method} {request.url.path} from {request.client.host if request.client else 'unknown'}")
            return Response(
                content='{"success": false, "data": null, "message": "Invalid API key provided.", "error": "Forbidden", "timestamp": null}',
                status_code=403,
                headers={"Content-Type": "application/json"}
            )
        
        # API key is valid, proceed
        logger.info(f"Authenticated request with API key: {api_key[:20]}...")
        return await call_next(request)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for detailed request/response logging"""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.time()
        
        # Log request details
        logger.info(
            f"REQUEST {request.method} {request.url.path} "
            f"- Client: {request.client.host if request.client else 'unknown'} "
            f"- User-Agent: {request.headers.get('user-agent', 'unknown')[:50]}..."
        )
        
        try:
            # Process request
            response = await call_next(request)
            
            # Calculate processing time
            process_time = time.time() - start_time
            
            # Log response
            logger.info(
                f"SUCCESS {request.method} {request.url.path} "
                f"- {response.status_code} "
                f"- {process_time:.3f}s"
            )
            
            # Add timing header
            response.headers["X-Process-Time"] = str(process_time)
            
            return response
            
        except Exception as e:
            process_time = time.time() - start_time
            logger.error(
                f"ERROR {request.method} {request.url.path} "
                f"- Error: {str(e)} "
                f"- {process_time:.3f}s"
            )
            raise


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
