"""PRISM — Session Guard (FastAPI dependency)

Validates the caller's Bearer token and ensures they are authorized to
manage PRISM objects (supreme user or super_admin role holder).

Usage in any endpoint:
    from core.prism_guard import CallerContext, require_prism_caller, require_prism_super

    @router.post("/something")
    async def endpoint(caller: CallerContext = Depends(require_prism_caller)):
        ...  # caller.user_id, caller.is_super, caller.employee_id available

    # For operations only super-admins can do (e.g. setting permission boundaries):
    @router.post("/critical")
    async def critical(caller: CallerContext = Depends(require_prism_super)):
        ...
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.database import central_session_context
from sqlalchemy import text

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


def _row(result) -> Optional[dict]:
    row = result.fetchone()
    return dict(row._mapping) if row else None


@dataclass
class CallerContext:
    """Resolved identity of the caller making a PRISM management request."""
    user_id: int
    is_super: bool                       # True if auth_supreme_user.is_super = 1
    employee_id: Optional[int] = None
    contact_id: Optional[int] = None
    mobile: Optional[str] = None
    display_name: Optional[str] = None
    token_claims: dict = field(default_factory=dict)


def _verify_token(token: str) -> dict:
    """Try auth-v2 keyring first, fall back to legacy single-key PASETO.

    Returns the decoded claims dict on success.
    Raises ValueError on any failure.
    """
    # Auth v2 (keyring-based — preferred path for all new sessions)
    try:
        from app.modules.auth.services.token_service import verify_v2_access_token
        claims = verify_v2_access_token(token)
        if claims:
            return claims
    except Exception as e:
        logger.debug("auth-v2 token verification failed: %s", e)

    # Legacy single-key PASETO (core/security.py)
    try:
        from core.security import validate_token
        claims = validate_token(token, expected_type="access")
        if claims:
            return claims
    except Exception as e:
        logger.debug("legacy token verification failed: %s", e)

    raise ValueError("Token is invalid or expired")


async def _ensure_active_session(user_id: int, token_jti: str) -> None:
    """Fail if the caller's auth session has been revoked or expired."""
    async with central_session_context() as db:
        row = _row(await db.execute(
            text(
                """
                SELECT id
                FROM auth_refresh_token
                WHERE user_id = :uid
                  AND token_jti = :jti
                  AND revoked_at IS NULL
                  AND expires_at > UTC_TIMESTAMP()
                LIMIT 1
                """
            ),
            {"uid": int(user_id), "jti": str(token_jti)},
        ))

    if not row:
        raise HTTPException(status_code=401, detail="Session revoked. Please login again.")


async def _resolve_caller(token: str) -> CallerContext:
    """Decode token → verify active supreme user → return CallerContext."""
    try:
        claims = _verify_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    user_id: Optional[int] = claims.get("user_id") or claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing user identity")

    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user identity in token")

    token_jti = claims.get("jti")
    if not token_jti:
        raise HTTPException(status_code=401, detail="Token missing session identifier")
    await _ensure_active_session(user_id, str(token_jti))

    # Verify the user exists in auth_supreme_user and is active
    async with central_session_context() as db:
        supreme = _row(await db.execute(
            text(
                "SELECT id, is_super, is_active, display_name, mobile, country_code "
                "FROM auth_supreme_user WHERE id = :uid LIMIT 1"
            ),
            {"uid": user_id},
        ))

    if not supreme:
        raise HTTPException(status_code=403, detail="Not a recognized supreme user")
    if not supreme["is_active"]:
        raise HTTPException(status_code=403, detail="Supreme user account is inactive")

    return CallerContext(
        user_id=user_id,
        is_super=bool(supreme.get("is_super")),
        employee_id=claims.get("employee_id"),
        contact_id=claims.get("contact_id"),
        mobile=supreme.get("mobile"),
        display_name=supreme.get("display_name"),
        token_claims=claims,
    )


async def _resolve_any_caller(token: str) -> CallerContext:
    """Decode token → verify signature only → return CallerContext.

    Does NOT check auth_supreme_user.  Safe for read-only self-service
    endpoints (e.g. /prism/evaluate/me/permissions) where the caller is
    fetching their own data.
    """
    try:
        claims = _verify_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    user_id: Optional[int] = claims.get("user_id") or claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing user identity")

    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user identity in token")

    token_jti = claims.get("jti")
    if not token_jti:
        raise HTTPException(status_code=401, detail="Token missing session identifier")
    await _ensure_active_session(user_id, str(token_jti))

    return CallerContext(
        user_id=user_id,
        is_super=bool(claims.get("is_super", False)),
        employee_id=claims.get("employee_id"),
        contact_id=claims.get("contact_id"),
        token_claims=claims,
    )


async def require_prism_caller(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> CallerContext:
    """Dependency: any active supreme user may call PRISM management endpoints.

    Raises 401 if no valid token is present.
    Raises 403 if the token belongs to a non-supreme or inactive account.
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="Authorization: Bearer <token> header is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await _resolve_caller(credentials.credentials)


async def require_prism_super(
    caller: CallerContext = Depends(require_prism_caller),
) -> CallerContext:
    """Dependency: only is_super=True users may call this endpoint.

    Used for high-privilege operations:
      - Setting permission boundaries
      - Removing permission boundaries
      - Deactivating system roles
    """
    if not caller.is_super:
        raise HTTPException(
            status_code=403,
            detail="This operation requires is_super=True (full super-admin privilege)",
        )
    return caller


async def require_any_caller(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> CallerContext:
    """Dependency: requires a valid Bearer token but NOT supreme user status.

    Use this for self-service endpoints where any authenticated user
    (employee or supreme) may fetch their own data.
    e.g. GET /prism/evaluate/me/permissions
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="Authorization: Bearer <token> header is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await _resolve_any_caller(credentials.credentials)


async def resolve_caller_from_request(request: Request) -> Optional["CallerContext"]:
    """Imperatively extract and validate the Bearer token from a Request object.

    Unlike the Depends-based helpers, this can be called directly from any
    coroutine (e.g. dynamic router middleware) without FastAPI dependency
    injection.

    Returns None (does NOT raise) when the request carries no Authorization
    header or the token is missing — callers should treat this as anonymous.
    Raises HTTPException(401/403) when a token IS present but invalid.
    """
    auth_header: str = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header[7:].strip()
    if not token:
        return None
    return await _resolve_any_caller(token)


