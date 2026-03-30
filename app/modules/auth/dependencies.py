"""Auth FastAPI dependencies."""

from __future__ import annotations

from fastapi import Header
from sqlalchemy import text

from app.modules.auth.constants import (
    AUTH_FORBIDDEN,
    AUTH_TOKEN_VERSION_MISMATCH,
    AUTH_UNAUTHORIZED,
    HEADER_AUTHORIZATION,
)
from app.modules.auth.schemas.models import CurrentV2User
from app.modules.auth.services.common import AuthError
from app.modules.auth.services.token_service import verify_access_token
from app.core.database import central_session_context
from app.core.settings import get_settings


def _normalize_roles(raw_roles: object) -> list[dict]:
    roles: list[dict] = []
    if not isinstance(raw_roles, list):
        return roles

    for item in raw_roles:
        if isinstance(item, dict):
            code = str(item.get("role_code") or item.get("code") or "").strip()
            name = str(item.get("role_name") or item.get("name") or code).strip()
            if code:
                roles.append({"role_code": code, "role_name": name or code})
            continue
        if isinstance(item, str):
            code = item.strip()
            if code:
                roles.append({"role_code": code, "role_name": code})
    return roles


async def require_auth(authorization: str | None = Header(default=None, alias=HEADER_AUTHORIZATION)) -> CurrentV2User:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError(AUTH_UNAUTHORIZED, "Missing or invalid Authorization header", 401)

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise AuthError(AUTH_UNAUTHORIZED, "Missing bearer token", 401)

    claims = verify_access_token(token)
    if int(claims.get("auth_ver", -1)) != int(get_settings().AUTH_V2_TOKEN_VERSION):
        raise AuthError(AUTH_TOKEN_VERSION_MISMATCH, "Token version mismatch", 401)

    user_id = claims.get("user_id")
    token_jti = claims.get("jti")
    if user_id is None or not token_jti:
        raise AuthError(AUTH_UNAUTHORIZED, "Session invalid", 401)

    async with central_session_context() as db:
        active_row = await db.execute(
            text(
                """
                SELECT id
                FROM auth_refresh_token
                WHERE user_id = :user_id
                  AND token_jti = :token_jti
                  AND revoked_at IS NULL
                  AND expires_at > UTC_TIMESTAMP()
                LIMIT 1
                """
            ),
            {"user_id": int(user_id), "token_jti": str(token_jti)},
        )
        if active_row.fetchone() is None:
            raise AuthError(AUTH_UNAUTHORIZED, "Session revoked. Please login again.", 401)

    claims = dict(claims)
    claims["roles"] = _normalize_roles(claims.get("roles"))
    claims["permissions"] = (
        [str(x) for x in claims.get("permissions", []) if str(x)]
        if isinstance(claims.get("permissions"), list)
        else []
    )
    claims["is_super"] = bool(claims.get("is_super", False))
    claims["permissions_version"] = int(claims.get("permissions_version", 0) or 0)
    claims["permissions_schema_version"] = int(claims.get("permissions_schema_version", 1) or 1)

    known_keys = {
        "sub",
        "user_id",
        "contact_id",
        "employee_id",
        "roles",
        "mobile",
        "jti",
        "iat",
        "exp",
        "iss",
        "aud",
        "auth_ver",
        "typ",
        "position_id",
        "position",
        "department_id",
        "department",
        "permissions",
        "is_super",
        "permissions_version",
        "permissions_schema_version",
    }
    extra = {k: v for k, v in claims.items() if k not in known_keys}
    return CurrentV2User(**claims, extra=extra)


async def require_super_auth(
    authorization: str | None = Header(default=None, alias=HEADER_AUTHORIZATION),
) -> CurrentV2User:
    current_user = await require_auth(authorization=authorization)
    if not bool(current_user.is_super):
        raise AuthError(AUTH_FORBIDDEN, "Super admin permission required", 403)
    return current_user


async def require_v2_auth(authorization: str | None = Header(default=None, alias=HEADER_AUTHORIZATION)) -> CurrentV2User:
    return await require_auth(authorization=authorization)


async def require_v2_super_auth(
    authorization: str | None = Header(default=None, alias=HEADER_AUTHORIZATION),
) -> CurrentV2User:
    return await require_super_auth(authorization=authorization)

