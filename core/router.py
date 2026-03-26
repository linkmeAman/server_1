"""
Dynamic router for the multi-project API system
Handles URL patterns like /py/{controller}/{function}/{id?} and dispatches to appropriate functions
"""
import fnmatch
import time
import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from .loader import call_function, get_controller_functions
from .response import success_response, error_response, APIResponse
from .exceptions import (
    DynamicAPIException,
)

# Set up logging
logger = logging.getLogger(__name__)

# Create the dynamic router
# No prefix - routes accessible at /controller/function
dynamic_router = APIRouter(prefix="", tags=["Dynamic API"])

# ---------------------------------------------------------------------------
# Controllers that are exempt from the PRISM auth layer.
# Keep this list minimal — only truly public endpoints belong here.
# ---------------------------------------------------------------------------
_PRISM_EXEMPT_CONTROLLERS = frozenset({
    "health",
})


def _prism_action_allowed(
    action: str,
    static_allows: list[str],
    static_denies: list[str],
) -> bool:
    """Check a single action against the static PRISM cache snapshot.

    The dynamic router uses an **explicit-deny** model (not the standard
    PRISM default-deny), because legacy policies use verb-based action
    patterns (e.g. ``*:read``) that don't match controller:function-style
    action names.  This means:
      - A token with a valid session is assumed allowed unless a DENY pattern
        in static_denies explicitly matches the action.
      - Supreme users are never checked here (handled before this call).
    """
    for pat in static_denies:
        if fnmatch.fnmatch(action, pat):
            return False
    return True  # default-allow: no explicit deny found


async def log_request_middleware(request: Request, call_next):
    """Middleware to log all requests with timing"""
    start_time = time.time()
    
    # Log incoming request
    logger.info(f"⏫ {request.method} {request.url.path} - Client: {request.client.host}")
    
    try:
        response = await call_next(request)
        
        # Calculate processing time
        process_time = time.time() - start_time
        
        # Log response
        logger.info(f"✅ {request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s")
        
        return response
        
    except Exception as e:
        process_time = time.time() - start_time
        logger.error(f"❌ {request.method} {request.url.path} - Error: {str(e)} - {process_time:.3f}s")
        raise


async def get_request_data(request: Request) -> tuple[Optional[Dict], Optional[Dict]]:
    """Extract query parameters and body data from request"""
    
    # Get query parameters
    query_params = dict(request.query_params) if request.query_params else None
    
    # Get body data for POST/PUT/PATCH requests
    body_data = None
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            content_type = request.headers.get("content-type", "")
            
            if "application/json" in content_type:
                body_data = await request.json()
            elif "application/x-www-form-urlencoded" in content_type:
                form_data = await request.form()
                body_data = dict(form_data)
            else:
                # Try to parse as JSON anyway
                try:
                    body_data = await request.json()
                except:
                    # If all fails, get raw body
                    raw_body = await request.body()
                    if raw_body:
                        logger.warning(f"Unhandled content type: {content_type}")
                        
        except Exception as e:
            logger.warning(f"Could not parse request body: {e}")
    
    return query_params, body_data


