"""
Main FastAPI application for the dynamic multi-project API system
"""
import logging
import os
import sys
from contextlib import asynccontextmanager
from uuid import uuid4
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

# Add the dynamic_api directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.settings import get_settings
from core.router import router as dynamic_router
from core.middleware import setup_middleware
from core.response import error_response
from core.exceptions import DynamicAPIException
from core.database import init_database
from core.prism_cache import init_redis, close_redis
from api.v1.router import api_router
from controllers.auth.services.common import AuthError
from routes.tables import router as explorer_tables_router
from routes.query import router as explorer_query_router
from routes.export import router as explorer_export_router
from routes.ai_query import router as explorer_ai_query_router
from controllers.prism.router import router as prism_router


class ConsecutiveDuplicateFilter(logging.Filter):
    """Suppress consecutive duplicate log records from the same logger."""

    def __init__(self):
        super().__init__()
        self._last_signature = None

    def filter(self, record: logging.LogRecord) -> bool:
        signature = (record.name, record.levelno, record.getMessage())
        if signature == self._last_signature:
            return False
        self._last_signature = signature
        return True


# Configure logging
def setup_logging():
    """Configure application logging"""
    settings = get_settings()
    
    # Create logs directory if it doesn't exist
    log_dir = os.path.dirname(settings.LOG_FILE)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(settings.LOG_FILE, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Set specific logger levels
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("fastapi").setLevel(logging.INFO)
    logging.getLogger("watchfiles.main").addFilter(ConsecutiveDuplicateFilter())
    
    logger = logging.getLogger(__name__)
    logger.info("Logging configured")
    return logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    logger = logging.getLogger(__name__)
    
    # Startup
    logger.info("Starting Dynamic Multi-Project API")
    
    # Initialize database
    db_initialized = init_database()
    if db_initialized:
        logger.info("Database initialized successfully")
    else:
        logger.warning("Database not configured or failed to initialize")

    # Initialize Redis (PRISM cache — non-fatal if unavailable)
    await init_redis()

    logger.info("Application startup complete")
    
    yield
    
    # Shutdown
    await close_redis()
    logger.info("Shutting down Dynamic Multi-Project API")


# Initialize settings and logging
settings = get_settings()
logger = setup_logging()

# Create FastAPI application
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Dynamic multi-project API system with automatic controller routing",
    debug=settings.DEBUG,
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
)


# Exception handlers
@app.exception_handler(DynamicAPIException)
async def dynamic_api_exception_handler(request: Request, exc: DynamicAPIException):
    """Handle custom dynamic API exceptions"""
    payload = error_response(
        error=exc.__class__.__name__,
        message=exc.message
    ).model_dump(mode='json')
    logger.error(
        "ERROR_RESPONSE %s %s status=%s payload=%s",
        request.method,
        request.url.path,
        exc.status_code,
        payload,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=payload
    )


@app.exception_handler(AuthError)
async def auth_v2_exception_handler(request: Request, exc: AuthError):
    """Handle auth v2 exceptions with required envelope + request_id/details."""
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    payload = error_response(
        error=exc.code,
        message=exc.message,
        data={"request_id": request_id, "details": exc.details or {}},
    ).model_dump(mode="json")
    logger.error(
        "ERROR_RESPONSE %s %s status=%s payload=%s",
        request.method,
        request.url.path,
        exc.status_code,
        payload,
    )
    response = JSONResponse(
        status_code=exc.status_code,
        content=payload,
    )
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle HTTP exceptions"""
    payload = error_response(
        error="HTTPException",
        message=str(exc.detail)
    ).model_dump(mode='json')
    logger.error(
        "ERROR_RESPONSE %s %s status=%s payload=%s",
        request.method,
        request.url.path,
        exc.status_code,
        payload,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=payload
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle request validation errors"""
    payload = error_response(
        error="ValidationError",
        message="Request validation failed",
        data=exc.errors()
    ).model_dump(mode='json')
    logger.error(
        "ERROR_RESPONSE %s %s status=%s payload=%s",
        request.method,
        request.url.path,
        422,
        payload,
    )
    return JSONResponse(
        status_code=422,
        content=payload
    )


@app.exception_handler(500)
async def internal_server_error_handler(request: Request, exc: Exception):
    """Handle internal server errors"""
    payload = error_response(
        error="InternalServerError",
        message="An unexpected error occurred"
    ).model_dump(mode='json')
    logger.error(
        "ERROR_RESPONSE %s %s status=%s payload=%s error=%s",
        request.method,
        request.url.path,
        500,
        payload,
        str(exc),
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content=payload
    )


# Set up middleware
setup_middleware(app)

# Include explicit APIRouter endpoints first.
# Legacy dynamic routes remain enabled as fallback for old clients.
app.include_router(api_router)
app.include_router(explorer_tables_router)
app.include_router(explorer_query_router)
app.include_router(explorer_export_router)
app.include_router(explorer_ai_query_router)
app.include_router(prism_router)


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint - redirects to health check"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/py/health")


# Include legacy dynamic router after explicit routers.
app.include_router(dynamic_router)


# Development server runner
if __name__ == "__main__":
    import uvicorn
    
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Debug mode: {settings.DEBUG}")
    logger.info(f"Server will start at http://{settings.HOST}:{settings.PORT}")
    
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.RELOAD,
        reload_excludes=["logs/*", "*.log", "__pycache__/*", ".git/*"],
        log_level=settings.LOG_LEVEL.lower(),
        access_log=True
    )
