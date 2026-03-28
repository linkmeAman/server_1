"""
Standardized response formats for the dynamic API system
"""
from typing import Any, Optional, Dict, List
from pydantic import BaseModel
from datetime import datetime


class APIResponse(BaseModel):
    """Standard API response format"""
    success: bool = True
    data: Any = None
    message: Optional[str] = None
    error: Optional[str] = None
    timestamp: datetime = datetime.utcnow()
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class PaginatedResponse(APIResponse):
    """Paginated response format"""
    data: List[Any] = []
    pagination: Optional[Dict[str, Any]] = None
    total_count: Optional[int] = None


class ErrorResponse(APIResponse):
    """Error response format"""
    success: bool = False
    error: str
    message: str
    data: Any = None


def success_response(
    data: Any = None, 
    message: str = "Success"
) -> APIResponse:
    """Create a success response"""
    return APIResponse(
        success=True,
        data=data,
        message=message
    )


def error_response(
    error: str, 
    message: str = "An error occurred",
    data: Any = None
) -> ErrorResponse:
    """Create an error response"""
    return ErrorResponse(
        success=False,
        error=error,
        message=message,
        data=data
    )


def paginated_response(
    data: List[Any],
    total_count: int,
    page: int = 1,
    per_page: int = 10,
    message: str = "Success"
) -> PaginatedResponse:
    """Create a paginated response"""
    total_pages = (total_count + per_page - 1) // per_page
    
    pagination = {
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "total_count": total_count,
        "has_next": page < total_pages,
        "has_prev": page > 1
    }
    
    return PaginatedResponse(
        success=True,
        data=data,
        message=message,
        pagination=pagination,
        total_count=total_count
    )