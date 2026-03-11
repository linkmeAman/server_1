"""Auth v2 FastAPI dependencies."""

from __future__ import annotations

from fastapi import Header

from controllers.auth_v2.constants import (
    AUTH_FORBIDDEN,
    AUTH_TOKEN_VERSION_MISMATCH,
    AUTH_UNAUTHORIZED,
    HEADER_AUTHORIZATION,
)
from controllers.auth_v2.schemas.models import CurrentV2User
from controllers.auth_v2.services.common import AuthV2Error
from controllers.auth_v2.services.token_service import verify_v2_access_token
from core.settings import get_settings


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


async def require_v2_auth(authorization: str | None = Header(default=None, alias=HEADER_AUTHORIZATION)) -> CurrentV2User:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthV2Error(AUTH_UNAUTHORIZED, "Missing or invalid Authorization header", 401)

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise AuthV2Error(AUTH_UNAUTHORIZED, "Missing bearer token", 401)

    claims = verify_v2_access_token(token)
    if int(claims.get("auth_ver", -1)) != int(get_settings().AUTH_V2_TOKEN_VERSION):
        raise AuthV2Error(AUTH_TOKEN_VERSION_MISMATCH, "Token version mismatch", 401)

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


async def require_v2_super_auth(
    authorization: str | None = Header(default=None, alias=HEADER_AUTHORIZATION),
) -> CurrentV2User:
    current_user = await require_v2_auth(authorization=authorization)
    if not bool(current_user.is_super):
        raise AuthV2Error(AUTH_FORBIDDEN, "Super admin permission required", 403)
    return current_user