async def _dispatch_request(
    controller: str,
    function: str,
    item_id: Optional[str],
    request: Request
) -> JSONResponse:
    """
    Internal function to dispatch requests to controller functions
    """
    try:
        # ------------------------------------------------------------------
        # PRISM enforcement — authenticate the caller and check the cache
        # ------------------------------------------------------------------
        if controller not in _PRISM_EXEMPT_CONTROLLERS:
            from core.prism_guard import resolve_caller_from_request
            from core.prism_cache import get_prism_cache

            try:
                caller = await resolve_caller_from_request(request)
            except HTTPException:
                raise  # 401/403 from token validation — propagate as-is

            if caller is None:
                # No Authorization header → anonymous request, deny by default
                return JSONResponse(
                    content=error_response(
                        error="Unauthorized",
                        message="Authentication required",
                    ).model_dump(mode="json"),
                    status_code=401,
                )

            if not caller.is_super:
                # Supreme users bypass PRISM (all access granted)
                prism_action = f"{controller}:{function}"
                cache = await get_prism_cache(caller.user_id)

                if cache is None:
                    # Cache miss — conservative deny; user should re-login
                    # to rebuild their snapshot
                    logger.warning(
                        "PRISM cache miss for user_id=%s on %s — denying",
                        caller.user_id, prism_action,
                    )
                    return JSONResponse(
                        content=error_response(
                            error="Forbidden",
                            message=(
                                "Permission snapshot unavailable. "
                                "Please log out and log back in."
                            ),
                        ).model_dump(mode="json"),
                        status_code=403,
                    )

                static_allows: list[str] = cache.get("static_allows", [])
                static_denies: list[str] = cache.get("static_denies", [])

                if not _prism_action_allowed(prism_action, static_allows, static_denies):
                    logger.info(
                        "PRISM denied user_id=%s action=%s",
                        caller.user_id, prism_action,
                    )
                    return JSONResponse(
                        content=error_response(
                            error="Forbidden",
                            message=f"Access denied: {prism_action}",
                        ).model_dump(mode="json"),
                        status_code=403,
                    )

        # Extract request data
        query_params, body_data = await get_request_data(request)

        # Log the dispatch
        params_info = []
        if item_id:
            params_info.append(f"id={item_id}")
        if query_params:
            params_info.append(f"query={len(query_params)} params")
        if body_data:
            params_info.append(f"body={len(body_data) if isinstance(body_data, dict) else 'data'}")
        
        param_str = ", ".join(params_info) if params_info else "no params"
        logger.info(f"Dispatching {controller}.{function}({param_str})")
        
        # Call the function through our dynamic loader
        result = await call_function(
            controller_name=controller,
            function_name=function,
            item_id=item_id,
            query_params=query_params,
            body_params=body_data
        )

        # If the controller returns a FastAPI Response object directly (HTML, File, etc.), return it
        from fastapi import Response
        if isinstance(result, Response):
            return result
        
        # Wrap result in standard response format if it's not already
        if isinstance(result, APIResponse):
            # Convert Pydantic model to dict with JSON serialization
            response_data = result.model_dump(mode='json')
        elif isinstance(result, dict) and "success" in result:
            # Already in our format
            response_data = result
        else:
            # Wrap in success response
            response_data = success_response(data=result).model_dump(mode='json')
        
        return JSONResponse(content=response_data)
        
    except DynamicAPIException as e:
        # Handle our custom exceptions
        logger.error(f"Dynamic API error: {e.message}")
        error_data = error_response(
            error=e.__class__.__name__,
            message=e.message
        ).model_dump(mode='json')
        return JSONResponse(content=error_data, status_code=e.status_code)
        
    except HTTPException as e:
        # Handle FastAPI HTTP exceptions
        logger.error(f"HTTP error: {e.detail}")
        error_data = error_response(
            error="HTTPException",
            message=e.detail
        ).model_dump(mode='json')
        return JSONResponse(content=error_data, status_code=e.status_code)
        
    except Exception as e:
        # Handle unexpected errors
        logger.error(f"Unexpected error in dispatch: {str(e)}", exc_info=True)
        error_data = error_response(
            error="InternalServerError",
            message="An unexpected error occurred"
        ).model_dump(mode='json')
        return JSONResponse(content=error_data, status_code=500)


# Additional utility endpoints
@dynamic_router.get("/health")
async def health_check():
    """Health check endpoint"""
    return success_response(
        data={"status": "healthy", "timestamp": time.time()},
        message="Dynamic API is running",
        

    )


@dynamic_router.get("/controllers")
async def list_controllers():
    """List all available controllers (for development/debugging)"""
    try:
        import os
        import glob
        
        # Get all controller files
        controllers_path = os.path.join(os.path.dirname(__file__), "..", "controllers")
        controller_files = glob.glob(os.path.join(controllers_path, "*.py"))
        
        controllers = []
        for file_path in controller_files:
            filename = os.path.basename(file_path)
            if filename != "__init__.py" and filename.endswith(".py"):
                controller_name = filename[:-3]  # Remove .py extension
                controllers.append(controller_name)
        
        return success_response(
            data={"controllers": controllers},
            message=f"Found {len(controllers)} controllers"
        )
        
    except Exception as e:
        logger.error(f"Error listing controllers: {e}")
        return error_response(
            error="ControllerListError",
            message="Could not list controllers"
        )


@dynamic_router.get("/controllers/{controller_name}/functions")
async def list_controller_functions(controller_name: str):
    """List all functions in a specific controller (for development/debugging)"""
    try:
        functions = get_controller_functions(controller_name)
        
        return success_response(
            data={
                "controller": controller_name,
                "functions": functions
            },
            message=f"Found {len(functions)} functions in {controller_name}"
        )
        
    except Exception as e:
        logger.error(f"Error listing functions for {controller_name}: {e}")
        return error_response(
            error="FunctionListError", 
            message=f"Could not list functions for controller {controller_name}"
        )


@dynamic_router.api_route("/{controller}/{function}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def dispatch_without_id(
    controller: str,
    function: str,
    request: Request
):
    """
    Handle requests without ID parameter
    Examples: 
    - GET /py/geosearch/cities
    - POST /py/geosearch/search
    """
    return await _dispatch_request(controller, function, None, request)


@dynamic_router.api_route("/{controller}/{function}/{item_id}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def dispatch_with_id(
    controller: str,
    function: str, 
    item_id: str,
    request: Request
):
    """
    Handle requests with ID parameter
    Examples:
    - GET /py/geosearch/venue/123
    - PUT /py/geosearch/venue/123
    - DELETE /py/geosearch/venue/123
    """
    return await _dispatch_request(controller, function, item_id, request)


# Create alias for the main router export
router = dynamic_router
