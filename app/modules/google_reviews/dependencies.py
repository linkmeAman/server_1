"""Auth dependency and domain error for Google Reviews routes."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.database import central_session_context
from app.core.prism_pdp import PDPRequest, evaluate
from app.modules.auth.services.token_service import verify_access_token
from app.modules.auth.services.common import AuthError


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
        return verify_access_token(token)
    except AuthError as exc:
        raise GoogleReviewsError(
            code="REVIEWS_UNAUTHORIZED",
            message=exc.message if hasattr(exc, "message") else "Invalid or expired access token",
            status_code=401,
        ) from exc
    except Exception as exc:
        raise GoogleReviewsError(
            code="REVIEWS_UNAUTHORIZED",
            message="Invalid or expired access token",
            status_code=401,
        ) from exc


async def has_google_reviews_permission(claims: Dict[str, Any], action: str) -> bool:
    """Return True when the caller is supreme or PRISM allows the action."""
    if bool(claims.get("is_super")):
        return True

    user_id = claims.get("user_id") or claims.get("sub")
    if user_id is None:
        return False

    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return False

    async with central_session_context() as central_db:
        result = await evaluate(
            PDPRequest(
                user_id=user_id_int,
                action=action,
                resource_type="google_reviews",
                resource_id="*",
            ),
            central_db,
        )
    return result.decision == "Allow"
