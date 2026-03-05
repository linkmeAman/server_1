"""Dependencies and errors for Employee Events V1."""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.security import validate_token


class EmployeeEventsError(Exception):
    """Domain error for employee-events APIs with stable codes."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        data: Optional[Dict[str, Any]] = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.data = data or {}
        super().__init__(message)


def require_app_access_claims(authorization_header: Optional[str]) -> Dict[str, Any]:
    """Validate app bearer access token and return claims."""
    if not authorization_header or not authorization_header.startswith("Bearer "):
        raise EmployeeEventsError(
            code="EMP_EVENT_UNAUTHORIZED",
            message="Missing or invalid Authorization header",
            status_code=401,
        )

    token = authorization_header.split(" ", 1)[1].strip()
    if not token:
        raise EmployeeEventsError(
            code="EMP_EVENT_UNAUTHORIZED",
            message="Missing app access token",
            status_code=401,
        )

    try:
        return validate_token(token, expected_type="access")
    except Exception as exc:
        raise EmployeeEventsError(
            code="EMP_EVENT_UNAUTHORIZED",
            message="Invalid or expired app access token",
            status_code=401,
        ) from exc
