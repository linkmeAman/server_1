"""Datetime and payload normalization helpers for Google Calendar V1."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from typing import Any, Dict, Iterable, Optional
from zoneinfo import ZoneInfo


def _parse_datetime_value(value: Any, fallback_timezone: str) -> Optional[datetime]:
    """Parse dateTime/date values from Google payloads."""
    if value in (None, ""):
        return None

    raw = str(value).strip()
    if not raw:
        return None

    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw

    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(fallback_timezone))
        return parsed
    except Exception:
        pass

    try:
        parsed_date = date.fromisoformat(raw)
        return datetime.combine(
            parsed_date,
            time.min,
            tzinfo=ZoneInfo(fallback_timezone),
        )
    except Exception:
        return None


def to_utc_iso(value: Any, fallback_timezone: str) -> str:
    """Convert Google dateTime/date values to UTC ISO string, preserving raw fallback."""
    raw = "" if value is None else str(value)
    parsed = _parse_datetime_value(value, fallback_timezone)
    if not parsed:
        return raw
    return parsed.astimezone(timezone.utc).isoformat()


def extract_event_timezone(event_payload: Dict[str, Any], fallback_timezone: str) -> str:
    """Get event timezone from payload with fallback."""
    start = event_payload.get("start") or {}
    end = event_payload.get("end") or {}
    return str(start.get("timeZone") or end.get("timeZone") or fallback_timezone)


def serialize_attendees(attendees: Any) -> str:
    """Serialize attendees safely for log storage."""
    if attendees is None:
        return "[]"
    try:
        return json.dumps(attendees, ensure_ascii=True)
    except Exception:
        return "[]"


def normalize_google_event_for_log(
    event_payload: Dict[str, Any],
    fallback_timezone: str,
) -> Dict[str, Any]:
    """Normalize Google event payload into DB log field values."""
    start = event_payload.get("start") or {}
    end = event_payload.get("end") or {}

    start_value = start.get("dateTime") or start.get("date")
    end_value = end.get("dateTime") or end.get("date")

    guests_can_modify = event_payload.get("guestsCanModify")
    if isinstance(guests_can_modify, bool):
        guests_can_modify = int(guests_can_modify)

    timezone_name = extract_event_timezone(event_payload, fallback_timezone)
    return {
        "event_id": str(event_payload.get("id") or ""),
        "summary": str(event_payload.get("summary") or ""),
        "description": str(event_payload.get("description") or ""),
        "location": str(event_payload.get("location") or ""),
        "event_start": to_utc_iso(start_value, timezone_name),
        "event_end": to_utc_iso(end_value, timezone_name),
        "event_timezone": timezone_name,
        "attendees": serialize_attendees(event_payload.get("attendees")),
        "can_attendees_modify": guests_can_modify,
    }


def select_next_upcoming_instance_id(
    instances: Iterable[Dict[str, Any]],
    compare_timezone: str,
) -> Optional[str]:
    """Return the earliest upcoming instance ID in compare timezone."""
    tz = ZoneInfo(compare_timezone)
    now = datetime.now(tz)

    candidates = []
    for instance in instances:
        instance_id = instance.get("id")
        if not instance_id:
            continue

        start = instance.get("start") or {}
        start_value = start.get("dateTime") or start.get("date")
        parsed_start = _parse_datetime_value(start_value, compare_timezone)
        if not parsed_start:
            continue

        local_start = parsed_start.astimezone(tz)
        if local_start > now:
            candidates.append((local_start, str(instance_id)))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]
