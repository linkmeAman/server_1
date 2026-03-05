"""Business workflows for Employee Events V1."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from core.settings import get_settings

from ..dependencies import EmployeeEventsError
from .event_repository import EmployeeEventsRepository
from .google_payload_builder import build_google_event_payload
from .google_sync_repository import EmployeeEventGoogleSyncRepository
from ...google_calendar_v1.services.google_client import GoogleCalendarClient
from ...google_calendar_v1.services.token_manager import GoogleCalendarTokenManager


class EmployeeEventsService:
    """Coordinates local employee-event writes and Google sync."""

    def __init__(
        self,
        event_repository: Optional[EmployeeEventsRepository] = None,
        sync_repository: Optional[EmployeeEventGoogleSyncRepository] = None,
        google_client: Optional[GoogleCalendarClient] = None,
        token_manager: Optional[GoogleCalendarTokenManager] = None,
    ):
        self.event_repository = event_repository or EmployeeEventsRepository()
        self.sync_repository = sync_repository or EmployeeEventGoogleSyncRepository()
        self.google_client = google_client or GoogleCalendarClient()
        self.token_manager = token_manager or GoogleCalendarTokenManager()

    @staticmethod
    def _extract_upstream_error(status_code: int, payload: Dict[str, Any]) -> Tuple[str, str]:
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            code = str(error.get("code", status_code))
            message = str(error.get("message") or "Google Calendar request failed")
            return code, message

        if isinstance(error, str):
            return str(status_code), error

        if isinstance(payload, dict) and payload.get("message"):
            return str(status_code), str(payload.get("message"))

        return str(status_code), "Google Calendar request failed"

    @staticmethod
    def _map_upstream_status(status_code: int) -> int:
        if 400 <= status_code <= 499:
            return status_code
        return 502

    @staticmethod
    def _as_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _to_text(value: Any) -> str:
        return "" if value is None else str(value)

    def _approved_status(self) -> int:
        settings = get_settings()
        return int(settings.EMP_EVENT_APPROVED_STATUS)

    def _parked_value(self) -> int:
        settings = get_settings()
        return int(settings.EMP_EVENT_PARKED_VALUE)

    def _sync_enabled(self) -> bool:
        settings = get_settings()
        return bool(settings.EMP_EVENT_ENABLE_GOOGLE_SYNC)

    def _configured_calendar_id(self) -> str:
        settings = get_settings()
        return str(getattr(settings, "GOOGLE_CALENDAR_ID", "") or "").strip()

    def _calendar_id(self) -> str:
        calendar_id = self._configured_calendar_id()
        if calendar_id:
            return calendar_id

        raise EmployeeEventsError(
            code="EMP_EVENT_CONFIG_ERROR",
            message="GOOGLE_CALENDAR_ID is not configured",
            status_code=500,
        )

    async def _google_create_for_event(self, event_row: Dict[str, Any]) -> Dict[str, Any]:
        allowances = self.event_repository.get_allowances(int(event_row["id"]))
        contact = self.event_repository.get_contact(self._as_int(event_row.get("contact_id")))
        try:
            payload = build_google_event_payload(event_row, allowances, contact)
        except Exception as exc:
            raise EmployeeEventsError(
                code="EMP_EVENT_GOOGLE_PAYLOAD_INVALID",
                message=f"Could not build Google event payload: {exc}",
                status_code=400,
            ) from exc

        access_token = await self.token_manager.get_valid_access_token()
        calendar_id = self._calendar_id()
        status_code, response_body = await self.google_client.create_event(
            calendar_id,
            payload,
            access_token,
        )
        if status_code not in (200, 201):
            err_code, err_message = self._extract_upstream_error(status_code, response_body)
            raise EmployeeEventsError(
                code="EMP_EVENT_SYNC_FAILED",
                message=err_message,
                status_code=self._map_upstream_status(status_code),
                data={"upstream_code": err_code, "response_body": response_body},
            )
        return response_body

    async def _google_update_for_event(
        self,
        google_event_id: str,
        event_row: Dict[str, Any],
    ) -> Dict[str, Any]:
        allowances = self.event_repository.get_allowances(int(event_row["id"]))
        contact = self.event_repository.get_contact(self._as_int(event_row.get("contact_id")))
        try:
            payload = build_google_event_payload(event_row, allowances, contact)
        except Exception as exc:
            raise EmployeeEventsError(
                code="EMP_EVENT_GOOGLE_PAYLOAD_INVALID",
                message=f"Could not build Google event payload: {exc}",
                status_code=400,
            ) from exc

        access_token = await self.token_manager.get_valid_access_token()
        calendar_id = self._calendar_id()
        status_code, response_body = await self.google_client.update_event(
            calendar_id,
            google_event_id,
            payload,
            access_token,
        )
        if status_code != 200:
            err_code, err_message = self._extract_upstream_error(status_code, response_body)
            raise EmployeeEventsError(
                code="EMP_EVENT_SYNC_FAILED",
                message=err_message,
                status_code=self._map_upstream_status(status_code),
                data={"upstream_code": err_code, "response_body": response_body},
            )
        return response_body

    async def _google_delete_event(self, google_event_id: str) -> Dict[str, Any]:
        access_token = await self.token_manager.get_valid_access_token()
        calendar_id = self._calendar_id()
        status_code, response_body = await self.google_client.delete_event(
            calendar_id,
            google_event_id,
            access_token,
        )
        if status_code not in (204, 410):
            err_code, err_message = self._extract_upstream_error(status_code, response_body)
            raise EmployeeEventsError(
                code="EMP_EVENT_SYNC_FAILED",
                message=err_message,
                status_code=self._map_upstream_status(status_code),
                data={"upstream_code": err_code, "response_body": response_body},
            )
        return {
            "deleted": True,
            "already_deleted": status_code == 410,
            "google_status": status_code,
        }

    def check_conflict(
        self,
        date: str,
        start_time: str,
        end_time: str,
        contact_id: int,
        exclude_event_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        conflict_ids = self.event_repository.check_conflict(
            date=date,
            start_time=start_time,
            end_time=end_time,
            contact_id=int(contact_id),
            parked_value=self._parked_value(),
            exclude_event_id=exclude_event_id,
        )
        return {
            "conflict": bool(conflict_ids),
            "conflict_event_ids": conflict_ids,
        }

    def create_event(self, payload: Dict[str, Any], actor_user_id: str) -> Dict[str, Any]:
        event_id = self.event_repository.create_event_with_allowances(payload, actor_user_id)

        if self._sync_enabled():
            calendar_id = self._configured_calendar_id()
            link = self.sync_repository.upsert_pending(event_id, calendar_id)
            sync_status = self._to_text(link.get("sync_status") or "pending_approval")
        else:
            sync_status = "sync_disabled"

        return {
            "event_id": int(event_id),
            "sync_status": sync_status,
        }

    def get_realtime_employee_data(self) -> Dict[str, Any]:
        employees = self.event_repository.list_realtime_employees()
        branches = self.event_repository.list_active_branches()
        return {
            "employees": employees,
            "branches": branches,
            "employee_count": len(employees),
            "branch_count": len(branches),
        }

    def list_events(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        contact_id: Optional[int] = None,
        status: Optional[int] = None,
        park: Optional[int] = None,
        include_parked: bool = True,
    ) -> Dict[str, Any]:
        rows = self.event_repository.list_events(
            from_date=from_date,
            to_date=to_date,
            contact_id=contact_id,
            status=status,
            park=park,
            include_parked=include_parked,
            parked_value=self._parked_value(),
        )

        event_ids = [int(row.get("id")) for row in rows if row.get("id") is not None]
        allowances_by_event = self.event_repository.get_allowances_for_event_ids(event_ids)
        sync_by_event = self.sync_repository.get_links_by_event_ids(event_ids) if self._sync_enabled() else {}

        events: list[Dict[str, Any]] = []
        for row in rows:
            event_id = int(row["id"])
            event = dict(row)

            contact_lookup_id = event.pop("contact_lookup_id", None)
            contact = {
                "id": contact_lookup_id,
                "fname": event.pop("contact_fname", None),
                "mname": event.pop("contact_mname", None),
                "lname": event.pop("contact_lname", None),
                "parent_name": event.pop("contact_parent_name", None),
                "country_code": event.pop("contact_country_code", None),
                "mobile": event.pop("contact_mobile", None),
                "email": event.pop("contact_email", None),
            }
            name_parts = [
                str(contact.get("fname") or "").strip(),
                str(contact.get("mname") or "").strip(),
                str(contact.get("lname") or "").strip(),
            ]
            contact["full_name"] = " ".join(part for part in name_parts if part)

            sync_row = sync_by_event.get(event_id, {})
            sync = {
                "google_event_id": sync_row.get("google_event_id"),
                "google_calendar_id": sync_row.get("google_calendar_id"),
                "sync_status": sync_row.get("sync_status") or (
                    "pending_approval" if self._sync_enabled() else "sync_disabled"
                ),
                "last_error_code": sync_row.get("last_error_code"),
                "last_error_message": sync_row.get("last_error_message"),
                "updated_at": sync_row.get("updated_at"),
            }

            event["allowance_items"] = allowances_by_event.get(event_id, [])
            event["contact"] = contact
            event["sync"] = sync
            events.append(event)

        return {
            "events": events,
            "count": len(events),
        }

    async def update_event(
        self,
        event_id: int,
        payload: Dict[str, Any],
        actor_user_id: str,
    ) -> Dict[str, Any]:
        self.event_repository.update_event_with_allowances(event_id, payload, actor_user_id)
        event_row = self.event_repository.get_event(event_id)

        approved_status = self._approved_status()
        parked_value = self._parked_value()
        current_status = self._as_int(event_row.get("status"))
        current_park = self._as_int(event_row.get("park"))

        if not self._sync_enabled():
            return {
                "event_id": int(event_id),
                "sync_status": "sync_disabled",
                "synced": False,
            }

        if current_status != approved_status or current_park == parked_value:
            return {
                "event_id": int(event_id),
                "sync_status": "pending_approval" if current_status != approved_status else "parked",
                "synced": False,
            }

        link = self.sync_repository.get_link(event_id)

        if link and self._to_text(link.get("google_event_id")):
            google_event_id = self._to_text(link.get("google_event_id"))
            try:
                calendar_id = self._calendar_id()
                google_event = await self._google_update_for_event(google_event_id, event_row)
                self.sync_repository.mark_active(event_id, google_event_id, calendar_id)
                return {
                    "event_id": int(event_id),
                    "sync_status": "active",
                    "synced": True,
                    "sync_action": "google_updated",
                    "google_event_id": google_event_id,
                    "google_event": google_event,
                }
            except EmployeeEventsError as exc:
                self.sync_repository.mark_error(event_id, "update_failed", exc.code, exc.message)
                return {
                    "event_id": int(event_id),
                    "sync_status": "update_failed",
                    "synced": False,
                    "sync_action": "google_update_failed",
                    "error_code": exc.code,
                    "error_message": exc.message,
                }

        try:
            calendar_id = self._calendar_id()
            google_event = await self._google_create_for_event(event_row)
            google_event_id = self._to_text(google_event.get("id"))
            if not google_event_id:
                raise EmployeeEventsError(
                    code="EMP_EVENT_SYNC_FAILED",
                    message="Google create did not return event id",
                    status_code=502,
                )
            self.sync_repository.mark_active(event_id, google_event_id, calendar_id)
            return {
                "event_id": int(event_id),
                "sync_status": "active",
                "synced": True,
                "sync_action": "google_created",
                "google_event_id": google_event_id,
                "google_event": google_event,
            }
        except EmployeeEventsError as exc:
            self.sync_repository.mark_error(event_id, "create_failed", exc.code, exc.message)
            return {
                "event_id": int(event_id),
                "sync_status": "create_failed",
                "synced": False,
                "sync_action": "google_create_failed",
                "error_code": exc.code,
                "error_message": exc.message,
            }

    async def park_event(self, event_id: int, park_value: int) -> Dict[str, Any]:
        self.event_repository.set_park(event_id, int(park_value))

        parked_value = self._parked_value()
        if int(park_value) != parked_value:
            return {
                "event_id": int(event_id),
                "park_value": int(park_value),
                "sync_status": "none",
                "synced": False,
                "sync_action": "none",
            }

        if not self._sync_enabled():
            return {
                "event_id": int(event_id),
                "park_value": int(park_value),
                "sync_status": "sync_disabled",
                "synced": False,
                "sync_action": "none",
            }

        link = self.sync_repository.get_link(event_id)
        google_event_id = self._to_text((link or {}).get("google_event_id"))
        if not google_event_id:
            self.sync_repository.mark_deleted(event_id)
            return {
                "event_id": int(event_id),
                "park_value": int(park_value),
                "sync_status": "deleted",
                "synced": True,
                "sync_action": "no_google_event",
            }

        try:
            delete_result = await self._google_delete_event(google_event_id)
            self.sync_repository.mark_deleted(event_id)
            return {
                "event_id": int(event_id),
                "park_value": int(park_value),
                "sync_status": "deleted",
                "synced": True,
                "sync_action": "google_deleted",
                "google_event_id": google_event_id,
                **delete_result,
            }
        except EmployeeEventsError as exc:
            self.sync_repository.mark_error(event_id, "delete_failed", exc.code, exc.message)
            return {
                "event_id": int(event_id),
                "park_value": int(park_value),
                "sync_status": "delete_failed",
                "synced": False,
                "sync_action": "google_delete_failed",
                "google_event_id": google_event_id,
                "error_code": exc.code,
                "error_message": exc.message,
            }

    async def approve_event(
        self,
        event_id: int,
        requested_status: Optional[int],
    ) -> Dict[str, Any]:
        approved_status = self._approved_status()
        status_to_set = approved_status if requested_status is None else int(requested_status)

        event_row = self.event_repository.get_event(event_id)
        current_status = self._as_int(event_row.get("status"))
        parked_value = self._parked_value()
        current_park = self._as_int(event_row.get("park"))

        if status_to_set != approved_status:
            # When an already-approved event is moved to a non-approved state (e.g. rejected),
            # remove linked Google event to keep calendar in sync with business status.
            if (
                self._sync_enabled()
                and current_status == approved_status
                and current_park != parked_value
            ):
                link = self.sync_repository.get_link(event_id)
                google_event_id = self._to_text((link or {}).get("google_event_id"))

                if google_event_id:
                    try:
                        delete_result = await self._google_delete_event(google_event_id)
                        self.sync_repository.mark_deleted(event_id)
                        self.event_repository.set_status(event_id, status_to_set)
                        return {
                            "event_id": int(event_id),
                            "status": int(status_to_set),
                            "sync_status": "deleted",
                            "synced": True,
                            "sync_action": "google_deleted",
                            "google_event_id": google_event_id,
                            **delete_result,
                        }
                    except EmployeeEventsError as exc:
                        self.sync_repository.mark_error(
                            event_id,
                            "delete_failed",
                            exc.code,
                            exc.message,
                        )
                        self.event_repository.set_status(event_id, status_to_set)
                        return {
                            "event_id": int(event_id),
                            "status": int(status_to_set),
                            "sync_status": "delete_failed",
                            "synced": False,
                            "sync_action": "google_delete_failed",
                            "google_event_id": google_event_id,
                            "error_code": exc.code,
                            "error_message": exc.message,
                        }

                self.sync_repository.mark_deleted(event_id)
                self.event_repository.set_status(event_id, status_to_set)
                return {
                    "event_id": int(event_id),
                    "status": int(status_to_set),
                    "sync_status": "deleted",
                    "synced": True,
                    "sync_action": "no_google_event",
                }

            self.event_repository.set_status(event_id, status_to_set)
            return {
                "event_id": int(event_id),
                "status": int(status_to_set),
                "sync_status": "none",
                "synced": False,
                "sync_action": "status_only",
            }

        if current_park == parked_value:
            raise EmployeeEventsError(
                code="EMP_EVENT_INVALID_STATE",
                message="Cannot approve a parked event",
                status_code=409,
            )

        if not self._sync_enabled():
            self.event_repository.set_status(event_id, approved_status)
            return {
                "event_id": int(event_id),
                "status": int(approved_status),
                "sync_status": "sync_disabled",
                "synced": False,
                "sync_action": "status_only",
            }

        calendar_id = self._calendar_id()
        link = self.sync_repository.upsert_pending(event_id, calendar_id)
        existing_google_event_id = self._to_text(link.get("google_event_id"))
        if self._to_text(link.get("sync_status")) == "active" and existing_google_event_id:
            self.event_repository.set_status(event_id, approved_status)
            return {
                "event_id": int(event_id),
                "status": int(approved_status),
                "sync_status": "active",
                "synced": True,
                "sync_action": "idempotent",
                "google_event_id": existing_google_event_id,
            }

        try:
            google_event = await self._google_create_for_event(event_row)
            google_event_id = self._to_text(google_event.get("id"))
            if not google_event_id:
                raise EmployeeEventsError(
                    code="EMP_EVENT_SYNC_FAILED",
                    message="Google create did not return event id",
                    status_code=502,
                )
        except EmployeeEventsError as exc:
            self.sync_repository.mark_error(event_id, "create_failed", exc.code, exc.message)
            raise

        try:
            self.event_repository.set_status(event_id, approved_status)
            self.sync_repository.mark_active(event_id, google_event_id, calendar_id)
        except EmployeeEventsError as exc:
            self.sync_repository.mark_error(
                event_id,
                "create_failed",
                "EMP_EVENT_DB_WRITE_FAILED",
                f"Google event created but local approve write failed: {exc.message}",
            )
            raise

        return {
            "event_id": int(event_id),
            "status": int(approved_status),
            "sync_status": "active",
            "synced": True,
            "sync_action": "google_created",
            "google_event_id": google_event_id,
            "google_event": google_event,
        }
