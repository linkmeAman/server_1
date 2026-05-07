"""Auth dependency and domain error for Google Reviews routes."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.security import validate_token


class GoogleReviewsError(Exception):
    """Stable domain error for Google Reviews API responses."""

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


def require_auth(authorization_header: Optional[str]) -> Dict[str, Any]:
    """Validate bearer token and return claims. Raises GoogleReviewsError on failure."""
    if not authorization_header or not authorization_header.startswith("Bearer "):
        raise GoogleReviewsError(
            code="REVIEWS_UNAUTHORIZED",
            message="Missing or invalid Authorization header",
            status_code=401,
        )

    token = authorization_header.split(" ", 1)[1].strip()
    if not token:
        raise GoogleReviewsError(
            code="REVIEWS_UNAUTHORIZED",
            message="Missing access token",
            status_code=401,
        )

    try:
        return validate_token(token, expected_type="access")
    except Exception as exc:
        raise GoogleReviewsError(
            code="REVIEWS_UNAUTHORIZED",
            message="Invalid or expired access token",
            status_code=401,
        ) from exc
