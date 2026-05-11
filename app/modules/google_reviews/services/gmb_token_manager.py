"""Google OAuth token manager for GMB API access.

Loads credentials from settings (service account JSON or stored OAuth tokens)
and provides a valid access token for API calls.

Two supported modes (controlled by GMB_AUTH_MODE setting):
  - "service_account" : Uses a Google service account JSON key
  - "token_store"     : Reads/refreshes an OAuth token stored in the DB
    (same table used by google_calendar_v1: google_drive_token)

Default: "service_account" (recommended for server-to-server).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx
from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials

from app.core.settings import get_settings

from ..dependencies import GoogleReviewsError

logger = logging.getLogger(__name__)

_GMB_SCOPES = [
    "https://www.googleapis.com/auth/business.manage",
]


class GmbTokenManager:
    """Provides a valid Google OAuth 2.0 access token for GMB API calls."""

    def __init__(self) -> None:
        settings = get_settings()
        self._auth_mode: str = getattr(settings, "GMB_AUTH_MODE", "service_account").lower()
        self._sa_json: str = getattr(settings, "GMB_SERVICE_ACCOUNT_JSON", "")
        self._token_id: int = int(getattr(settings, "GOOGLE_DRIVE_TOKEN_ID", 2))
        self._token_url: str = getattr(
            settings, "GOOGLE_OAUTH_TOKEN_URL", "https://oauth2.googleapis.com/token"
        )
        self._timeout: float = float(getattr(settings, "GMB_TIMEOUT_SECONDS", 30))
        # Simple in-memory cache to avoid redundant refreshes
        self._cached_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def get_valid_access_token(self) -> str:
        """Return a non-expired access token, refreshing when necessary."""
        now = datetime.now(tz=timezone.utc)
        if self._cached_token and self._token_expiry and self._token_expiry > now + timedelta(seconds=60):
            return self._cached_token

        if self._auth_mode == "service_account":
            return await self._refresh_service_account()
        elif self._auth_mode == "token_store":
            return await self._refresh_from_token_store()
        else:
            raise GoogleReviewsError(
                code="GMB_CONFIG_ERROR",
                message=f"Unknown GMB_AUTH_MODE: {self._auth_mode!r}. Use 'service_account' or 'token_store'.",
                status_code=500,
            )

    # ------------------------------------------------------------------
    # Service account flow
    # ------------------------------------------------------------------

    async def _refresh_service_account(self) -> str:
        if not self._sa_json:
            raise GoogleReviewsError(
                code="GMB_CONFIG_ERROR",
                message="GMB_SERVICE_ACCOUNT_JSON is not configured",
                status_code=500,
            )

        try:
            sa_info = json.loads(self._sa_json)
        except json.JSONDecodeError as exc:
            raise GoogleReviewsError(
                code="GMB_CONFIG_ERROR",
                message="GMB_SERVICE_ACCOUNT_JSON is not valid JSON",
                status_code=500,
            ) from exc

        try:
            creds = service_account.Credentials.from_service_account_info(
                sa_info, scopes=_GMB_SCOPES
            )
            # google-auth uses a sync Request; wrap it
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, creds.refresh, GoogleAuthRequest())

            self._cached_token = creds.token
            self._token_expiry = creds.expiry.replace(tzinfo=timezone.utc) if creds.expiry else None
            return self._cached_token  # type: ignore[return-value]
        except Exception as exc:
            raise GoogleReviewsError(
                code="GMB_AUTH_ERROR",
                message=f"Service account credential refresh failed: {exc}",
                status_code=502,
            ) from exc

    # ------------------------------------------------------------------
    # Token store flow (reuses google_drive_token table from calendar v1)
    # ------------------------------------------------------------------

    async def _refresh_from_token_store(self) -> str:
        """Load token row from DB, refresh if expired, persist back, return access token."""
        from app.core.database import engines
        from sqlalchemy import text as sa_text

        def _get_engine():
            settings = get_settings()
            db_map = getattr(settings, "SQL_GATEWAY_DB_ENGINE_MAP", {})
            key = "central"
            if isinstance(db_map, dict):
                mapped = db_map.get("CENTRAL")
                if isinstance(mapped, str) and mapped:
                    key = mapped
            eng = engines.get(key)
            if eng is None:
                raise GoogleReviewsError(
                    code="GMB_TOKEN_UNAVAILABLE",
                    message="Central DB engine not available",
                    status_code=503,
                )
            return eng

        engine = _get_engine()
        sql_select = sa_text(
            "SELECT id, access_token, client_id, client_secret, refresh_token, "
            "       expires_in, created, updated_at "
            "FROM google_drive_token WHERE id = :tid"
        )
        with engine.connect() as conn:
            row = conn.execute(sql_select, {"tid": self._token_id}).mappings().first()

        if not row:
            raise GoogleReviewsError(
                code="GMB_TOKEN_UNAVAILABLE",
                message=f"google_drive_token row not found for id={self._token_id}",
                status_code=404,
            )

        row_dict: Dict[str, Any] = dict(row)
        expiry = _compute_expiry(row_dict)
        now = datetime.now(tz=timezone.utc)

        # If still valid, return cached
        if expiry and expiry > now + timedelta(seconds=60):
            self._cached_token = row_dict["access_token"]
            self._token_expiry = expiry
            return self._cached_token

        # Refresh
        new_token = await _do_refresh(
            row_dict["client_id"],
            row_dict["client_secret"],
            row_dict["refresh_token"],
            self._token_url,
            self._timeout,
        )
        # Persist new access token
        sql_update = sa_text(
            "UPDATE google_drive_token SET access_token = :at, expires_in = :ei, "
            "created = UNIX_TIMESTAMP(), updated_at = NOW() WHERE id = :tid"
        )
        with engine.begin() as conn:
            conn.execute(sql_update, {"at": new_token["access_token"], "ei": new_token["expires_in"], "tid": self._token_id})

        self._cached_token = new_token["access_token"]
        self._token_expiry = datetime.now(tz=timezone.utc) + timedelta(seconds=new_token["expires_in"] - 60)
        return self._cached_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_expiry(row: Dict[str, Any]) -> Optional[datetime]:
    created_raw = row.get("created")
    expires_in = row.get("expires_in") or 3600
    if created_raw is None:
        return None
    if isinstance(created_raw, datetime):
        created = created_raw.replace(tzinfo=timezone.utc)
    else:
        try:
            ts = float(created_raw)
            created = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (TypeError, ValueError):
            return None
    return created + timedelta(seconds=int(expires_in))


async def _do_refresh(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    token_url: str,
    timeout: float,
) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            token_url,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if response.status_code != 200:
        raise GoogleReviewsError(
            code="GMB_TOKEN_REFRESH_FAILED",
            message="Google OAuth token refresh failed",
            status_code=502,
            data={"status": response.status_code},
        )
    return response.json()
