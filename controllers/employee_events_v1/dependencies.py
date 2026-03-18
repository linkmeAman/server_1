"""Dependencies and errors for Employee Events V1."""

from __future__ import annotations

from typing import Any, Dict, Optional

from controllers.auth.services.token_service import verify_access_token
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
    """Validate app bearer access token and return claims.

    Supports both:
    - legacy app access tokens from `/login`
    - auth access tokens from `/auth/login-employee`
    """
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

    legacy_error = None
    try:
        claims = validate_token(token, expected_type="access")
        claims["_auth_token_family"] = "legacy"
        return claims
    except Exception as exc:
        legacy_error = str(exc)

    try:
        claims = verify_access_token(token)
        claims["_auth_token_family"] = "auth_v2"
        return claims
    except Exception as exc:
        raise EmployeeEventsError(
            code="EMP_EVENT_UNAUTHORIZED",
            message="Invalid or expired app access token",
            status_code=401,
            data={
                "legacy_reason": legacy_error or "validation_failed",
                "auth_v2_reason": str(exc),
            },
        ) from exc
