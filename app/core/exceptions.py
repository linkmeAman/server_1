"""
Custom exceptions for the dynamic API system
"""
from fastapi import HTTPException
from typing import Optional


class DynamicAPIException(Exception):
    """Base exception for dynamic API system"""
    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


class ControllerNotFoundException(DynamicAPIException):
    """Raised when a controller is not found"""
    def __init__(self, controller: str):
        message = f"Controller '{controller}' not found"
        super().__init__(message, 404)


class FunctionNotFoundException(DynamicAPIException):
    """Raised when a function is not found in a controller"""
    def __init__(self, controller: str, function: str):
        message = f"Function '{function}' not found in controller '{controller}'"
        super().__init__(message, 404)


class InvalidControllerNameException(DynamicAPIException):
    """Raised when controller name is invalid"""
    def __init__(self, controller: str):
        message = f"Invalid controller name: '{controller}'"
        super().__init__(message, 400)


class InvalidFunctionNameException(DynamicAPIException):
    """Raised when function name is invalid"""
    def __init__(self, function: str):
        message = f"Invalid function name: '{function}'"
        super().__init__(message, 400)


class PrivateMethodAccessException(DynamicAPIException):
    """Raised when trying to access private methods"""
    def __init__(self, function: str):
        message = f"Access to private method '{function}' is not allowed"
        super().__init__(message, 403)


class ParameterValidationException(DynamicAPIException):
    """Raised when parameter validation fails"""
    def __init__(self, message: str):
        super().__init__(f"Parameter validation error: {message}", 400)


class ControllerExecutionException(DynamicAPIException):
    """Raised when controller function execution fails"""
    def __init__(self, controller: str, function: str, error: str):
        message = f"Error executing {controller}.{function}: {error}"
        super().__init__(message, 500)


def dynamic_api_exception_handler(request, exc: DynamicAPIException):
    """Convert DynamicAPIException to HTTPException"""
    raise HTTPException(status_code=exc.status_code, detail=exc.message)