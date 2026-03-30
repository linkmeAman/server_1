"""Database log persistence for Google Calendar V1 operations."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import text

from app.core.database import get_db_session


class LogPersistenceError(RuntimeError):
    """Raised when calendar event logging fails."""


class CalendarEventLogRepository:
    """Repository for `calendar_event_logs` write operations."""

    def _execute_write(self, sql: str, params: Dict[str, Any]) -> int:
        db = get_db_session()
        try:
            result = db.execute(text(sql), params)
            db.commit()
            return int(result.rowcount or 0)
        except Exception as exc:
            db.rollback()
            raise LogPersistenceError(str(exc)) from exc
        finally:
            db.close()

    def insert_create_success_log(
        self,
        actor_name: str,
        actor_email: str,
        normalized_event: Dict[str, Any],
    ) -> None:
        sql = """
        INSERT INTO calendar_event_logs (
            event_created_by,
            creators_email,
            event_id,
            summary,
            description,
            location,
            event_start,
            event_end,
            event_timezone,
            attendees,
            can_attendees_modify,
            created_at
        ) VALUES (
            :event_created_by,
            :creators_email,
            :event_id,
            :summary,
            :description,
            :location,
            :event_start,
            :event_end,
            :event_timezone,
            :attendees,
            :can_attendees_modify,
            CURRENT_TIMESTAMP()
        )
        """
        self._execute_write(
            sql,
            {
                "event_created_by": actor_name,
                "creators_email": actor_email,
                "event_id": normalized_event.get("event_id"),
                "summary": normalized_event.get("summary"),
                "description": normalized_event.get("description"),
                "location": normalized_event.get("location"),
                "event_start": normalized_event.get("event_start"),
                "event_end": normalized_event.get("event_end"),
                "event_timezone": normalized_event.get("event_timezone"),
                "attendees": normalized_event.get("attendees"),
                "can_attendees_modify": normalized_event.get("can_attendees_modify"),
            },
        )

    def insert_create_error_log(
        self,
        actor_name: str,
        actor_email: str,
        response_code: Any,
        response_message: str,
    ) -> None:
        sql = """
        INSERT INTO calendar_event_logs (
            event_created_by,
            creators_email,
            response_code,
            response_message,
            created_at
        ) VALUES (
            :event_created_by,
            :creators_email,
            :response_code,
            :response_message,
            CURRENT_TIMESTAMP()
        )
        """
        self._execute_write(
            sql,
            {
                "event_created_by": actor_name,
                "creators_email": actor_email,
                "response_code": response_code,
                "response_message": response_message,
            },
        )

    def update_event_log(
        self,
        actor_name: str,
        normalized_event: Dict[str, Any],
        log_row_id: Optional[int] = None,
    ) -> None:
        if log_row_id is not None:
            sql = """
            UPDATE calendar_event_logs
            SET
                updated_by = :updated_by,
                event_id = :event_id,
                summary = :summary,
                description = :description,
                location = :location,
                event_start = :event_start,
                event_end = :event_end,
                event_timezone = :event_timezone,
                attendees = :attendees,
                can_attendees_modify = :can_attendees_modify
            WHERE id = :row_id AND event_id = :event_id
            """
            params = {
                "updated_by": actor_name,
                "row_id": log_row_id,
                "event_id": normalized_event.get("event_id"),
                "summary": normalized_event.get("summary"),
                "description": normalized_event.get("description"),
                "location": normalized_event.get("location"),
                "event_start": normalized_event.get("event_start"),
                "event_end": normalized_event.get("event_end"),
                "event_timezone": normalized_event.get("event_timezone"),
                "attendees": normalized_event.get("attendees"),
                "can_attendees_modify": normalized_event.get("can_attendees_modify"),
            }
        else:
            sql = """
            UPDATE calendar_event_logs
            SET
                updated_by = :updated_by,
                event_id = :event_id,
                summary = :summary,
                description = :description,
                location = :location,
                event_start = :event_start,
                event_end = :event_end,
                event_timezone = :event_timezone,
                attendees = :attendees,
                can_attendees_modify = :can_attendees_modify
            WHERE id = (
                SELECT id FROM (
                    SELECT id
                    FROM calendar_event_logs
                    WHERE event_id = :event_id
                    ORDER BY id DESC
                    LIMIT 1
                ) latest
            )
            """
            params = {
                "updated_by": actor_name,
                "event_id": normalized_event.get("event_id"),
                "summary": normalized_event.get("summary"),
                "description": normalized_event.get("description"),
                "location": normalized_event.get("location"),
                "event_start": normalized_event.get("event_start"),
                "event_end": normalized_event.get("event_end"),
                "event_timezone": normalized_event.get("event_timezone"),
                "attendees": normalized_event.get("attendees"),
                "can_attendees_modify": normalized_event.get("can_attendees_modify"),
            }

        rowcount = self._execute_write(sql, params)
        if rowcount <= 0:
            raise LogPersistenceError("No matching calendar_event_logs row found for update")

    def mark_event_deleted(self, event_id: str) -> None:
        sql = """
        UPDATE calendar_event_logs
        SET park = '1'
        WHERE event_id = :event_id
        """
        self._execute_write(sql, {"event_id": event_id})
