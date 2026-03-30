"""Security helpers for bcrypt password hashing and PASETO v4.local tokens."""

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from .settings import get_settings


class SecurityDependencyError(RuntimeError):
    """Raised when required security libraries are missing."""


def _load_bcrypt_module():
    try:
        import bcrypt  # type: ignore
    except Exception as exc:
        raise SecurityDependencyError(
            "bcrypt is required for password hashing. Install `bcrypt`."
        ) from exc
    return bcrypt


def _load_pyseto_module():
    try:
        import pyseto  # type: ignore
        from pyseto import Key  # type: ignore
    except Exception as exc:
        raise SecurityDependencyError(
            "pyseto is required for PASETO tokens. Install `pyseto`."
        ) from exc
    return pyseto, Key


def hash_password(password: str) -> str:
    """Hash plaintext password with bcrypt."""
    bcrypt = _load_bcrypt_module()
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify plaintext password against a bcrypt hash."""
    bcrypt = _load_bcrypt_module()
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def _paseto_key():
    """Build a deterministic 32-byte symmetric key for v4.local."""
    pyseto, Key = _load_pyseto_module()
    settings = get_settings()
    raw_secret = settings.PASETO_SECRET_KEY.strip()
    if not raw_secret:
        raise RuntimeError("PASETO_SECRET_KEY is not configured")
    key_bytes = hashlib.sha256(raw_secret.encode("utf-8")).digest()
    return pyseto, Key.new(version=4, purpose="local", key=key_bytes)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _encode_paseto_payload(payload: Dict[str, Any]) -> str:
    pyseto, key = _paseto_key()
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    token = pyseto.encode(key, payload_bytes)
    return token.decode("utf-8") if isinstance(token, bytes) else token


def decode_paseto_token(token: str) -> Dict[str, Any]:
    """Decode and return PASETO payload as dict."""
    pyseto, key = _paseto_key()
    decoded = pyseto.decode(key, token)
    payload = decoded.payload if hasattr(decoded, "payload") else decoded
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        return json.loads(payload)
    if isinstance(payload, dict):
        return payload
    raise ValueError("Invalid PASETO payload format")


def _build_token_payload(
    subject: str,
    token_type: str,
    expires_delta: timedelta,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    now = _utc_now()
    payload: Dict[str, Any] = {
        "sub": subject,
        "typ": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    if extra_claims:
        payload.update(extra_claims)
    return payload


def create_access_token(subject: str, extra_claims: Optional[Dict[str, Any]] = None) -> str:
    """Create short-lived PASETO access token."""
    settings = get_settings()
    payload = _build_token_payload(
        subject=subject,
        token_type="access",
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        extra_claims=extra_claims,
    )
    return _encode_paseto_payload(payload)


def create_refresh_token(subject: str, extra_claims: Optional[Dict[str, Any]] = None) -> str:
    """Create longer-lived PASETO refresh token."""
    settings = get_settings()
    payload = _build_token_payload(
        subject=subject,
        token_type="refresh",
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        extra_claims=extra_claims,
    )
    return _encode_paseto_payload(payload)


def validate_token(token: str, expected_type: Optional[str] = None) -> Dict[str, Any]:
    """Decode and validate expiration/type for PASETO token."""
    payload = decode_paseto_token(token)
    exp = int(payload.get("exp", 0))
    now_ts = int(_utc_now().timestamp())

    if exp <= now_ts:
        raise ValueError("Token expired")

    if expected_type and payload.get("typ") != expected_type:
        raise ValueError(f"Invalid token type: expected {expected_type}")

    return payload


def generate_reset_token() -> str:
    """Generate secure one-time token for password reset flow."""
    return secrets.token_urlsafe(48)

