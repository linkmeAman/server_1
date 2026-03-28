"""Business workflows for Employee Events V1."""

from __future__ import annotations

import logging
import re
from datetime import date as date_value, datetime, time as time_value, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.settings import get_settings

from ..dependencies import EmployeeEventsError
from .event_repository import EmployeeEventsRepository
from .google_payload_builder import build_google_event_payload
from .google_sync_repository import EmployeeEventGoogleSyncRepository
from ...google_calendar_v1.services.google_client import GoogleCalendarClient
from ...google_calendar_v1.services.token_manager import GoogleCalendarTokenManager

logger = logging.getLogger(__name__)


class EmployeeEventsService:
    """Coordinates local employee-event writes and Google sync."""

    _LEAVE_STATUS_LABELS: Dict[int, str] = {
        0: "Pending",
        1: "Approved",
        2: "Rejected",
    }
    _LEAVE_REQUEST_TYPE_MAP: Dict[int, Tuple[str, str]] = {
        1: ("Leave", "#EF4865"),
        2: ("Work From Home", "#96C1CC"),
        3: ("Half Day", "#E29082"),
        4: ("Late", "#FF5722"),
        5: ("Punch IN/OUT", "#0EA5E9"),
        6: ("Optional Holiday", "#4167B0"),
        7: ("Supplementary", "#FFC25C"),
        8: ("BREAK", "#14B8A6"),
    }
    _LEAVE_UNKNOWN_REQUEST_TYPE_COLOR = "#9CA3AF"
    _LEAVE_PENDING_COLOR_DELTA = -40
    _LEAVE_HALF_DAY_LIGHTEN_DELTA = 60
    _LEAVE_DEFAULT_START_TIME = time_value(9, 0, 0)
    _LEAVE_DEFAULT_END_TIME = time_value(17, 0, 0)

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
        self._cached_workshift_timezone_name: Optional[str] = None
        self._cached_workshift_timezone: Optional[ZoneInfo] = None

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

    @staticmethod
    def _invalid_workshift_query(
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> EmployeeEventsError:
        return EmployeeEventsError(
            code="EMP_EVENT_INVALID_WORKSHIFT_QUERY",
            message=message,
            status_code=400,
            data=data,
        )

    @staticmethod
    def _workshift_service_misconfigured(
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> EmployeeEventsError:
        return EmployeeEventsError(
            code="EMP_EVENT_SERVICE_MISCONFIGURED",
            message=message,
            status_code=500,
            data=data,
        )

    @staticmethod
    def _invalid_leave_query(
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> EmployeeEventsError:
        return EmployeeEventsError(
            code="EMP_EVENT_INVALID_LEAVE_QUERY",
            message=message,
            status_code=400,
            data=data,
        )

    @staticmethod
    def _invalid_demo_query(
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> EmployeeEventsError:
        return EmployeeEventsError(
            code="EMP_EVENT_INVALID_DEMO_QUERY",
            message=message,
            status_code=400,
            data=data,
        )

    @staticmethod
    def _invalid_batch_query(
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> EmployeeEventsError:
        return EmployeeEventsError(
            code="EMP_EVENT_INVALID_BATCH_QUERY",
            message=message,
            status_code=400,
            data=data,
        )

    @staticmethod
    def _demo_query_failed(
        message: str = "Could not fetch demo events",
        data: Optional[Dict[str, Any]] = None,
    ) -> EmployeeEventsError:
        return EmployeeEventsError(
            code="EMP_EVENT_DEMO_QUERY_FAILED",
            message=message,
            status_code=500,
            data=data,
        )

    @staticmethod
    def _batch_query_failed(
        message: str = "Could not fetch active batches",
        data: Optional[Dict[str, Any]] = None,
    ) -> EmployeeEventsError:
        return EmployeeEventsError(
            code="EMP_EVENT_BATCH_QUERY_FAILED",
            message=message,
            status_code=500,
            data=data,
        )

    @staticmethod
    def _venue_query_failed(
        message: str = "Could not fetch active venues",
        data: Optional[Dict[str, Any]] = None,
    ) -> EmployeeEventsError:
        return EmployeeEventsError(
            code="EMP_EVENT_VENUE_QUERY_FAILED",
            message=message,
            status_code=500,
            data=data,
        )

    @staticmethod
    def _leave_query_failed(
        message: str = "Could not fetch employee leave calendar",
        data: Optional[Dict[str, Any]] = None,
    ) -> EmployeeEventsError:
        return EmployeeEventsError(
            code="EMP_EVENT_LEAVE_QUERY_FAILED",
            message=message,
            status_code=500,
            data=data,
        )

    @staticmethod
    def _invalid_calendar_query(
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> EmployeeEventsError:
        return EmployeeEventsError(
            code="EMP_EVENT_INVALID_CALENDAR_QUERY",
            message=message,
            status_code=400,
            data=data,
        )

    @staticmethod
    def _calendar_query_failed(
        message: str = "Could not fetch calendar events",
        data: Optional[Dict[str, Any]] = None,
    ) -> EmployeeEventsError:
        return EmployeeEventsError(
            code="EMP_EVENT_CALENDAR_QUERY_FAILED",
            message=message,
            status_code=500,
            data=data,
        )

    @staticmethod
    def _normalize_employee_ids(employee_ids: List[Any]) -> List[int]:
        seen: set[int] = set()
        normalized: List[int] = []

        for raw_value in employee_ids:
            if isinstance(raw_value, bool):
                raise EmployeeEventsService._invalid_workshift_query(
                    "employee_ids must contain positive integers",
                    data={"invalid_employee_id": raw_value},
                )

            try:
                employee_id = int(raw_value)
            except Exception as exc:
                raise EmployeeEventsService._invalid_workshift_query(
                    "employee_ids must contain positive integers",
                    data={"invalid_employee_id": raw_value},
                ) from exc

            if employee_id <= 0:
                raise EmployeeEventsService._invalid_workshift_query(
                    "employee_ids must contain positive integers",
                    data={"invalid_employee_id": raw_value},
                )

            if employee_id not in seen:
                seen.add(employee_id)
                normalized.append(employee_id)

        if not normalized:
            raise EmployeeEventsService._invalid_workshift_query(
                "employee_ids must contain at least one unique employee id",
            )

        if len(normalized) > 25:
            raise EmployeeEventsService._invalid_workshift_query(
                "employee_ids may contain at most 25 unique employee ids",
                data={"employee_count": len(normalized)},
            )

        return normalized

    @staticmethod
    def _normalize_leave_employee_ids(employee_ids: List[Any]) -> List[int]:
        seen: set[int] = set()
        normalized: List[int] = []

        for raw_value in employee_ids:
            if isinstance(raw_value, bool):
                raise EmployeeEventsService._invalid_leave_query(
                    "employee_ids must contain positive integers",
                    data={"invalid_employee_id": raw_value},
                )

            try:
                employee_id = int(raw_value)
            except Exception as exc:
                raise EmployeeEventsService._invalid_leave_query(
                    "employee_ids must contain positive integers",
                    data={"invalid_employee_id": raw_value},
                ) from exc

            if employee_id <= 0:
                raise EmployeeEventsService._invalid_leave_query(
                    "employee_ids must contain positive integers",
                    data={"invalid_employee_id": raw_value},
                )

            if employee_id not in seen:
                seen.add(employee_id)
                normalized.append(employee_id)

        if not normalized:
            raise EmployeeEventsService._invalid_leave_query(
                "employee_ids must contain at least one unique employee id",
            )

        if len(normalized) > 25:
            raise EmployeeEventsService._invalid_leave_query(
                "employee_ids may contain at most 25 unique employee ids",
                data={"employee_count": len(normalized)},
            )

        return normalized

    @staticmethod
    def _normalize_demo_employee_ids(employee_ids: List[Any]) -> List[int]:
        seen: set[int] = set()
        normalized: List[int] = []

        for raw_value in employee_ids:
            if isinstance(raw_value, bool):
                raise EmployeeEventsService._invalid_demo_query(
                    "employee_ids must contain positive integers",
                    data={"invalid_employee_id": raw_value},
                )

            try:
                employee_id = int(raw_value)
            except Exception as exc:
                raise EmployeeEventsService._invalid_demo_query(
                    "employee_ids must contain positive integers",
                    data={"invalid_employee_id": raw_value},
                ) from exc

            if employee_id <= 0:
                raise EmployeeEventsService._invalid_demo_query(
                    "employee_ids must contain positive integers",
                    data={"invalid_employee_id": raw_value},
                )

            if employee_id not in seen:
                seen.add(employee_id)
                normalized.append(employee_id)

        if not normalized:
            raise EmployeeEventsService._invalid_demo_query(
                "employee_ids must contain at least one unique employee id",
            )

        if len(normalized) > 25:
            raise EmployeeEventsService._invalid_demo_query(
                "employee_ids may contain at most 25 unique employee ids",
                data={"employee_count": len(normalized)},
            )

        return normalized

    @classmethod
    def _parse_workshift_query_date(cls, raw_value: Any, field_name: str) -> date_value:
        text_value = str(raw_value or "").strip()
        try:
            return datetime.strptime(text_value, "%Y-%m-%d").date()
        except Exception as exc:
            raise cls._invalid_workshift_query(
                f"{field_name} must be in YYYY-MM-DD format",
                data={"field": field_name, "value": raw_value},
            ) from exc

    @classmethod
    def _parse_leave_query_date(cls, raw_value: Any, field_name: str) -> date_value:
        text_value = str(raw_value or "").strip()
        try:
            return datetime.strptime(text_value, "%Y-%m-%d").date()
        except Exception as exc:
            raise cls._invalid_leave_query(
                f"{field_name} must be in YYYY-MM-DD format",
                data={"field": field_name, "value": raw_value},
            ) from exc

    @classmethod
    def _parse_demo_query_date(cls, raw_value: Any, field_name: str) -> date_value:
        text_value = str(raw_value or "").strip()
        try:
            return datetime.strptime(text_value, "%Y-%m-%d").date()
        except Exception as exc:
            raise cls._invalid_demo_query(
                f"{field_name} must be in YYYY-MM-DD format",
                data={"field": field_name, "value": raw_value},
            ) from exc

    @classmethod
    def _parse_calendar_query_date(cls, raw_value: Any, field_name: str) -> date_value:
        text_value = str(raw_value or "").strip()
        try:
            return datetime.strptime(text_value, "%Y-%m-%d").date()
        except Exception as exc:
            raise cls._invalid_calendar_query(
                f"{field_name} must be in YYYY-MM-DD format",
                data={"field": field_name, "value": raw_value},
            ) from exc

    @staticmethod
    def _parse_calendar_event_datetime(raw_value: Any) -> Optional[datetime]:
        if isinstance(raw_value, datetime):
            parsed = raw_value
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed

        if isinstance(raw_value, date_value):
            return datetime.combine(raw_value, time_value(0, 0, 0))

        text_value = str(raw_value or "").strip()
        if not text_value:
            return None

        candidate_formats = (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M%z",
            "%B %d, %Y at %I:%M %p",
            "%b %d, %Y at %I:%M %p",
        )
        for fmt in candidate_formats:
            try:
                parsed = datetime.strptime(text_value, fmt)
                if parsed.tzinfo is not None:
                    parsed = parsed.astimezone().replace(tzinfo=None)
                return parsed
            except Exception:
                continue

        try:
            parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
        except Exception:
            return None

    @classmethod
    def _parse_calendar_event_start(cls, raw_value: Any) -> Optional[datetime]:
        return cls._parse_calendar_event_datetime(raw_value)

    @staticmethod
    def _format_calendar_datetime(value: datetime) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _to_title(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _employee_event_title(cls, event: Dict[str, Any]) -> str:
        for key in ("title", "category", "description", "type"):
            candidate = cls._to_title(event.get(key))
            if candidate:
                return candidate
        event_id = cls._as_int(event.get("id"), default=0)
        if event_id > 0:
            return f"Employee Event {event_id}"
        return "Employee Event"

    @classmethod
    def _trainer_event_title(cls, row: Dict[str, Any]) -> str:
        for key in ("title", "display_name", "batch", "summary", "batch_name"):
            candidate = cls._to_title(row.get(key))
            if candidate:
                return candidate
        return "Trainer Batch"

    @classmethod
    def _employee_event_start_end(
        cls,
        event: Dict[str, Any],
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        date_text = cls._to_title(event.get("date"))
        start_time_text = cls._to_title(event.get("start_time"))
        end_time_text = cls._to_title(event.get("end_time"))
        if not date_text or not start_time_text or not end_time_text:
            return None, None

        start_dt = cls._parse_calendar_event_datetime(f"{date_text} {start_time_text}")
        end_dt = cls._parse_calendar_event_datetime(f"{date_text} {end_time_text}")
        return start_dt, end_dt

    @classmethod
    def _parse_calendar_date_value(cls, raw_value: Any) -> Optional[date_value]:
        if isinstance(raw_value, datetime):
            return raw_value.date()
        if isinstance(raw_value, date_value):
            return raw_value
        parsed = cls._parse_calendar_event_datetime(raw_value)
        if parsed is None:
            return None
        return parsed.date()

    @staticmethod
    def _parse_calendar_time_value(raw_value: Any) -> time_value:
        if isinstance(raw_value, time_value):
            return raw_value.replace(microsecond=0)

        text_value = str(raw_value or "").strip()
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(text_value, fmt).time().replace(microsecond=0)
            except Exception:
                continue
        return time_value(0, 0, 0)

    def _localize_batch_occurrence(
        self,
        occurrence_date: date_value,
        time_value_obj: time_value,
        timezone_id: Optional[str],
    ) -> datetime:
        """Create timezone-aware datetime from date + time in batch timezone.
        
        Args:
            occurrence_date: The date for the occurrence
            time_value_obj: The time value (start or end time)
            timezone_id: The timezone identifier from batch row (e.g., "Asia/Kolkata")
            
        Returns:
            Timezone-aware datetime object localized to batch timezone
        """
        tz_name = str(timezone_id or "").strip()
        if not tz_name:
            # Fallback to configured default timezone
            tz_name, tz = self._workshift_timezone()
        else:
            try:
                tz = ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                logger.warning(
                    "Invalid timezone_id=%r for batch, falling back to EMP_EVENT_TIMEZONE",
                    timezone_id,
                )
                tz_name, tz = self._workshift_timezone()
        
        naive_dt = datetime.combine(occurrence_date, time_value_obj)
        return naive_dt.replace(tzinfo=tz)

    @staticmethod
    def _effective_calendar_range(
        from_date_value: Optional[date_value],
        to_date_value: Optional[date_value],
    ) -> Tuple[date_value, date_value]:
        if from_date_value is None and to_date_value is None:
            today = date_value.today()
            return today, today + timedelta(days=90)
        if from_date_value is None and to_date_value is not None:
            return to_date_value - timedelta(days=90), to_date_value
        if from_date_value is not None and to_date_value is None:
            return from_date_value, from_date_value + timedelta(days=90)
        return from_date_value, to_date_value

    @classmethod
    def _decode_day_code(cls, raw_value: Any) -> List[int]:
        raw_code, parsed_days, _warnings = cls._decode_week_off_code(raw_value)
        if not raw_code:
            return []
        return parsed_days

    @classmethod
    def _normalize_leave_filter_values(
        cls,
        raw_values: Optional[List[Any]],
        *,
        field_name: str,
        min_value: int,
    ) -> List[int]:
        if raw_values is None:
            return []
        if not isinstance(raw_values, list):
            raise cls._invalid_leave_query(
                f"{field_name} must be an array of integers",
                data={"field": field_name},
            )

        seen: set[int] = set()
        normalized: List[int] = []
        for raw_value in raw_values:
            if isinstance(raw_value, bool):
                raise cls._invalid_leave_query(
                    f"{field_name} must contain integers >= {min_value}",
                    data={"field": field_name, "invalid_value": raw_value},
                )

            try:
                normalized_value = int(raw_value)
            except Exception as exc:
                raise cls._invalid_leave_query(
                    f"{field_name} must contain integers >= {min_value}",
                    data={"field": field_name, "invalid_value": raw_value},
                ) from exc

            if normalized_value < min_value:
                raise cls._invalid_leave_query(
                    f"{field_name} must contain integers >= {min_value}",
                    data={"field": field_name, "invalid_value": raw_value},
                )

            if normalized_value not in seen:
                seen.add(normalized_value)
                normalized.append(normalized_value)

        return normalized

    @classmethod
    def _normalize_demo_filter_values(
        cls,
        raw_values: Optional[List[Any]],
        *,
        field_name: str,
        min_value: int,
    ) -> List[int]:
        if raw_values is None:
            return []
        if not isinstance(raw_values, list):
            raise cls._invalid_demo_query(
                f"{field_name} must be an array of integers",
                data={"field": field_name},
            )

        seen: set[int] = set()
        normalized: List[int] = []
        for raw_value in raw_values:
            if isinstance(raw_value, bool):
                raise cls._invalid_demo_query(
                    f"{field_name} must contain integers >= {min_value}",
                    data={"field": field_name, "invalid_value": raw_value},
                )

            try:
                normalized_value = int(raw_value)
            except Exception as exc:
                raise cls._invalid_demo_query(
                    f"{field_name} must contain integers >= {min_value}",
                    data={"field": field_name, "invalid_value": raw_value},
                ) from exc

            if normalized_value < min_value:
                raise cls._invalid_demo_query(
                    f"{field_name} must contain integers >= {min_value}",
                    data={"field": field_name, "invalid_value": raw_value},
                )

            if normalized_value not in seen:
                seen.add(normalized_value)
                normalized.append(normalized_value)

        return normalized

    @classmethod
    def _normalize_batch_venue_ids(cls, venue_ids: List[Any]) -> List[int]:
        if not isinstance(venue_ids, list):
            raise cls._invalid_batch_query(
                "venue_ids must be an array of positive integers",
                data={"field": "venue_ids"},
            )

        seen: set[int] = set()
        normalized: List[int] = []

        for raw_value in venue_ids:
            if isinstance(raw_value, bool):
                raise cls._invalid_batch_query(
                    "venue_ids must contain positive integers",
                    data={"field": "venue_ids", "invalid_venue_id": raw_value},
                )

            try:
                venue_id = int(raw_value)
            except Exception as exc:
                raise cls._invalid_batch_query(
                    "venue_ids must contain positive integers",
                    data={"field": "venue_ids", "invalid_venue_id": raw_value},
                ) from exc

            if venue_id <= 0:
                raise cls._invalid_batch_query(
                    "venue_ids must contain positive integers",
                    data={"field": "venue_ids", "invalid_venue_id": raw_value},
                )

            if venue_id not in seen:
                seen.add(venue_id)
                normalized.append(venue_id)

        if not normalized:
            raise cls._invalid_batch_query(
                "venue_ids must contain at least one unique venue id",
            )

        if len(normalized) > 25:
            raise cls._invalid_batch_query(
                "venue_ids may contain at most 25 unique venue ids",
                data={"venue_count": len(normalized)},
            )

        return normalized

    @staticmethod
    def _normalize_employee_name(value: Any) -> Optional[str]:
        if value is None:
            return None
        text_value = str(value).strip()
        if not text_value:
            return None
        return text_value

    @staticmethod
    def _coerce_code_with_text(raw_value: Any, *, fallback: int = -1) -> Tuple[int, str]:
        if raw_value is None:
            return fallback, str(fallback)

        if isinstance(raw_value, bool):
            value = int(raw_value)
            return value, str(value)

        try:
            value = int(raw_value)
            return value, str(value)
        except Exception:
            text_value = str(raw_value).strip()
            if text_value:
                return fallback, text_value
            return fallback, str(fallback)

    @classmethod
    def _status_label_and_warning(cls, status_code: int, status_text: str) -> Tuple[str, Optional[str]]:
        label = cls._LEAVE_STATUS_LABELS.get(status_code)
        if label is not None:
            return label, None
        return f"Unknown({status_text})", f"unknown_status:{status_text}"

    @classmethod
    def _request_type_details(
        cls,
        request_type_code: int,
        request_type_text: str,
    ) -> Tuple[str, str, Optional[str]]:
        mapping = cls._LEAVE_REQUEST_TYPE_MAP.get(request_type_code)
        if mapping is None:
            return (
                "Unknown",
                cls._LEAVE_UNKNOWN_REQUEST_TYPE_COLOR,
                f"unknown_request_type:{request_type_text}",
            )

        request_type_name, color = mapping
        if request_type_code == 3:
            color = cls._adjust_hex_color(color, cls._LEAVE_HALF_DAY_LIGHTEN_DELTA)
        return request_type_name, color, None

    @classmethod
    def _adjust_hex_color(cls, hex_color: str, delta: int) -> str:
        normalized = str(hex_color or "").strip().lstrip("#")
        if len(normalized) != 6:
            normalized = cls._LEAVE_UNKNOWN_REQUEST_TYPE_COLOR.lstrip("#")

        try:
            red = int(normalized[0:2], 16)
            green = int(normalized[2:4], 16)
            blue = int(normalized[4:6], 16)
        except Exception:
            fallback = cls._LEAVE_UNKNOWN_REQUEST_TYPE_COLOR.lstrip("#")
            red = int(fallback[0:2], 16)
            green = int(fallback[2:4], 16)
            blue = int(fallback[4:6], 16)

        red = max(0, min(255, red + int(delta)))
        green = max(0, min(255, green + int(delta)))
        blue = max(0, min(255, blue + int(delta)))
        return f"#{red:02X}{green:02X}{blue:02X}"

    @classmethod
    def _normalize_leave_datetime(
        cls,
        raw_value: Any,
        *,
        timezone: ZoneInfo,
        default_time: time_value,
    ) -> Optional[datetime]:
        if raw_value is None:
            return None

        if isinstance(raw_value, datetime):
            parsed_dt = raw_value
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=timezone)
            else:
                parsed_dt = parsed_dt.astimezone(timezone)
            return parsed_dt.replace(microsecond=0)

        if isinstance(raw_value, date_value):
            return datetime.combine(raw_value, default_time, tzinfo=timezone)

        raw_text = str(raw_value).strip()
        if not raw_text:
            return None

        try:
            parsed_date = datetime.strptime(raw_text, "%Y-%m-%d").date()
            return datetime.combine(parsed_date, default_time, tzinfo=timezone)
        except ValueError:
            pass

        candidate = raw_text[:-1] + "+00:00" if raw_text.endswith("Z") else raw_text

        parsed_dt: Optional[datetime] = None
        try:
            parsed_dt = datetime.fromisoformat(candidate)
        except ValueError:
            for format_string in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M",
            ):
                try:
                    parsed_dt = datetime.strptime(raw_text, format_string)
                    break
                except ValueError:
                    continue

        if parsed_dt is None:
            return None

        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=timezone)
        else:
            parsed_dt = parsed_dt.astimezone(timezone)

        return parsed_dt.replace(microsecond=0)

    @staticmethod
    def _append_warning(
        warnings: List[str],
        warning_lookup: set[str],
        warning: Optional[str],
    ) -> None:
        if warning is None:
            return
        if warning in warning_lookup:
            return
        warnings.append(warning)
        warning_lookup.add(warning)

    @staticmethod
    def _inclusive_dates(from_date: date_value, to_date: date_value) -> List[date_value]:
        return [from_date + timedelta(days=offset) for offset in range((to_date - from_date).days + 1)]

    def _workshift_timezone(self) -> Tuple[str, ZoneInfo]:
        if self._cached_workshift_timezone_name and self._cached_workshift_timezone is not None:
            return self._cached_workshift_timezone_name, self._cached_workshift_timezone

        settings = get_settings()
        timezone_name = str(getattr(settings, "EMP_EVENT_TIMEZONE", "") or "").strip()
        if not timezone_name:
            raise self._workshift_service_misconfigured("EMP_EVENT_TIMEZONE is not configured")

        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise self._workshift_service_misconfigured(
                "EMP_EVENT_TIMEZONE is invalid",
                data={"timezone": timezone_name},
            ) from exc

        self._cached_workshift_timezone_name = timezone_name
        self._cached_workshift_timezone = timezone
        return timezone_name, timezone

    @staticmethod
    def _normalize_workshift_id(value: Any) -> Tuple[Optional[int], Optional[str]]:
        if value is None:
            return None, "missing_workshift_id"

        candidate: Any = value
        if isinstance(candidate, str):
            candidate = candidate.strip()
            if not candidate:
                return None, "missing_workshift_id"

        try:
            normalized = int(candidate)
        except Exception:
            return None, "missing_workshift_id"

        if normalized < 0:
            return None, "missing_workshift_id"

        return normalized, None

    @staticmethod
    def _normalize_workshift_time(
        value: Any,
        *,
        missing_issue: str,
        invalid_issue: str,
    ) -> Tuple[Optional[str], Optional[time_value], Optional[str]]:
        if value is None:
            return None, None, missing_issue

        if isinstance(value, time_value):
            parsed = value.replace(microsecond=0)
            return parsed.strftime("%H:%M:%S"), parsed, None

        raw_text = str(value).strip()
        if not raw_text:
            return None, None, missing_issue

        for format_string in ("%H:%M:%S", "%H:%M"):
            try:
                parsed = datetime.strptime(raw_text, format_string).time().replace(microsecond=0)
                return parsed.strftime("%H:%M:%S"), parsed, None
            except ValueError:
                continue

        return raw_text, None, invalid_issue

    @staticmethod
    def _decode_week_off_code(value: Any) -> Tuple[str, List[int], List[str]]:
        raw_code = "" if value is None else str(value).strip()
        if not raw_code:
            return "", [], []

        tokens = [token for token in re.split(r"[\s,]+", raw_code) if token]
        decoded_days: set[int] = set()
        warnings: List[str] = []
        seen_warnings: set[str] = set()

        for token in tokens:
            try:
                day_value = int(token)
            except Exception:
                warning = f"invalid_week_off_token:{token}"
                if warning not in seen_warnings:
                    warnings.append(warning)
                    seen_warnings.add(warning)
                continue

            if 0 <= day_value <= 6:
                decoded_days.add(day_value)
                continue

            warning = f"invalid_week_off_token:{token}"
            if warning not in seen_warnings:
                warnings.append(warning)
                seen_warnings.add(warning)

        return raw_code, sorted(decoded_days), warnings

    @staticmethod
    def _weekday_sunday_zero(current_date: date_value) -> int:
        return (current_date.weekday() + 1) % 7

    @classmethod
    def _build_calendar_days(
        cls,
        *,
        calendar_dates: List[date_value],
        timezone: ZoneInfo,
        workshift_id: int,
        week_off_days: List[int],
        in_time: time_value,
        out_time: time_value,
    ) -> List[Dict[str, Any]]:
        calendar_days: List[Dict[str, Any]] = []
        week_off_lookup = set(week_off_days)
        is_overnight = out_time <= in_time

        for current_date in calendar_dates:
            weekday = cls._weekday_sunday_zero(current_date)
            is_week_off = weekday in week_off_lookup

            if is_week_off:
                calendar_days.append(
                    {
                        "date": current_date.isoformat(),
                        "weekday": weekday,
                        "is_week_off": True,
                        "is_overnight": False,
                        "shift_start": None,
                        "shift_end": None,
                        "workshift_id": workshift_id,
                    }
                )
                continue

            shift_start = datetime.combine(current_date, in_time, tzinfo=timezone)
            shift_end_date = current_date + timedelta(days=1) if is_overnight else current_date
            shift_end = datetime.combine(shift_end_date, out_time, tzinfo=timezone)

            calendar_days.append(
                {
                    "date": current_date.isoformat(),
                    "weekday": weekday,
                    "is_week_off": False,
                    "is_overnight": is_overnight,
                    "shift_start": shift_start.isoformat(),
                    "shift_end": shift_end.isoformat(),
                    "workshift_id": workshift_id,
                }
            )

        return calendar_days

    @classmethod
    def _build_calendar_days_per_day(
        cls,
        *,
        calendar_dates: List[date_value],
        timezone: ZoneInfo,
        workshift_id: int,
        day_schedule: Dict[int, Tuple[time_value, time_value]],
    ) -> List[Dict[str, Any]]:
        calendar_days: List[Dict[str, Any]] = []

        for current_date in calendar_dates:
            weekday = cls._weekday_sunday_zero(current_date)

            if weekday not in day_schedule:
                calendar_days.append(
                    {
                        "date": current_date.isoformat(),
                        "weekday": weekday,
                        "is_week_off": True,
                        "is_overnight": False,
                        "shift_start": None,
                        "shift_end": None,
                        "workshift_id": workshift_id,
                    }
                )
                continue

            in_time, out_time = day_schedule[weekday]
            is_overnight = out_time <= in_time
            shift_start = datetime.combine(current_date, in_time, tzinfo=timezone)
            shift_end_date = current_date + timedelta(days=1) if is_overnight else current_date
            shift_end = datetime.combine(shift_end_date, out_time, tzinfo=timezone)

            calendar_days.append(
                {
                    "date": current_date.isoformat(),
                    "weekday": weekday,
                    "is_week_off": False,
                    "is_overnight": is_overnight,
                    "shift_start": shift_start.isoformat(),
                    "shift_end": shift_end.isoformat(),
                    "workshift_id": workshift_id,
                }
            )

        return calendar_days

    def _build_workshift_batch_result(
        self,
        *,
        employee_id: int,
        row: Optional[Dict[str, Any]],
        calendar_dates: List[date_value],
        timezone: ZoneInfo,
        workshift_day_schedule: Optional[Dict[int, Tuple[time_value, time_value]]] = None,
    ) -> Dict[str, Any]:
        if row is None:
            return {
                "employee_id": int(employee_id),
                "employee_name": None,
                "result_status": "not_found",
                "warnings": [],
                "workshift": None,
                "calendar_days": [],
                "day_count": 0,
            }

        employee_name = row.get("employee_name")
        if employee_name is not None:
            employee_name = str(employee_name).strip()

        normalized_workshift_id, workshift_issue = self._normalize_workshift_id(row.get("workshift_id"))

        # Per-day schedule path: workshift_id > 0 uses workshift_day table rows
        if normalized_workshift_id is not None and normalized_workshift_id > 0:
            if not workshift_day_schedule:
                workshift = {
                    "workshift_id": normalized_workshift_id,
                    "workshift_in_time": None,
                    "workshift_out_time": None,
                    "week_off_code": None,
                    "week_off_days": list(range(7)),
                    "day_schedule": [],
                    "is_configured": False,
                    "configuration_issues": ["no_days_configured"],
                }
                return {
                    "employee_id": int(employee_id),
                    "employee_name": employee_name,
                    "result_status": "unconfigured",
                    "warnings": [],
                    "workshift": workshift,
                    "calendar_days": [],
                    "day_count": 0,
                }

            week_off_days = sorted(set(range(7)) - set(workshift_day_schedule.keys()))
            day_schedule_list = [
                {
                    "day_code": dc,
                    "start_time": s.strftime("%H:%M:%S"),
                    "end_time": e.strftime("%H:%M:%S"),
                }
                for dc, (s, e) in sorted(workshift_day_schedule.items())
            ]
            workshift = {
                "workshift_id": normalized_workshift_id,
                "workshift_in_time": None,
                "workshift_out_time": None,
                "week_off_code": None,
                "week_off_days": week_off_days,
                "day_schedule": day_schedule_list,
                "is_configured": True,
                "configuration_issues": [],
            }
            calendar_days = self._build_calendar_days_per_day(
                calendar_dates=calendar_dates,
                timezone=timezone,
                workshift_id=normalized_workshift_id,
                day_schedule=workshift_day_schedule,
            )
            return {
                "employee_id": int(employee_id),
                "employee_name": employee_name,
                "result_status": "configured",
                "warnings": [],
                "workshift": workshift,
                "calendar_days": calendar_days,
                "day_count": len(calendar_days),
            }

        # Legacy path: workshift_id == 0 or null/invalid — use single in/out time + week_off_code
        in_time_text, in_time, in_time_issue = self._normalize_workshift_time(
            row.get("workshift_in_time"),
            missing_issue="missing_in_time",
            invalid_issue="invalid_in_time",
        )
        out_time_text, out_time, out_time_issue = self._normalize_workshift_time(
            row.get("workshift_out_time"),
            missing_issue="missing_out_time",
            invalid_issue="invalid_out_time",
        )
        raw_week_off_code, week_off_days, warnings = self._decode_week_off_code(row.get("week_off_code"))

        configuration_issues = [
            issue
            for issue in (workshift_issue, in_time_issue, out_time_issue)
            if issue is not None
        ]
        is_configured = not configuration_issues
        workshift = {
            "workshift_id": normalized_workshift_id,
            "workshift_in_time": in_time_text,
            "workshift_out_time": out_time_text,
            "week_off_code": raw_week_off_code,
            "week_off_days": week_off_days,
            "is_configured": is_configured,
            "configuration_issues": configuration_issues,
        }

        if not is_configured:
            return {
                "employee_id": int(employee_id),
                "employee_name": employee_name,
                "result_status": "unconfigured",
                "warnings": warnings,
                "workshift": workshift,
                "calendar_days": [],
                "day_count": 0,
            }

        calendar_days = self._build_calendar_days(
            calendar_dates=calendar_dates,
            timezone=timezone,
            workshift_id=int(normalized_workshift_id),
            week_off_days=week_off_days,
            in_time=in_time,
            out_time=out_time,
        )
        return {
            "employee_id": int(employee_id),
            "employee_name": employee_name,
            "result_status": "configured",
            "warnings": warnings,
            "workshift": workshift,
            "calendar_days": calendar_days,
            "day_count": len(calendar_days),
        }

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

    def get_active_venues(self) -> Dict[str, Any]:
        try:
            venues = self.event_repository.list_active_venues()
            return {
                "venues": venues,
                "total_count": len(venues),
            }
        except EmployeeEventsError:
            raise
        except Exception as exc:
            raise self._venue_query_failed(
                message=f"Unexpected error fetching active venues: {exc}",
            ) from exc

    def get_active_batches_by_venue(self, venue_ids: List[Any]) -> Dict[str, Any]:
        try:
            normalized_venue_ids = self._normalize_batch_venue_ids(venue_ids)
            batches = self.event_repository.list_active_batches_by_venue_ids(normalized_venue_ids)
            return {
                "venue_ids": normalized_venue_ids,
                "total_count": len(batches),
                "batches": batches,
            }
        except EmployeeEventsError:
            raise
        except Exception as exc:
            raise self._batch_query_failed(
                message=f"Unexpected error fetching active batches: {exc}",
            ) from exc

    def get_employee_workshift_calendar_batch(
        self,
        employee_ids: List[Any],
        from_date: str,
        to_date: str,
    ) -> Dict[str, Any]:
        normalized_employee_ids = self._normalize_employee_ids(employee_ids)
        from_date_value = self._parse_workshift_query_date(from_date, "from_date")
        to_date_value = self._parse_workshift_query_date(to_date, "to_date")

        if from_date_value > to_date_value:
            raise self._invalid_workshift_query(
                "from_date must be less than or equal to to_date",
            )

        range_day_count = (to_date_value - from_date_value).days + 1
        if range_day_count > 62:
            raise self._invalid_workshift_query(
                "Date range may not exceed 62 days",
                data={"range_day_count": range_day_count},
            )

        timezone_name, timezone = self._workshift_timezone()
        calendar_dates = self._inclusive_dates(from_date_value, to_date_value)
        repository_rows = self.event_repository.get_employee_workshifts(normalized_employee_ids)
        rows_by_employee: Dict[int, Dict[str, Any]] = {}
        for row in repository_rows:
            employee_id = self._as_int(row.get("employee_id"))
            if employee_id > 0 and employee_id not in rows_by_employee:
                rows_by_employee[employee_id] = dict(row)

        # Batch-fetch per-day schedule for all distinct workshift_ids > 0
        distinct_workshift_ids = list({
            self._as_int(r.get("workshift_id"))
            for r in rows_by_employee.values()
            if self._as_int(r.get("workshift_id")) > 0
        })
        workshift_day_map: Dict[int, Dict[int, Tuple[time_value, time_value]]] = {}
        if distinct_workshift_ids:
            raw_day_rows = self.event_repository.get_workshift_day_rows(distinct_workshift_ids)
            for day_row in raw_day_rows:
                ws_id = self._as_int(day_row.get("workshift_id"))
                dc = day_row.get("day_code")
                if ws_id <= 0 or dc is None:
                    continue
                try:
                    dc_int = int(dc)
                except Exception:
                    continue
                if not (0 <= dc_int <= 6):
                    continue
                _, st, st_issue = self._normalize_workshift_time(
                    day_row.get("start_time"),
                    missing_issue="missing_start_time",
                    invalid_issue="invalid_start_time",
                )
                _, et, et_issue = self._normalize_workshift_time(
                    day_row.get("end_time"),
                    missing_issue="missing_end_time",
                    invalid_issue="invalid_end_time",
                )
                if st_issue or et_issue:
                    continue
                if ws_id not in workshift_day_map:
                    workshift_day_map[ws_id] = {}
                workshift_day_map[ws_id][dc_int] = (st, et)

        employees = []
        for employee_id in normalized_employee_ids:
            emp_row = rows_by_employee.get(employee_id)
            ws_id = self._as_int(emp_row.get("workshift_id")) if emp_row is not None else 0
            ws_schedule = workshift_day_map.get(ws_id) if ws_id > 0 else None
            employees.append(
                self._build_workshift_batch_result(
                    employee_id=employee_id,
                    row=emp_row,
                    calendar_dates=calendar_dates,
                    timezone=timezone,
                    workshift_day_schedule=ws_schedule,
                )
            )

        return {
            "timezone": timezone_name,
            "from_date": from_date_value.isoformat(),
            "to_date": to_date_value.isoformat(),
            "range_day_count": range_day_count,
            "employee_count": len(normalized_employee_ids),
            "matched_count": len(rows_by_employee),
            "employees": employees,
        }

    def get_employee_leave_calendar_batch(
        self,
        employee_ids: List[Any],
        from_date: str,
        to_date: str,
        statuses: Optional[List[Any]] = None,
        request_types: Optional[List[Any]] = None,
        department_ids: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        try:
            normalized_employee_ids = self._normalize_leave_employee_ids(employee_ids)
            from_date_value = self._parse_leave_query_date(from_date, "from_date")
            to_date_value = self._parse_leave_query_date(to_date, "to_date")

            if from_date_value > to_date_value:
                raise self._invalid_leave_query(
                    "from_date must be less than or equal to to_date",
                )

            range_day_count = (to_date_value - from_date_value).days + 1
            if range_day_count > 62:
                raise self._invalid_leave_query(
                    "Date range may not exceed 62 days",
                    data={"range_day_count": range_day_count},
                )

            normalized_statuses = self._normalize_leave_filter_values(
                statuses,
                field_name="statuses",
                min_value=0,
            )
            normalized_request_types = self._normalize_leave_filter_values(
                request_types,
                field_name="request_types",
                min_value=1,
            )
            normalized_department_ids = self._normalize_leave_filter_values(
                department_ids,
                field_name="department_ids",
                min_value=1,
            )

            timezone_name, timezone = self._workshift_timezone()
            active_rows = self.event_repository.get_active_employees(normalized_employee_ids)
            active_by_employee: Dict[int, Dict[str, Any]] = {}
            for row in active_rows:
                employee_id = self._as_int(row.get("employee_id"))
                if employee_id <= 0 or employee_id in active_by_employee:
                    continue
                active_by_employee[employee_id] = {
                    "employee_id": employee_id,
                    "employee_name": self._normalize_employee_name(row.get("employee_name")),
                    "department_id": (
                        None
                        if row.get("department_id") is None
                        else self._as_int(row.get("department_id"))
                    ),
                }

            active_employee_ids = [
                employee_id
                for employee_id in normalized_employee_ids
                if employee_id in active_by_employee
            ]
            leave_rows = self.event_repository.get_employee_leave_requests(
                employee_ids=active_employee_ids,
                from_date=from_date_value.isoformat(),
                to_date=to_date_value.isoformat(),
                statuses=normalized_statuses or None,
                request_types=normalized_request_types or None,
                department_ids=normalized_department_ids or None,
            )

            state_by_employee: Dict[int, Dict[str, Any]] = {}
            for employee_id in normalized_employee_ids:
                employee_row = active_by_employee.get(employee_id)
                state_by_employee[employee_id] = {
                    "employee_name": (
                        None
                        if employee_row is None
                        else self._normalize_employee_name(employee_row.get("employee_name"))
                    ),
                    "warnings": [],
                    "warning_lookup": set(),
                    "leave_events": [],
                }

            for row in leave_rows:
                employee_id = self._as_int(row.get("employee_id"))
                state = state_by_employee.get(employee_id)
                if state is None:
                    continue

                leave_request_id = self._as_int(row.get("leave_request_id"), default=0)
                warning_leave_id = leave_request_id if leave_request_id > 0 else "unknown"

                status_code, status_text = self._coerce_code_with_text(row.get("status"))
                status_label, status_warning = self._status_label_and_warning(status_code, status_text)
                self._append_warning(state["warnings"], state["warning_lookup"], status_warning)

                request_type_code, request_type_text = self._coerce_code_with_text(
                    row.get("request_type")
                )
                request_type_name, color, request_type_warning = self._request_type_details(
                    request_type_code,
                    request_type_text,
                )
                self._append_warning(
                    state["warnings"],
                    state["warning_lookup"],
                    request_type_warning,
                )

                start_value = self._normalize_leave_datetime(
                    row.get("start_date"),
                    timezone=timezone,
                    default_time=self._LEAVE_DEFAULT_START_TIME,
                )
                end_value = self._normalize_leave_datetime(
                    row.get("end_date"),
                    timezone=timezone,
                    default_time=self._LEAVE_DEFAULT_END_TIME,
                )
                if start_value is None or end_value is None or start_value > end_value:
                    self._append_warning(
                        state["warnings"],
                        state["warning_lookup"],
                        f"invalid_leave_datetime:{warning_leave_id}",
                    )
                    continue

                if status_code == 0:
                    color = self._adjust_hex_color(color, self._LEAVE_PENDING_COLOR_DELTA)

                department_id = row.get("department_id")
                if department_id is None:
                    department_id = (active_by_employee.get(employee_id) or {}).get("department_id")
                elif not isinstance(department_id, bool):
                    try:
                        department_id = int(department_id)
                    except Exception:
                        department_id = None
                else:
                    department_id = None

                event_payload = {
                    "leave_request_id": leave_request_id,
                    "employee_id": employee_id,
                    "employee_name": state["employee_name"],
                    "department_id": department_id,
                    "start": start_value.isoformat(),
                    "end": end_value.isoformat(),
                    "status": status_code,
                    "status_label": status_label,
                    "request_type": request_type_code,
                    "request_type_name": request_type_name,
                    "title": request_type_name,
                    "color": color,
                    "allDay": False,
                    "module_id": 80,
                }
                state["leave_events"].append(event_payload)

            employees: List[Dict[str, Any]] = []
            for employee_id in normalized_employee_ids:
                employee_row = active_by_employee.get(employee_id)
                state = state_by_employee.get(employee_id, {})
                leave_events = list(state.get("leave_events") or [])
                leave_events.sort(key=lambda event: (event.get("start") or "", event.get("leave_request_id") or 0))

                if employee_row is None:
                    result_status = "not_found"
                    leave_events = []
                else:
                    result_status = "has_events" if leave_events else "no_events"

                employees.append(
                    {
                        "employee_id": int(employee_id),
                        "employee_name": state.get("employee_name"),
                        "result_status": result_status,
                        "warnings": list(state.get("warnings") or []),
                        "leave_events": leave_events,
                        "leave_event_count": len(leave_events),
                    }
                )

            return {
                "timezone": timezone_name,
                "from_date": from_date_value.isoformat(),
                "to_date": to_date_value.isoformat(),
                "range_day_count": range_day_count,
                "employee_count": len(normalized_employee_ids),
                "matched_count": len(active_by_employee),
                "filters_applied": {
                    "statuses": normalized_statuses,
                    "request_types": normalized_request_types,
                    "department_ids": normalized_department_ids,
                },
                "employees": employees,
            }
        except EmployeeEventsError as exc:
            if exc.code in {"EMP_EVENT_DB_UNAVAILABLE", "EMP_EVENT_INVALID_LEAVE_QUERY"}:
                raise
            raise self._leave_query_failed(
                data={"reason": exc.code, "message": exc.message},
            ) from exc
        except Exception as exc:
            raise self._leave_query_failed(
                data={
                    "reason": "unexpected_exception",
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                }
            ) from exc

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

    @staticmethod
    def _normalize_binary_flag(value: Any) -> int:
        try:
            return 1 if int(value) != 0 else 0
        except Exception:
            return 0

    @staticmethod
    def _derive_batch_type(
        batch_status: Any,
        is_scheduled: int,
        is_original: int,
    ) -> str:
        normalized_status = str(batch_status or "").strip().lower()
        if normalized_status in {"original", "scheduled", "prescheduled"}:
            return normalized_status
        if is_scheduled == 1:
            return "scheduled"
        if is_original == 1:
            return "original"
        return "prescheduled"

    def get_trainer_calendar_events(
        self,
        contact_id: int,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            normalized_contact_id = self._as_int(contact_id)
            if normalized_contact_id <= 0:
                raise self._invalid_calendar_query(
                    "contact_id must be a positive integer",
                    data={"field": "contact_id", "value": contact_id},
                )

            from_date_value: Optional[date_value] = None
            if from_date is not None:
                from_date_value = self._parse_calendar_query_date(from_date, "from_date")

            to_date_value: Optional[date_value] = None
            if to_date is not None:
                to_date_value = self._parse_calendar_query_date(to_date, "to_date")

            if from_date_value and to_date_value and from_date_value > to_date_value:
                raise self._invalid_calendar_query(
                    "from_date must be less than or equal to to_date",
                )

            effective_from_date, effective_to_date = self._effective_calendar_range(
                from_date_value=from_date_value,
                to_date_value=to_date_value,
            )

            employee_result = self.list_events(
                from_date=effective_from_date.isoformat(),
                to_date=effective_to_date.isoformat(),
                contact_id=normalized_contact_id,
                status=None,
                park=None,
                include_parked=False,
            )
            trainer_rows = self.event_repository.list_trainer_calendar_events(
                contact_id=normalized_contact_id,
                from_date=effective_from_date.isoformat(),
                to_date=effective_to_date.isoformat(),
            )

            unified_events: List[Dict[str, Any]] = []

            for employee_event in employee_result.get("events") or []:
                employee_raw = dict(employee_event)
                employee_id = self._as_int(employee_raw.get("id"), default=0)
                if employee_id <= 0:
                    logger.warning(
                        "Skipping employee_event row with invalid id for contact_id=%s",
                        normalized_contact_id,
                    )
                    continue

                start_dt, end_dt = self._employee_event_start_end(employee_raw)
                if start_dt is None or end_dt is None:
                    logger.warning(
                        "Skipping employee_event id=%s due to invalid datetime (date=%r start=%r end=%r)",
                        employee_id,
                        employee_raw.get("date"),
                        employee_raw.get("start_time"),
                        employee_raw.get("end_time"),
                    )
                    continue

                unified_events.append(
                    {
                        "source": "employee_event",
                        "source_event_id": f"employee_{employee_id}",
                        "title": self._employee_event_title(employee_raw),
                        "start": self._format_calendar_datetime(start_dt),
                        "end": self._format_calendar_datetime(end_dt),
                        "is_read_only": False,
                        "raw": employee_raw,
                        "_sort_start": start_dt,
                    }
                )

            for row in trainer_rows:
                source_row = dict(row)
                trainer_id = self._as_int(source_row.get("id"), default=0)
                if trainer_id <= 0:
                    logger.warning(
                        "Skipping trainer_batch row with invalid id for contact_id=%s",
                        normalized_contact_id,
                    )
                    continue

                parent_batch_id = self._as_int(source_row.get("parent_id"), default=0)
                is_child_batch = parent_batch_id != 0

                parsed_date = self._parse_calendar_date_value(source_row.get("date"))
                parsed_start_date = self._parse_calendar_date_value(source_row.get("start_date"))
                parsed_end_date = self._parse_calendar_date_value(source_row.get("end_date"))

                if parsed_start_date is None:
                    parsed_start_date = parsed_date
                if parsed_end_date is None:
                    parsed_end_date = parsed_start_date or parsed_date
                if parsed_start_date and parsed_end_date and parsed_end_date < parsed_start_date:
                    parsed_start_date, parsed_end_date = parsed_end_date, parsed_start_date

                occurrence_dates: List[date_value] = []
                if is_child_batch:
                    one_off_date = parsed_date or parsed_start_date
                    if one_off_date is None:
                        logger.warning(
                            "Skipping child trainer_batch id=%s due to missing occurrence date",
                            trainer_id,
                        )
                        continue
                    if effective_from_date <= one_off_date <= effective_to_date:
                        occurrence_dates.append(one_off_date)
                else:
                    if parsed_start_date is None or parsed_end_date is None:
                        logger.warning(
                            "Skipping parent trainer_batch id=%s due to missing range dates",
                            trainer_id,
                        )
                        continue

                    overlap_start = max(parsed_start_date, effective_from_date)
                    overlap_end = min(parsed_end_date, effective_to_date)
                    if overlap_start > overlap_end:
                        continue

                    day_codes = self._decode_day_code(source_row.get("day_code"))
                    if day_codes:
                        day_lookup = set(day_codes)
                        for current_date in self._inclusive_dates(overlap_start, overlap_end):
                            if self._weekday_sunday_zero(current_date) in day_lookup:
                                occurrence_dates.append(current_date)
                    else:
                        fallback_date = parsed_date or parsed_start_date
                        if (
                            fallback_date is not None
                            and effective_from_date <= fallback_date <= effective_to_date
                        ):
                            occurrence_dates.append(fallback_date)

                if not occurrence_dates:
                    continue

                start_time_value = self._parse_calendar_time_value(source_row.get("start_time"))
                end_time_value = self._parse_calendar_time_value(source_row.get("end_time"))
                title = self._trainer_event_title(source_row)
                batch_timezone_id = source_row.get("timezone_id")

                for occurrence_date in occurrence_dates:
                    # Create timezone-aware datetimes using batch timezone
                    event_start_dt = self._localize_batch_occurrence(
                        occurrence_date, start_time_value, batch_timezone_id
                    )
                    event_end_dt = self._localize_batch_occurrence(
                        occurrence_date, end_time_value, batch_timezone_id
                    )
                    # Format as local time string (frontend expects YYYY-MM-DD HH:MM:SS format)
                    event_start_text = event_start_dt.strftime("%Y-%m-%d %H:%M:%S")
                    event_end_text = event_end_dt.strftime("%Y-%m-%d %H:%M:%S")

                    mapped_row = dict(source_row)
                    mapped_row["event_id"] = None
                    mapped_row["batch_id"] = trainer_id
                    mapped_row["demo_id"] = None
                    mapped_row["batch_name"] = source_row.get("batch")
                    mapped_row["summary"] = title
                    mapped_row["location"] = source_row.get("venue")
                    mapped_row["event_timezone"] = (
                        "" if source_row.get("timezone_id") is None else str(source_row.get("timezone_id"))
                    )
                    mapped_row["event_start"] = event_start_text
                    mapped_row["event_end"] = event_end_text
                    mapped_row["attendees"] = "[]"
                    mapped_row["parent_batch_id"] = (
                        int(parent_batch_id) if parent_batch_id != 0 else None
                    )
                    mapped_row["parent_batch_name"] = source_row.get("parent_batch_name")

                    if is_child_batch:
                        mapped_row["batch_type"] = "prescheduled"
                        mapped_row["batch_status"] = "prescheduled"
                        mapped_row["is_original"] = 0
                        mapped_row["is_scheduled"] = 1
                        mapped_row["is_recurring"] = 0
                    else:
                        mapped_row["batch_type"] = "original"
                        mapped_row["batch_status"] = "original"
                        mapped_row["is_original"] = 1
                        mapped_row["is_scheduled"] = 0
                        mapped_row["is_recurring"] = 1

                    unified_events.append(
                        {
                            "source": "trainer_batch",
                            "source_event_id": f"trainer_{trainer_id}_{occurrence_date.strftime('%Y%m%d')}",
                            "title": title,
                            "start": event_start_text,
                            "end": event_end_text,
                            "is_read_only": True,
                            "raw": mapped_row,
                            "_sort_start": event_start_dt,
                        }
                    )

            unified_events.sort(
                key=lambda row: (row.get("_sort_start"), str(row.get("source_event_id") or ""))
            )
            for row in unified_events:
                row.pop("_sort_start", None)

            return {
                "events": unified_events,
                "total_count": len(unified_events),
            }
        except EmployeeEventsError as exc:
            if exc.code in {"EMP_EVENT_DB_UNAVAILABLE", "EMP_EVENT_INVALID_CALENDAR_QUERY"}:
                raise
            raise self._calendar_query_failed(
                data={"reason": exc.code, "message": exc.message},
            ) from exc
        except Exception as exc:
            raise self._calendar_query_failed(
                data={
                    "reason": "unexpected_exception",
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                }
            ) from exc

    def get_teacher_daily_availability(
        self,
        contact_id: int,
        date_str: str,
    ) -> Dict[str, Any]:
        """
        Calculate teacher's daily availability including workshift, busy blocks, and free slots.
        
        Args:
            contact_id: Teacher's contact_id (from batch_employee_time_view/emp_cont_view)
            date_str: Target date in YYYY-MM-DD format
        
        Returns:
            Dictionary with availability details including shift times, busy blocks, and free slots
        """
        try:
            # 1. Validate inputs
            normalized_contact_id = self._as_int(contact_id)
            if normalized_contact_id <= 0:
                raise self._invalid_calendar_query(
                    "contact_id must be a positive integer",
                    data={"field": "contact_id", "value": contact_id},
                )
            
            target_date = self._parse_calendar_query_date(date_str, "date")
            target_weekday = self._weekday_sunday_zero(target_date)
            
            # 2. Lookup employee record to get employee_id and workshift info
            workshift_rows = self.event_repository.get_employee_workshifts([normalized_contact_id])
            if not workshift_rows:
                raise EmployeeEventsError(
                    code="EMP_EVENT_TEACHER_NOT_FOUND",
                    message="Teacher not found or inactive",
                    status_code=404,
                    data={"contact_id": normalized_contact_id},
                )
            
            workshift_row = workshift_rows[0]
            employee_id = self._as_int(workshift_row.get("employee_id"), default=0)
            employee_name = workshift_row.get("employee_name")
            
            # 3. Parse workshift configuration
            in_time_text, in_time, in_time_issue = self._normalize_workshift_time(
                workshift_row.get("workshift_in_time"),
                missing_issue="missing_in_time",
                invalid_issue="invalid_in_time",
            )
            out_time_text, out_time, out_time_issue = self._normalize_workshift_time(
                workshift_row.get("workshift_out_time"),
                missing_issue="missing_out_time",
                invalid_issue="invalid_out_time",
            )
            raw_week_off_code, week_off_days, week_off_warnings = self._decode_week_off_code(
                workshift_row.get("week_off_code")
            )
            
            warnings: List[str] = []
            warnings.extend(week_off_warnings)
            
            # 4. Check if target date is a week-off day
            is_week_off = target_weekday in week_off_days
            if is_week_off:
                return {
                    "teacher_contact_id": normalized_contact_id,
                    "teacher_employee_id": employee_id,
                    "teacher_name": employee_name,
                    "date": date_str,
                    "shift_start": None,
                    "shift_end": None,
                    "is_week_off": True,
                    "busy_blocks": [],
                    "free_slots": [],
                    "total_busy_minutes": 0,
                    "total_free_minutes": 0,
                    "warnings": warnings,
                }
            
            # Check workshift configuration
            if in_time_issue or out_time_issue:
                warnings.append("workshift_unconfigured")
                return {
                    "teacher_contact_id": normalized_contact_id,
                    "teacher_employee_id": employee_id,
                    "teacher_name": employee_name,
                    "date": date_str,
                    "shift_start": in_time_text,
                    "shift_end": out_time_text,
                    "is_week_off": False,
                    "busy_blocks": [],
                    "free_slots": [],
                    "total_busy_minutes": 0,
                    "total_free_minutes": 0,
                    "warnings": warnings,
                }
            
            # 5. Fetch all events for this day
            busy_blocks: List[Dict[str, Any]] = []
            
            # 5a. Fetch trainer batch occurrences
            trainer_rows = self.event_repository.list_trainer_calendar_events(
                contact_id=normalized_contact_id,
                from_date=date_str,
                to_date=date_str,
            )
            
            for batch_row in trainer_rows:
                # Expand batch occurrence times (reuse expansion logic)
                batch_id = self._as_int(batch_row.get("id"), default=0)
                if batch_id <= 0:
                    continue
                
                parent_batch_id = self._as_int(batch_row.get("parent_id"), default=0)
                is_child_batch = parent_batch_id != 0
                
                # Parse dates for occurrence check
                parsed_date = self._parse_calendar_date_value(batch_row.get("date"))
                parsed_start_date = self._parse_calendar_date_value(batch_row.get("start_date"))
                parsed_end_date = self._parse_calendar_date_value(batch_row.get("end_date"))
                
                if parsed_start_date is None:
                    parsed_start_date = parsed_date
                if parsed_end_date is None:
                    parsed_end_date = parsed_start_date or parsed_date
                
                # Check if this batch has an occurrence on target_date
                has_occurrence = False
                if is_child_batch:
                    one_off_date = parsed_date or parsed_start_date
                    if one_off_date == target_date:
                        has_occurrence = True
                else:
                    if parsed_start_date and parsed_end_date:
                        if parsed_start_date <= target_date <= parsed_end_date:
                            day_codes = self._decode_day_code(batch_row.get("day_code"))
                            if day_codes:
                                if target_weekday in day_codes:
                                    has_occurrence = True
                            else:
                                fallback_date = parsed_date or parsed_start_date
                                if fallback_date == target_date:
                                    has_occurrence = True
                
                if has_occurrence:
                    start_time_value = self._parse_calendar_time_value(batch_row.get("start_time"))
                    end_time_value = self._parse_calendar_time_value(batch_row.get("end_time"))
                    title = self._trainer_event_title(batch_row)
                    
                    busy_blocks.append({
                        "start": start_time_value.strftime("%H:%M:%S"),
                        "end": end_time_value.strftime("%H:%M:%S"),
                        "source": "trainer_batch",
                        "event_id": f"trainer_{batch_id}_{target_date.strftime('%Y%m%d')}",
                        "title": title,
                        "_start_time": start_time_value,
                        "_end_time": end_time_value,
                    })
            
            # 5b. Fetch employee events
            employee_result = self.list_events(
                from_date=date_str,
                to_date=date_str,
                contact_id=normalized_contact_id,
                status=None,
                park=None,
                include_parked=False,
            )
            
            for emp_event in employee_result.get("events", []):
                emp_event_id = self._as_int(emp_event.get("id"), default=0)
                if emp_event_id <= 0:
                    continue
                
                start_time_text = str(emp_event.get("start_time") or "").strip()
                end_time_text = str(emp_event.get("end_time") or "").strip()
                if not start_time_text or not end_time_text:
                    continue
                
                start_time_value = self._parse_calendar_time_value(start_time_text)
                end_time_value = self._parse_calendar_time_value(end_time_text)
                title = self._employee_event_title(emp_event)
                
                busy_blocks.append({
                    "start": start_time_value.strftime("%H:%M:%S"),
                    "end": end_time_value.strftime("%H:%M:%S"),
                    "source": "employee_event",
                    "event_id": f"employee_{emp_event_id}",
                    "title": title,
                    "_start_time": start_time_value,
                    "_end_time": end_time_value,
                })
            
            # 5c. Fetch approved leave
            if employee_id > 0:
                leave_rows = self.event_repository.get_approved_leave_for_employee(
                    employee_id=employee_id,
                    from_date=date_str,
                    to_date=date_str,
                )
                
                for leave_row in leave_rows:
                    leave_id = self._as_int(leave_row.get("id"), default=0)
                    if leave_id <= 0:
                        continue
                    
                    # For leave, use default work hours (09:00-17:00) if not specified
                    # Leave typically blocks the entire day
                    busy_blocks.append({
                        "start": self._LEAVE_DEFAULT_START_TIME.strftime("%H:%M:%S"),
                        "end": self._LEAVE_DEFAULT_END_TIME.strftime("%H:%M:%S"),
                        "source": "leave",
                        "event_id": f"leave_{leave_id}",
                        "title": "Approved Leave",
                        "_start_time": self._LEAVE_DEFAULT_START_TIME,
                        "_end_time": self._LEAVE_DEFAULT_END_TIME,
                    })
            
            # 6. Sort busy blocks by start time
            busy_blocks.sort(key=lambda b: b["_start_time"])
            
            # 7. Calculate free slots within shift window
            free_slots: List[Dict[str, Any]] = []
            shift_start_minutes = in_time.hour * 60 + in_time.minute
            shift_end_minutes = out_time.hour * 60 + out_time.minute
            
            # Handle overnight shifts
            if shift_end_minutes <= shift_start_minutes:
                shift_end_minutes += 24 * 60
            
            current_minutes = shift_start_minutes
            
            for block in busy_blocks:
                block_start = block["_start_time"]
                block_end = block["_end_time"]
                block_start_minutes = block_start.hour * 60 + block_start.minute
                block_end_minutes = block_end.hour * 60 + block_end.minute
                
                # Skip blocks that are outside shift window
                if block_end_minutes <= shift_start_minutes:
                    continue
                if block_start_minutes >= shift_end_minutes:
                    continue
                
                # Clip block to shift window
                block_start_minutes = max(block_start_minutes, shift_start_minutes)
                block_end_minutes = min(block_end_minutes, shift_end_minutes)
                
                # Add free slot before this block (if any)
                if current_minutes < block_start_minutes:
                    gap_minutes = block_start_minutes - current_minutes
                    free_start_time = time_value(current_minutes // 60, current_minutes % 60)
                    free_end_time = time_value(block_start_minutes // 60, block_start_minutes % 60)
                    free_slots.append({
                        "start": free_start_time.strftime("%H:%M:%S"),
                        "end": free_end_time.strftime("%H:%M:%S"),
                        "duration_minutes": gap_minutes,
                    })
                
                # Move current position past this block
                current_minutes = max(current_minutes, block_end_minutes)
            
            # Add final free slot after last block (if any)
            if current_minutes < shift_end_minutes:
                gap_minutes = shift_end_minutes - current_minutes
                free_start_time = time_value(current_minutes // 60, current_minutes % 60)
                free_end_time = time_value(shift_end_minutes // 60, shift_end_minutes % 60)
                free_slots.append({
                    "start": free_start_time.strftime("%H:%M:%S"),
                    "end": free_end_time.strftime("%H:%M:%S"),
                    "duration_minutes": gap_minutes,
                })
            
            # 8. Calculate totals
            total_busy_minutes = sum(
                (block["_end_time"].hour * 60 + block["_end_time"].minute) -
                (block["_start_time"].hour * 60 + block["_start_time"].minute)
                for block in busy_blocks
            )
            total_free_minutes = sum(slot["duration_minutes"] for slot in free_slots)
            
            # 9. Remove internal helper fields from busy blocks
            for block in busy_blocks:
                block.pop("_start_time", None)
                block.pop("_end_time", None)
            
            return {
                "teacher_contact_id": normalized_contact_id,
                "teacher_employee_id": employee_id,
                "teacher_name": employee_name,
                "date": date_str,
                "shift_start": in_time_text,
                "shift_end": out_time_text,
                "is_week_off": False,
                "busy_blocks": busy_blocks,
                "free_slots": free_slots,
                "total_busy_minutes": total_busy_minutes,
                "total_free_minutes": total_free_minutes,
                "warnings": warnings,
            }
        
        except EmployeeEventsError as exc:
            if exc.code in {
                "EMP_EVENT_DB_UNAVAILABLE",
                "EMP_EVENT_INVALID_CALENDAR_QUERY",
                "EMP_EVENT_TEACHER_NOT_FOUND",
            }:
                raise
            raise self._calendar_query_failed(
                data={"reason": exc.code, "message": exc.message},
            ) from exc
        except Exception as exc:
            raise self._calendar_query_failed(
                data={
                    "reason": "unexpected_exception",
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                }
            ) from exc

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

    def get_demo_events_batch(
        self,
        employee_ids: List[Any],
        from_date: str,
        to_date: str,
        statuses: Optional[List[Any]] = None,
        types: Optional[List[Any]] = None,
        venue_ids: Optional[List[Any]] = None,
        batch_ids: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        try:
            normalized_employee_ids = self._normalize_demo_employee_ids(employee_ids)
            from_date_value = self._parse_demo_query_date(from_date, "from_date")
            to_date_value = self._parse_demo_query_date(to_date, "to_date")

            if from_date_value > to_date_value:
                raise self._invalid_demo_query(
                    "from_date must be less than or equal to to_date",
                )

            range_day_count = (to_date_value - from_date_value).days + 1
            if range_day_count > 62:
                raise self._invalid_demo_query(
                    "Date range may not exceed 62 days",
                    data={"range_day_count": range_day_count},
                )

            normalized_statuses = self._normalize_demo_filter_values(
                statuses,
                field_name="statuses",
                min_value=0,
            )
            normalized_types = self._normalize_demo_filter_values(
                types,
                field_name="types",
                min_value=0,
            )
            normalized_venue_ids = self._normalize_demo_filter_values(
                venue_ids,
                field_name="venue_ids",
                min_value=1,
            )
            normalized_batch_ids = self._normalize_demo_filter_values(
                batch_ids,
                field_name="batch_ids",
                min_value=1,
            )

            rows = self.event_repository.get_demo_events(
                employee_ids=normalized_employee_ids,
                from_date=from_date_value.isoformat(),
                to_date=to_date_value.isoformat(),
                statuses=normalized_statuses or None,
                types=normalized_types or None,
                venue_ids=normalized_venue_ids or None,
                batch_ids=normalized_batch_ids or None,
            )

            demos_by_employee: Dict[int, List[Dict[str, Any]]] = {
                eid: [] for eid in normalized_employee_ids
            }
            employee_id_set = set(normalized_employee_ids)
            for row in rows:
                matched_ids: set[int] = set()
                for col in ("host_contact_id", "sc_contact_id", "so_contact_id", "owner_contact_id"):
                    val = self._as_int(row.get(col))
                    if val in employee_id_set:
                        matched_ids.add(val)
                for eid in matched_ids:
                    demos_by_employee[eid].append(row)

            employees = []
            for employee_id in normalized_employee_ids:
                employees.append({
                    "employee_id": employee_id,
                    "demos": demos_by_employee.get(employee_id, []),
                    "demo_count": len(demos_by_employee.get(employee_id, [])),
                })

            return {
                "from_date": from_date_value.isoformat(),
                "to_date": to_date_value.isoformat(),
                "range_day_count": range_day_count,
                "employee_count": len(normalized_employee_ids),
                "matched_count": sum(
                    1 for eid in normalized_employee_ids
                    if demos_by_employee.get(eid)
                ),
                "total_demos": len(rows),
                "employees": employees,
            }
        except EmployeeEventsError:
            raise
        except Exception as exc:
            raise self._demo_query_failed(
                message=f"Unexpected error fetching demo events: {exc}",
            ) from exc
