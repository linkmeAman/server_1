"""
Dynamic function loader and executor
This is the core engine that discovers and executes controller functions
"""
import importlib
import inspect
import re
import logging
from typing import Any, Dict, Optional, Union, get_type_hints
from fastapi import HTTPException

from .settings import get_settings
from .exceptions import (
    ControllerNotFoundException,
    FunctionNotFoundException,
    InvalidControllerNameException,
    InvalidFunctionNameException,
    PrivateMethodAccessException,
    ParameterValidationException,
    ControllerExecutionException
)

# Set up logging
logger = logging.getLogger(__name__)

# Security: Safe name pattern (alphanumeric + underscore, must start with letter)
SAFE_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")

# Cache for loaded modules to improve performance
_module_cache = {}

# Canonical controller modules for the legacy dynamic /py/{controller}/{function}
# surface. Keep this mapping explicit so runtime behavior no longer depends on
# the deprecated controllers/ directory layout.
CANONICAL_CONTROLLER_MODULES: dict[str, str] = {
    "example": "app.modules.example.service",
    "geosearch": "app.modules.geosearch.service",
    "llm": "app.modules.llm.service",
    "orders": "app.modules.orders.router",
}


def validate_name(name: str, name_type: str = "name") -> bool:
    """Validate controller or function name for security"""
    settings = get_settings()
    
    if not name:
        return False
        
    # Check pattern
    if not SAFE_NAME_PATTERN.match(name):
        return False
        
    # Check length limits
    max_length = (settings.MAX_CONTROLLER_NAME_LENGTH if name_type == "controller" 
                 else settings.MAX_FUNCTION_NAME_LENGTH)
    
    if len(name) > max_length:
        return False
        
    return True


def convert_parameter(value: str, param_type: type) -> Any:
    """Convert string parameter to the appropriate type"""
    if param_type == str or param_type == Any:
        return value
    elif param_type == int:
        try:
            return int(value)
        except ValueError:
            raise ParameterValidationException(f"Cannot convert '{value}' to int")
    elif param_type == float:
        try:
            return float(value)
        except ValueError:
            raise ParameterValidationException(f"Cannot convert '{value}' to float")
    elif param_type == bool:
        return value.lower() in ('true', '1', 'yes', 'on', 'enabled')
    else:
        # For complex types, return as string and let the function handle it
        return value


def load_controller_module(controller_name: str):
    """Dynamically load a controller module with caching"""
    
    # Validate controller name
    if not validate_name(controller_name, "controller"):
        raise InvalidControllerNameException(controller_name)
    
    # Check cache first
    if controller_name in _module_cache:
        return _module_cache[controller_name]
    
    try:
        module_path = CANONICAL_CONTROLLER_MODULES.get(controller_name)
        if module_path is None:
            raise ControllerNotFoundException(controller_name)
        module = importlib.import_module(module_path)
        
        # Cache the module
        _module_cache[controller_name] = module
        
        logger.info(f"Loaded controller module: {controller_name}")
        return module
        
    except ImportError as e:
        logger.error(f"Failed to import controller '{controller_name}': {e}")
        raise ControllerNotFoundException(controller_name)


def list_registered_controllers() -> list[str]:
    """Return the controller names available to the dynamic router."""
    return sorted(CANONICAL_CONTROLLER_MODULES.keys())


def get_function_from_module(module, function_name: str, controller_name: str):
    """Get function from module with security checks"""
    settings = get_settings()
    
    # Validate function name
    if not validate_name(function_name, "function"):
        raise InvalidFunctionNameException(function_name)
    
    # Check for private methods
    if function_name.startswith('_') and not settings.ALLOW_PRIVATE_METHODS:
        raise PrivateMethodAccessException(function_name)
    
    # Get the function
    func = getattr(module, function_name, None)
    if func is None:
        raise FunctionNotFoundException(controller_name, function_name)
    
    # Ensure it's callable
    if not callable(func):
        raise FunctionNotFoundException(controller_name, function_name)
    
    return func


