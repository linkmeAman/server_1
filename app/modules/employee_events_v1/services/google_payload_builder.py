"""Google payload builder for employee events."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.core.settings import get_settings


def _parse_local_datetime(date_value: Any, time_value: Any, timezone_name: str) -> datetime:
    date_str = str(date_value or "").strip()
    time_str = str(time_value or "").strip()

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(f"{date_str} {time_str}", fmt)
            return parsed.replace(tzinfo=ZoneInfo(timezone_name))
        except ValueError:
            continue

    # Fallback: keep deterministic error for bad source data.
    raise ValueError(f"Invalid date/time combination: {date_str} {time_str}")


def _contact_display_name(contact: Optional[Dict[str, Any]]) -> str:
    if not contact:
        return ""
    parts = [
        str(contact.get("fname") or "").strip(),
        str(contact.get("lname") or "").strip(),
    ]
    return " ".join(p for p in parts if p)


def _allowances_display(allowances: List[Dict[str, Any]]) -> str:
    if not allowances:
        return "None"
    chunks = []
    for item in allowances:
        name = str(item.get("name") or "").strip() or "Allowance"
        amount = item.get("amount")
        chunks.append(f"{name}: {amount}")
    return "; ".join(chunks)


def build_google_event_payload(
    event_row: Dict[str, Any],
    allowances: List[Dict[str, Any]],
    contact: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build Google Calendar event payload from local employee event data."""
    settings = get_settings()
    timezone_name = settings.EMP_EVENT_TIMEZONE

    start_dt = _parse_local_datetime(event_row.get("date"), event_row.get("start_time"), timezone_name)
    end_dt = _parse_local_datetime(event_row.get("date"), event_row.get("end_time"), timezone_name)

    contact_name = _contact_display_name(contact)
    category = str(event_row.get("category") or "").strip()
    event_type = str(event_row.get("type") or "").strip()
    branch_name = str(event_row.get("branch") or "").strip()
    summary_parts = [branch_name, event_type, category, contact_name]
    summary = " | ".join(part for part in summary_parts if part) or "Employee Event"

    contact_email = ""
    if contact:
        cc = str(contact.get("country_code") or "").strip()
        mobile = str(contact.get("mobile") or "").strip()
        contact_mobile = f"{cc}{mobile}" if (cc or mobile) else ""
        contact_email = str(contact.get("email") or "").strip()

    description_lines = [
        f"Description: {event_row.get('description') or ''}",
        f"Category: {event_row.get('category')}",
        f"Type: {event_row.get('type')}",
        f"Lease Type: {event_row.get('lease_type')}",
        f"Amount: {event_row.get('amount')}",
        f"Deduction Amount: {event_row.get('deduction_amount')}",
        f"Allowance: {event_row.get('allowance')}",
        f"Allowances: {_allowances_display(allowances)}",
        f"Contact Name: {contact_name}",
        f"Contact Mobile: {contact_mobile}",
        f"Contact Email: {contact_email}",
    ]

    payload: Dict[str, Any] = {
        "summary": summary,
        "description": "",
        "location": str(event_row.get("branch") or ""),
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": timezone_name,
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": timezone_name,
        },
    }

    if contact_email:
        payload["attendees"] = [{"email": contact_email}]

    return payload
