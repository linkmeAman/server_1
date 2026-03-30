"""Central DB-backed Google OAuth token manager for Calendar V1."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx
from sqlalchemy import text

from app.core.database import engines
from app.core.settings import get_settings

from ..dependencies import GoogleCalendarError


class GoogleCalendarTokenManager:
    """Loads, validates, refreshes, and persists Google access tokens."""

    def __init__(self):
        settings = get_settings()
        self.token_id = int(settings.GOOGLE_DRIVE_TOKEN_ID)
        self.refresh_skew_seconds = int(settings.GOOGLE_TOKEN_REFRESH_SKEW_SECONDS)
        self.token_url = settings.GOOGLE_OAUTH_TOKEN_URL
        self.timeout_seconds = float(settings.GOOGLE_CALENDAR_TIMEOUT_SECONDS)

    @staticmethod
    def _get_central_engine():
        settings = get_settings()
        db_map = getattr(settings, "SQL_GATEWAY_DB_ENGINE_MAP", {})

        engine_key = "central"
        if isinstance(db_map, dict):
            mapped = db_map.get("CENTRAL")
            if isinstance(mapped, str) and mapped:
                engine_key = mapped

        engine = engines.get(engine_key)
        if engine is None:
            raise GoogleCalendarError(
                code="GCAL_TOKEN_UNAVAILABLE",
                message="Central DB engine is not available",
                status_code=503,
            )
        return engine

    def _fetch_token_row(self) -> Dict[str, Any]:
        engine = self._get_central_engine()

        sql = text(
            """
            SELECT `id`, `type`, `access_token`, `client_id`, `client_secret`,
                   `refresh_token`, `token_type`, `expires_in`, `created`, `updated_at`
            FROM `google_drive_token`
            WHERE `id` = :token_id
            """
        )
        with engine.connect() as conn:
            row = conn.execute(sql, {"token_id": self.token_id}).mappings().first()

        if row is None:
            raise GoogleCalendarError(
                code="GCAL_TOKEN_UNAVAILABLE",
                message=f"google_drive_token row not found for id={self.token_id}",
                status_code=404,
            )

        return dict(row)

    @staticmethod
    def _coerce_datetime(value: Any) -> Optional[datetime]:
        if value is None:
            return None

        if isinstance(value, datetime):
            return value

        raw = str(value).strip()
        if not raw:
            return None

        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw

        try:
            return datetime.fromisoformat(normalized)
        except Exception:
            return None

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        try:
            if value is None:
                return None
            return int(value)
        except Exception:
            return None

    def _is_access_token_valid(self, row: Dict[str, Any]) -> bool:
        access_token = str(row.get("access_token") or "").strip()
        if not access_token:
            return False

        expires_in = self._coerce_int(row.get("expires_in"))
        created = self._coerce_datetime(row.get("created"))
        if expires_in is None or expires_in <= 0 or created is None:
            return False

        created_utc = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
        expiry_utc = created_utc + timedelta(seconds=expires_in)
        check_time = datetime.now(timezone.utc) + timedelta(seconds=self.refresh_skew_seconds)
        return expiry_utc > check_time

    async def _refresh_access_token(self, row: Dict[str, Any]) -> Dict[str, Any]:
        refresh_token = str(row.get("refresh_token") or "").strip()
        client_id = str(row.get("client_id") or "").strip()
        client_secret = str(row.get("client_secret") or "").strip()

        if not refresh_token or not client_id or not client_secret:
            raise GoogleCalendarError(
                code="GCAL_TOKEN_REFRESH_FAILED",
                message="Missing refresh credentials in google_drive_token",
                status_code=500,
            )

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as client:
                response = await client.post(self.token_url, data=data)
        except httpx.RequestError as exc:
            raise GoogleCalendarError(
                code="GCAL_TOKEN_REFRESH_FAILED",
                message="Failed to reach Google OAuth token endpoint",
                status_code=502,
                data={"reason": str(exc)},
            ) from exc

        try:
            payload = response.json() if response.text else {}
        except Exception:
            payload = {"raw": response.text}

        if response.status_code != 200:
            message = "Failed to refresh Google access token"
            if isinstance(payload, dict) and payload.get("error_description"):
                message = str(payload["error_description"])
            elif isinstance(payload, dict) and payload.get("error"):
                message = str(payload["error"])

            raise GoogleCalendarError(
                code="GCAL_TOKEN_REFRESH_FAILED",
                message=message,
                status_code=502,
                data={"response_body": payload},
            )

        access_token = str((payload or {}).get("access_token") or "").strip()
        if not access_token:
            raise GoogleCalendarError(
                code="GCAL_TOKEN_REFRESH_FAILED",
                message="Token refresh response missing access_token",
                status_code=502,
                data={"response_body": payload},
            )

        token_type = str((payload or {}).get("token_type") or row.get("token_type") or "Bearer")
        expires_in = self._coerce_int((payload or {}).get("expires_in")) or 3600

        return {
            "access_token": access_token,
            "token_type": token_type,
            "expires_in": expires_in,
        }

    def _persist_refreshed_token(self, refreshed: Dict[str, Any]) -> None:
        engine = self._get_central_engine()

        sql = text(
            """
            UPDATE `google_drive_token`
            SET `access_token` = :access_token,
                `token_type` = :token_type,
                `expires_in` = :expires_in,
                `created` = UTC_TIMESTAMP(),
                `updated_at` = UTC_TIMESTAMP()
            WHERE `id` = :token_id
            """
        )

        with engine.begin() as conn:
            conn.execute(
                sql,
                {
                    "access_token": refreshed["access_token"],
                    "token_type": refreshed["token_type"],
                    "expires_in": refreshed["expires_in"],
                    "token_id": self.token_id,
                },
            )

    async def get_valid_access_token(self) -> str:
        row = self._fetch_token_row()
        if self._is_access_token_valid(row):
            return str(row.get("access_token")).strip()

        refreshed = await self._refresh_access_token(row)
        self._persist_refreshed_token(refreshed)
        return refreshed["access_token"]
