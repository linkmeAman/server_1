"""Token issuance and verification for auth v2 (PASETO v4.local)."""

from __future__ import annotations

import base64
import json
from datetime import timedelta
from typing import Any, Dict, List, Optional

from controllers.auth_v2.constants import (
    AUTH_INVALID_TOKEN,
    AUTH_TOKEN_VERSION_MISMATCH,
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
)
from controllers.auth_v2.services.common import AuthV2Error, random_jti, utcnow
from controllers.auth_v2.services.keyring import get_current_key, get_key_for_kid
from core.settings import get_settings


def _load_pyseto():
    try:
        import pyseto  # type: ignore
        from pyseto import Key  # type: ignore
    except Exception as exc:  # pragma: no cover - runtime dependency check
        raise RuntimeError("pyseto is required for auth v2 token operations") from exc
    return pyseto, Key


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("utf-8"))


def _build_footer(kid: str) -> bytes:
    return json.dumps({"kid": kid}, separators=(",", ":")).encode("utf-8")


def _parse_footer_kid(token: str) -> str:
    parts = token.split(".")
    if len(parts) < 3:
        raise AuthV2Error(AUTH_INVALID_TOKEN, "Malformed token", 401)

    footer_segment = parts[3] if len(parts) >= 4 else ""
    if not footer_segment:
        raise AuthV2Error(AUTH_INVALID_TOKEN, "Missing token kid footer", 401)

    try:
        footer = json.loads(_b64url_decode(footer_segment).decode("utf-8"))
    except Exception as exc:
        raise AuthV2Error(AUTH_INVALID_TOKEN, "Invalid token footer", 401) from exc

    kid = str(footer.get("kid", "")).strip()
    if not kid:
        raise AuthV2Error(AUTH_INVALID_TOKEN, "Missing token kid", 401)
    return kid


def _payload(subject: str, token_type: str, expires_delta: timedelta, claims: Dict[str, Any]) -> Dict[str, Any]:
    now = utcnow()
    settings = get_settings()
    payload: Dict[str, Any] = {
        "sub": subject,
        "typ": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "iss": settings.AUTH_V2_ISSUER,
        "aud": settings.AUTH_V2_AUDIENCE,
        "auth_ver": settings.AUTH_V2_TOKEN_VERSION,
    }
    payload.update(claims)
    return payload


def _encode(payload: Dict[str, Any], kid: str, secret_bytes: bytes) -> str:
    pyseto, Key = _load_pyseto()
    key = Key.new(version=4, purpose="local", key=secret_bytes)
    token = pyseto.encode(
        key,
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"),
        footer=_build_footer(kid),
    )
    return token.decode("utf-8") if isinstance(token, bytes) else token


def _decode(token: str) -> Dict[str, Any]:
    pyseto, Key = _load_pyseto()
    kid = _parse_footer_kid(token)
    key_record = get_key_for_kid(kid)
    key = Key.new(version=4, purpose="local", key=key_record.key_bytes)

    try:
        decoded = pyseto.decode(key, token)
    except Exception as exc:
        raise AuthV2Error(AUTH_INVALID_TOKEN, "Invalid or expired token", 401) from exc

    payload = decoded.payload if hasattr(decoded, "payload") else decoded
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except Exception as exc:
            raise AuthV2Error(AUTH_INVALID_TOKEN, "Invalid token payload", 401) from exc
        if not isinstance(parsed, dict):
            raise AuthV2Error(AUTH_INVALID_TOKEN, "Invalid token payload", 401)
        return parsed
    if isinstance(payload, dict):
        return payload
    raise AuthV2Error(AUTH_INVALID_TOKEN, "Invalid token payload", 401)


def _validate_claims(payload: Dict[str, Any], expected_type: Optional[str] = None) -> Dict[str, Any]:
    settings = get_settings()
    required = {
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
    }
    missing = [claim for claim in required if claim not in payload]
    if missing:
        raise AuthV2Error(AUTH_INVALID_TOKEN, f"Missing required claims: {', '.join(missing)}", 401)

    if int(payload.get("auth_ver", -1)) != int(settings.AUTH_V2_TOKEN_VERSION):
        raise AuthV2Error(AUTH_TOKEN_VERSION_MISMATCH, "Token version mismatch", 401)

    if expected_type and payload.get("typ") != expected_type:
        raise AuthV2Error(AUTH_INVALID_TOKEN, "Invalid token type", 401)

    now_ts = int(utcnow().timestamp())
    if int(payload.get("exp", 0)) <= now_ts:
        raise AuthV2Error(AUTH_INVALID_TOKEN, "Token expired", 401)

    if payload.get("iss") != settings.AUTH_V2_ISSUER:
        raise AuthV2Error(AUTH_INVALID_TOKEN, "Invalid token issuer", 401)

    if payload.get("aud") != settings.AUTH_V2_AUDIENCE:
        raise AuthV2Error(AUTH_INVALID_TOKEN, "Invalid token audience", 401)

    return payload


def issue_v2_token_pair(
    user_id: int,
    contact_id: int,
    employee_id: int,
    roles: List[Any],
    mobile: str,
    authorization: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    settings = get_settings()
    record = get_current_key()
    jti = random_jti()

    role_claims: List[Any] = []
    for role in roles:
        if isinstance(role, dict):
            code = str(role.get("role_code") or role.get("code") or "").strip()
            if not code:
                continue
            name = str(role.get("role_name") or role.get("name") or code).strip()
            role_claims.append({"role_code": code, "role_name": name or code})
            continue
        code = str(role).strip()
        if code:
            role_claims.append(code)

    authz = authorization or {}
    permissions = authz.get("permissions", [])
    if not isinstance(permissions, list):
        permissions = []

    base_claims: Dict[str, Any] = {
        "user_id": int(user_id),
        "contact_id": int(contact_id),
        "employee_id": int(employee_id),
        "roles": role_claims,
        "mobile": str(mobile),
        "jti": jti,
        "position_id": authz.get("position_id"),
        "position": authz.get("position"),
        "department_id": authz.get("department_id"),
        "department": authz.get("department"),
        "permissions": [str(permission) for permission in permissions if str(permission)],
        "is_super": bool(authz.get("is_super", False)),
        "permissions_version": int(authz.get("permissions_version", 0) or 0),
        "permissions_schema_version": int(authz.get("permissions_schema_version", 1) or 1),
    }

    access_payload = _payload(
        subject=str(user_id),
        token_type=TOKEN_TYPE_ACCESS,
        expires_delta=timedelta(minutes=int(settings.AUTH_V2_ACCESS_TOKEN_MINUTES)),
        claims=base_claims,
    )
    refresh_payload = _payload(
        subject=str(user_id),
        token_type=TOKEN_TYPE_REFRESH,
        expires_delta=timedelta(days=int(settings.AUTH_V2_REFRESH_TOKEN_DAYS)),
        claims=base_claims,
    )

    access_token = _encode(access_payload, record.kid, record.key_bytes)
    refresh_token = _encode(refresh_payload, record.kid, record.key_bytes)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "jti": jti,
    }


def verify_v2_access_token(token: str) -> Dict[str, Any]:
    payload = _decode(token)
    return _validate_claims(payload, expected_type=TOKEN_TYPE_ACCESS)


def verify_v2_refresh_token(token: str) -> Dict[str, Any]:
    payload = _decode(token)
    return _validate_claims(payload, expected_type=TOKEN_TYPE_REFRESH)
