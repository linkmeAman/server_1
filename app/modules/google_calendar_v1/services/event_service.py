"""Business workflows for Google Calendar V1 endpoints."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.core.settings import get_settings

from ..dependencies import GoogleCalendarError
from .datetime_utils import normalize_google_event_for_log, select_next_upcoming_instance_id
from .event_log_repository import CalendarEventLogRepository, LogPersistenceError
from .google_client import GoogleCalendarClient
from .token_manager import GoogleCalendarTokenManager


class GoogleCalendarEventService:
    """Coordinates Google API calls and log persistence."""

    def __init__(
        self,
        client: Optional[GoogleCalendarClient] = None,
        log_repository: Optional[CalendarEventLogRepository] = None,
        token_manager: Optional[GoogleCalendarTokenManager] = None,
    ):
        self.client = client or GoogleCalendarClient()
        self.log_repository = log_repository or CalendarEventLogRepository()
        self.token_manager = token_manager or GoogleCalendarTokenManager()

    @staticmethod
    def _extract_upstream_error(status_code: int, payload: Dict[str, Any]) -> Tuple[Any, str]:
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            response_code = error.get("code", status_code)
            response_message = str(error.get("message") or "Google Calendar request failed")
            return response_code, response_message

        if isinstance(error, str):
            return status_code, error

        if isinstance(payload, dict) and payload.get("message"):
            return status_code, str(payload.get("message"))

        return status_code, "Google Calendar request failed"

    @staticmethod
    def _map_upstream_status(status_code: int) -> int:
        if 400 <= status_code <= 499:
            return status_code
        return 502

    def _normalize_for_log(self, event_payload: Dict[str, Any]) -> Dict[str, Any]:
        settings = get_settings()
        return normalize_google_event_for_log(
            event_payload,
            fallback_timezone=settings.GOOGLE_CALENDAR_COMPARE_TIMEZONE,
        )

    @staticmethod
    def _calendar_id_from_settings() -> str:
        settings = get_settings()
        calendar_id = str(getattr(settings, "GOOGLE_CALENDAR_ID", "") or "").strip()
        if not calendar_id:
            raise GoogleCalendarError(
                code="GCAL_CONFIG_ERROR",
                message="GOOGLE_CALENDAR_ID is not configured",
                status_code=500,
            )
        return calendar_id

    async def create_event(
        self,
        event: Dict[str, Any],
        actor_name: str,
        actor_email: str,
    ) -> Dict[str, Any]:
        calendar_id = self._calendar_id_from_settings()
        google_access_token = await self.token_manager.get_valid_access_token()
        status_code, payload = await self.client.create_event(calendar_id, event, google_access_token)
        if status_code not in (200, 201):
            response_code, response_message = self._extract_upstream_error(status_code, payload)
            try:
                self.log_repository.insert_create_error_log(
                    actor_name=actor_name,
                    actor_email=actor_email,
                    response_code=response_code,
                    response_message=response_message,
                )
            except LogPersistenceError as exc:
                raise GoogleCalendarError(
                    code="GCAL_LOG_PERSISTENCE_FAILED",
                    message="Failed to persist calendar error log",
                    status_code=500,
                ) from exc

            raise GoogleCalendarError(
                code="GCAL_UPSTREAM_ERROR",
                message=response_message,
                status_code=self._map_upstream_status(status_code),
                data={
                    "response_code": response_code,
                    "response_body": payload,
                },
            )

        normalized = self._normalize_for_log(payload)
        try:
            self.log_repository.insert_create_success_log(actor_name, actor_email, normalized)
        except LogPersistenceError as exc:
            raise GoogleCalendarError(
                code="GCAL_LOG_PERSISTENCE_FAILED",
                message="Failed to persist calendar event log",
                status_code=500,
            ) from exc

        return {
            "google_event": payload,
            "log_status": "create_logged",
        }

    async def update_event(
        self,
        event_id: str,
        event: Dict[str, Any],
        actor_name: str,
        log_row_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        calendar_id = self._calendar_id_from_settings()
        google_access_token = await self.token_manager.get_valid_access_token()
        status_code, payload = await self.client.update_event(
            calendar_id,
            event_id,
            event,
            google_access_token,
        )
        if status_code != 200:
            response_code, response_message = self._extract_upstream_error(status_code, payload)
            raise GoogleCalendarError(
                code="GCAL_UPSTREAM_ERROR",
                message=response_message,
                status_code=self._map_upstream_status(status_code),
                data={
                    "response_code": response_code,
                    "response_body": payload,
                },
            )

        normalized = self._normalize_for_log(payload)
        try:
            self.log_repository.update_event_log(
                actor_name=actor_name,
                normalized_event=normalized,
                log_row_id=log_row_id,
            )
        except LogPersistenceError as exc:
            raise GoogleCalendarError(
                code="GCAL_LOG_PERSISTENCE_FAILED",
                message="Failed to persist calendar update log",
                status_code=500,
            ) from exc

        return {
            "google_event": payload,
            "log_status": "update_logged",
        }

    async def delete_event(
        self,
        event_id: str,
        delete_mode: str = "full",
    ) -> Dict[str, Any]:
        calendar_id = self._calendar_id_from_settings()
        google_access_token = await self.token_manager.get_valid_access_token()
        target_event_id = event_id

        if delete_mode == "next_instance":
            status_code, payload = await self.client.list_instances(
                calendar_id,
                event_id,
                google_access_token,
            )
            if status_code != 200:
                response_code, response_message = self._extract_upstream_error(status_code, payload)
                raise GoogleCalendarError(
                    code="GCAL_UPSTREAM_ERROR",
                    message=response_message,
                    status_code=self._map_upstream_status(status_code),
                    data={
                        "response_code": response_code,
                        "response_body": payload,
                    },
                )

            settings = get_settings()
            instances = payload.get("items", []) if isinstance(payload, dict) else []
            target_event_id = select_next_upcoming_instance_id(
                instances=instances,
                compare_timezone=settings.GOOGLE_CALENDAR_COMPARE_TIMEZONE,
            )
            if not target_event_id:
                raise GoogleCalendarError(
                    code="GCAL_INSTANCE_NOT_FOUND",
                    message="No upcoming instance found to delete",
                    status_code=404,
                )

        status_code, payload = await self.client.delete_event(
            calendar_id,
            target_event_id,
            google_access_token,
        )
        if status_code not in (204, 410):
            response_code, response_message = self._extract_upstream_error(status_code, payload)
            raise GoogleCalendarError(
                code="GCAL_UPSTREAM_ERROR",
                message=response_message,
                status_code=self._map_upstream_status(status_code),
                data={
                    "response_code": response_code,
                    "response_body": payload,
                },
            )

        try:
            self.log_repository.mark_event_deleted(event_id)
        except LogPersistenceError as exc:
            raise GoogleCalendarError(
                code="GCAL_LOG_PERSISTENCE_FAILED",
                message="Failed to persist calendar delete log",
                status_code=500,
            ) from exc

        return {
            "delete_mode": delete_mode,
            "deleted_event_id": target_event_id,
            "already_deleted": status_code == 410,
            "google_status": status_code,
        }