def prepare_function_arguments(
    func: callable,
    item_id: Optional[str] = None,
    query_params: Optional[Dict[str, str]] = None,
    body_params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Prepare function arguments from request parameters"""
    
    # Get function signature and type hints
    sig = inspect.signature(func)
    type_hints = get_type_hints(func)
    
    kwargs = {}
    
    # Handle item_id parameter (common pattern: /controller/function/123)
    if item_id is not None:
        # Check if function accepts 'id' parameter
        if 'id' in sig.parameters:
            param_type = type_hints.get('id', str)
            kwargs['id'] = convert_parameter(item_id, param_type)
        # Also check for 'item_id' parameter
        elif 'item_id' in sig.parameters:
            param_type = type_hints.get('item_id', str)
            kwargs['item_id'] = convert_parameter(item_id, param_type)
    
    # Handle query parameters
    if query_params:
        for param_name, param_value in query_params.items():
            if param_name in sig.parameters:
                param_type = type_hints.get(param_name, str)
                kwargs[param_name] = convert_parameter(param_value, param_type)
    
    # Handle body parameters (JSON payload)
    if body_params:
        for param_name, param_value in body_params.items():
            if param_name in sig.parameters:
                # Body parameters are already in correct type from JSON parsing
                kwargs[param_name] = param_value
    
    # Check for required parameters
    for param_name, param in sig.parameters.items():
        if param.default == inspect.Parameter.empty and param_name not in kwargs:
            # This is a required parameter that wasn't provided
            if param_name not in ['self', 'cls']:  # Ignore self/cls for methods
                raise ParameterValidationException(
                    f"Required parameter '{param_name}' not provided"
                )
    
    return kwargs


async def call_function(
    controller_name: str,
    function_name: str,
    item_id: Optional[str] = None,
    query_params: Optional[Dict[str, str]] = None,
    body_params: Optional[Dict[str, Any]] = None
) -> Any:
    """
    Main function to dynamically call controller functions
    
    Args:
        controller_name: Name of the controller (e.g., 'geosearch')
        function_name: Name of the function (e.g., 'search')
        item_id: Optional ID parameter from URL path
        query_params: Query string parameters
        body_params: JSON body parameters
        
    Returns:
        Result from the controller function
    """
    
    try:
        # Load the controller module
        module = load_controller_module(controller_name)
        
        # Get the function
        func = get_function_from_module(module, function_name, controller_name)
        
        # Prepare function arguments
        kwargs = prepare_function_arguments(func, item_id, query_params, body_params)
        
        # Log the function call
        logger.info(f"Calling {controller_name}.{function_name} with args: {list(kwargs.keys())}")
        
        # Execute the function
        if inspect.iscoroutinefunction(func):
            # Async function
            result = await func(**kwargs)
        else:
            # Sync function
            result = func(**kwargs)
        
        logger.info(f"Successfully executed {controller_name}.{function_name}")
        return result
        
    except (ControllerNotFoundException, FunctionNotFoundException, 
            InvalidControllerNameException, InvalidFunctionNameException,
            PrivateMethodAccessException, ParameterValidationException) as e:
        # Re-raise known exceptions
        raise e
        
    except Exception as e:
        # Wrap unknown exceptions
        logger.error(f"Error executing {controller_name}.{function_name}: {str(e)}")
        raise ControllerExecutionException(controller_name, function_name, str(e))


def clear_module_cache():
    """Clear the module cache (useful for development/testing)"""
    global _module_cache
    _module_cache.clear()
    logger.info("Module cache cleared")


def get_controller_functions(controller_name: str) -> Dict[str, Any]:
    """Get all available functions from a controller (useful for debugging/docs)"""
    try:
        module = load_controller_module(controller_name)
        functions = {}
        
        for name in dir(module):
            if not name.startswith('_'):  # Skip private methods
                obj = getattr(module, name)
                if callable(obj) and not inspect.isclass(obj):
                    sig = inspect.signature(obj)
                    functions[name] = {
                        'signature': str(sig),
                        'doc': inspect.getdoc(obj),
                        'is_async': inspect.iscoroutinefunction(obj)
                    }
        
        return functions
        
    except Exception as e:
        logger.error(f"Error getting functions for controller {controller_name}: {e}")
        return {}
