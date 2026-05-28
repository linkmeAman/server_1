"""Scheduled follow-up reminder notifications."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI
from sqlalchemy import text

from app.core.database import central_session_context, main_session_context
from app.core.settings import get_settings
from app.modules.notifications.schemas.models import (
    DEFAULT_FOLLOWUP_OFFSETS,
    FOLLOWUP_EVENT_TYPE,
    FOLLOWUP_SOURCE,
    NotificationRecipientScope,
)
from app.modules.notifications.services.publisher import publish_notification
from app.modules.notifications.services.repository import get_notification_rule

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FollowupCandidate:
    followup_id: str
    contact_id: str | None
    employee_id: int
    employee_contact_id: int | None
    reminder_at: datetime
    fullname: str | None
    country_code: str | None
    mobile: str | None
    bid: int | None
    status: int | None
    master: str | None
    master_id: str | None


def _workspace_timezone() -> ZoneInfo:
    timezone_name = get_settings().NOTIFICATION_WORKSPACE_TIMEZONE or "Asia/Kolkata"
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "Invalid NOTIFICATION_WORKSPACE_TIMEZONE=%s; falling back to Asia/Kolkata",
            timezone_name,
        )
        return ZoneInfo("Asia/Kolkata")


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _datetime_from_row(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    normalized = str(value).strip()
    if not normalized or normalized.startswith("0000-00-00"):
        return None
    try:
        return datetime.fromisoformat(normalized.replace(" ", "T")).replace(tzinfo=None)
    except ValueError:
        logger.warning("Invalid follow-up reminder timestamp ignored value=%s", normalized)
        return None


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _localize_reminder(reminder_at: datetime, workspace_tz: ZoneInfo) -> datetime:
    if reminder_at.tzinfo is not None:
        return reminder_at.astimezone(workspace_tz)
    return reminder_at.replace(tzinfo=workspace_tz)


def _format_db_datetime(value: datetime) -> str:
    return value.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _mobile_last4(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits[-4:] if digits else None


def _display_name(candidate: FollowupCandidate) -> str:
    if candidate.fullname:
        return candidate.fullname
    if candidate.mobile:
        return f"lead ending {candidate.mobile[-4:]}"
    return f"follow-up #{candidate.followup_id}"


def _parse_offsets_json(value: str | None) -> list[int]:
    if not value:
        return DEFAULT_FOLLOWUP_OFFSETS.copy()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return DEFAULT_FOLLOWUP_OFFSETS.copy()
    if not isinstance(parsed, list):
        return DEFAULT_FOLLOWUP_OFFSETS.copy()

    offsets: set[int] = set()
    for item in parsed:
        try:
            offset = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= offset <= 1440:
            offsets.add(offset)
    return sorted(offsets) or DEFAULT_FOLLOWUP_OFFSETS.copy()


async def max_followup_offset() -> int:
    """Return the largest enabled follow-up offset configured by any user."""
    async with main_session_context() as session:
        result = await session.execute(
            text(
                """
                SELECT reminder_offsets_json
                FROM notification_delivery_rule
                WHERE source = :source
                    AND event_type = :event_type
                    AND enabled = 1
                """
            ),
            {"source": FOLLOWUP_SOURCE, "event_type": FOLLOWUP_EVENT_TYPE},
        )
        offsets: set[int] = set(DEFAULT_FOLLOWUP_OFFSETS)
        for row in result.mappings().all():
            offsets.update(_parse_offsets_json(row.get("reminder_offsets_json")))
    return max(offsets or set(DEFAULT_FOLLOWUP_OFFSETS))


async def fetch_followup_candidates(
    *,
    now_local: datetime,
    max_offset_minutes: int,
    lookback_seconds: int,
) -> list[FollowupCandidate]:
    workspace_tz = _workspace_timezone()
    start_at = (now_local - timedelta(seconds=lookback_seconds)).astimezone(workspace_tz)
    end_at = (now_local + timedelta(minutes=max_offset_minutes)).astimezone(workspace_tz)

    async with main_session_context() as session:
        result = await session.execute(
            text(
                """
                SELECT
                    f.id AS followup_id,
                    f.contact_id,
                    f.employee_id,
                    e.contact_id AS employee_contact_id,
                    f.reminder AS reminder_at,
                    f.status,
                    f.master,
                    f.master_id,
                    f.bid,
                    c.country_code,
                    c.mobile,
                    TRIM(CONCAT_WS(' ', c.fname, c.mname, c.lname)) AS fullname
                FROM followup f
                LEFT JOIN contact c ON c.id = f.contact_id
                LEFT JOIN employee e ON e.id = f.employee_id
                WHERE COALESCE(f.park, 0) = 0
                    AND COALESCE(f.unfollow, 0) = 0
                    AND COALESCE(f.employee_id, 0) <> 0
                    AND f.reminder IS NOT NULL
                    AND f.reminder <> '0000-00-00 00:00:00'
                    AND COALESCE(f.status, 0) IN (0, 1, 2, 5)
                    AND f.reminder >= :start_at
                    AND f.reminder <= :end_at
                ORDER BY f.reminder ASC
                LIMIT 500
                """
            ),
            {
                "start_at": _format_db_datetime(start_at),
                "end_at": _format_db_datetime(end_at),
            },
        )
        rows = result.mappings().all()

    candidates: list[FollowupCandidate] = []
    for row in rows:
        reminder_at = _datetime_from_row(row.get("reminder_at"))
        employee_id = _as_int(row.get("employee_id"))
        if reminder_at is None or employee_id is None:
            continue
        candidates.append(
            FollowupCandidate(
                followup_id=str(row["followup_id"]),
                contact_id=_as_text(row.get("contact_id")),
                employee_id=employee_id,
                employee_contact_id=_as_int(row.get("employee_contact_id")),
                reminder_at=reminder_at,
                fullname=_as_text(row.get("fullname")),
                country_code=_as_text(row.get("country_code")),
                mobile=_as_text(row.get("mobile")),
                bid=_as_int(row.get("bid")),
                status=_as_int(row.get("status")),
                master=_as_text(row.get("master")),
                master_id=_as_text(row.get("master_id")),
            )
        )
    return candidates


async def resolve_user_id_for_employee(
    employee_id: int,
    employee_contact_id: int | None,
) -> str | None:
    """Map a legacy employee row to the authenticated platform user id."""
    settings = get_settings()
    tenant_db = settings.DB_NAME

    async with central_session_context() as session:
        if tenant_db:
            mapped = await session.execute(
                text(
                    """
                    SELECT map.user_id AS user_id
                    FROM auth_employee_user_map map
                    JOIN user u ON u.id = map.user_id
                    JOIN client_db c ON c.id = u.client_id
                    WHERE map.employee_id = :employee_id
                        AND map.is_active = 1
                        AND c.db_name = :tenant_db
                        AND u.inactive = 0
                        AND COALESCE(u.park, 0) = 0
                    ORDER BY map.id DESC
                    LIMIT 1
                    """
                ),
                {"employee_id": int(employee_id), "tenant_db": tenant_db},
            )
        else:
            mapped = await session.execute(
                text(
                    """
                    SELECT map.user_id AS user_id
                    FROM auth_employee_user_map map
                    JOIN user u ON u.id = map.user_id
                    WHERE map.employee_id = :employee_id
                        AND map.is_active = 1
                        AND u.inactive = 0
                        AND COALESCE(u.park, 0) = 0
                    ORDER BY map.id DESC
                    LIMIT 1
                    """
                ),
                {"employee_id": int(employee_id)},
            )
        row = mapped.mappings().first()
        if row and row.get("user_id") is not None:
            return str(row["user_id"])

        if employee_contact_id is None:
            return None

        if tenant_db:
            fallback = await session.execute(
                text(
                    """
                    SELECT u.id AS user_id
                    FROM user u
                    JOIN client_db c ON c.id = u.client_id
                    WHERE c.db_name = :tenant_db
                        AND u.contact_id = :employee_contact_id
                        AND u.inactive = 0
                        AND COALESCE(u.park, 0) = 0
                    ORDER BY u.id DESC
                    LIMIT 1
                    """
                ),
                {
                    "employee_contact_id": int(employee_contact_id),
                    "tenant_db": tenant_db,
                },
            )
        else:
            fallback = await session.execute(
                text(
                    """
                    SELECT u.id AS user_id
                    FROM user u
                    WHERE u.contact_id = :employee_contact_id
                        AND u.inactive = 0
                        AND COALESCE(u.park, 0) = 0
                    ORDER BY u.id DESC
                    LIMIT 1
                    """
                ),
                {"employee_contact_id": int(employee_contact_id)},
            )
        row = fallback.mappings().first()
        return str(row["user_id"]) if row and row.get("user_id") is not None else None


async def resolve_followup_recipients(
    *,
    candidate: FollowupCandidate,
    assigned_user_id: str,
    recipient_scope: NotificationRecipientScope,
) -> list[str]:
    if recipient_scope != "branch" or candidate.bid is None:
        return [assigned_user_id]

    settings = get_settings()
    tenant_db = settings.DB_NAME
    recipients = {assigned_user_id}

    async with central_session_context() as session:
        if tenant_db:
            result = await session.execute(
                text(
                    """
                    SELECT DISTINCT ub.user_id AS user_id
                    FROM user_bid ub
                    JOIN user u ON u.id = ub.user_id
                    JOIN client_db c ON c.id = u.client_id
                    WHERE ub.bid = :bid
                        AND c.db_name = :tenant_db
                        AND u.inactive = 0
                        AND COALESCE(u.park, 0) = 0
                    LIMIT 200
                    """
                ),
                {"bid": int(candidate.bid), "tenant_db": tenant_db},
            )
        else:
            result = await session.execute(
                text(
                    """
                    SELECT DISTINCT ub.user_id AS user_id
                    FROM user_bid ub
                    JOIN user u ON u.id = ub.user_id
                    WHERE ub.bid = :bid
                        AND u.inactive = 0
                        AND COALESCE(u.park, 0) = 0
                    LIMIT 200
                    """
                ),
                {"bid": int(candidate.bid)},
            )

    for row in result.mappings().all():
        if row.get("user_id") is not None:
            recipients.add(str(row["user_id"]))
    return sorted(recipients)


async def claim_dispatch(
    *,
    candidate: FollowupCandidate,
    reminder_at_utc: datetime,
    offset_minutes: int,
    recipient_user_id: str,
    event_id: str,
) -> bool:
    async with main_session_context() as session:
        result = await session.execute(
            text(
                """
                INSERT IGNORE INTO notification_dispatch_ledger (
                    followup_id,
                    reminder_at,
                    offset_minutes,
                    recipient_user_id,
                    event_id
                )
                VALUES (
                    :followup_id,
                    :reminder_at,
                    :offset_minutes,
                    :recipient_user_id,
                    :event_id
                )
                """
            ),
            {
                "followup_id": str(candidate.followup_id),
                "reminder_at": _utc_naive(reminder_at_utc),
                "offset_minutes": int(offset_minutes),
                "recipient_user_id": str(recipient_user_id),
                "event_id": event_id,
            },
        )
        await session.commit()
        return int(result.rowcount or 0) > 0


async def publish_followup_reminder(
    *,
    candidate: FollowupCandidate,
    reminder_at_local: datetime,
    offset_minutes: int,
    recipient_user_id: str,
    workspace_tz: ZoneInfo,
) -> bool:
    reminder_at_utc = reminder_at_local.astimezone(timezone.utc)
    event_id = str(uuid4())
    claimed = await claim_dispatch(
        candidate=candidate,
        reminder_at_utc=reminder_at_utc,
        offset_minutes=offset_minutes,
        recipient_user_id=recipient_user_id,
        event_id=event_id,
    )
    if not claimed:
        return False

    dedupe_key = (
        f"followup-reminder:{candidate.followup_id}:"
        f"{int(reminder_at_utc.timestamp())}:{offset_minutes}:{recipient_user_id}"
    )
    request_id = (
        f"followup-reminder:{candidate.followup_id}:"
        f"{int(reminder_at_utc.timestamp())}:{offset_minutes}"
    )
    display_name = _display_name(candidate)
    metadata = {
        "followup_id": candidate.followup_id,
        "contact_id": candidate.contact_id,
        "employee_id": candidate.employee_id,
        "reminder_at": reminder_at_utc.isoformat(),
        "reminder_at_local": reminder_at_local.isoformat(),
        "workspace_timezone": workspace_tz.key,
        "offset_minutes": offset_minutes,
        "bid": candidate.bid,
        "status": candidate.status,
        "master": candidate.master,
        "master_id": candidate.master_id,
        "display_name": display_name,
        "mobile_last4": _mobile_last4(candidate.mobile),
    }

    await publish_notification(
        request_id=request_id,
        event_type=FOLLOWUP_EVENT_TYPE,
        severity="warning",
        source=FOLLOWUP_SOURCE,
        message=f"Follow-up with {display_name} in {offset_minutes} minutes.",
        metadata=metadata,
        user_id=recipient_user_id,
        group_key=f"followup:{candidate.followup_id}",
        dedupe_key=dedupe_key,
    )
    return True


def _should_dispatch_offset(
    *,
    now_local: datetime,
    reminder_at_local: datetime,
    offset_minutes: int,
    lookback_seconds: int,
) -> bool:
    seconds_until = (reminder_at_local - now_local).total_seconds()
    upper = offset_minutes * 60
    lower = upper - lookback_seconds
    return lower <= seconds_until <= upper


async def scan_followup_reminders(now_utc: datetime | None = None) -> int:
    """Scan active follow-ups and publish due personalized reminders."""
    settings = get_settings()
    if not settings.FOLLOWUP_REMINDERS_ENABLED:
        return 0

    workspace_tz = _workspace_timezone()
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_local = now.astimezone(workspace_tz)
    interval_seconds = max(15, int(settings.FOLLOWUP_REMINDER_SCAN_INTERVAL_SECONDS))
    lookback_seconds = max(120, interval_seconds * 2)

    max_offset = await max_followup_offset()
    candidates = await fetch_followup_candidates(
        now_local=now_local,
        max_offset_minutes=max_offset,
        lookback_seconds=lookback_seconds,
    )

    published = 0
    for candidate in candidates:
        assigned_user_id = await resolve_user_id_for_employee(
            candidate.employee_id,
            candidate.employee_contact_id,
        )
        if not assigned_user_id:
            logger.info(
                "Skipping follow-up reminder with unresolved employee followup_id=%s employee_id=%s",
                candidate.followup_id,
                candidate.employee_id,
            )
            continue

        rule = await get_notification_rule(
            user_id=assigned_user_id,
            source=FOLLOWUP_SOURCE,
            event_type=FOLLOWUP_EVENT_TYPE,
        )
        if not rule.enabled:
            continue

        reminder_at_local = _localize_reminder(candidate.reminder_at, workspace_tz)
        for offset in rule.reminder_offsets_minutes:
            if not _should_dispatch_offset(
                now_local=now_local,
                reminder_at_local=reminder_at_local,
                offset_minutes=offset,
                lookback_seconds=lookback_seconds,
            ):
                continue

            recipients = await resolve_followup_recipients(
                candidate=candidate,
                assigned_user_id=assigned_user_id,
                recipient_scope=rule.recipient_scope,
            )
            for recipient_user_id in recipients:
                if await publish_followup_reminder(
                    candidate=candidate,
                    reminder_at_local=reminder_at_local,
                    offset_minutes=offset,
                    recipient_user_id=recipient_user_id,
                    workspace_tz=workspace_tz,
                ):
                    published += 1
    return published


async def followup_reminder_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    interval_seconds = max(15, int(settings.FOLLOWUP_REMINDER_SCAN_INTERVAL_SECONDS))
    logger.info("Follow-up reminder scheduler started interval_seconds=%s", interval_seconds)
    try:
        while not stop_event.is_set():
            try:
                count = await scan_followup_reminders()
                if count:
                    logger.info("Published %s follow-up reminder notifications", count)
            except Exception:
                logger.exception("Follow-up reminder scan failed")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                continue
    finally:
        logger.info("Follow-up reminder scheduler stopped")


def start_followup_reminder_scheduler(app: FastAPI) -> None:
    if not get_settings().FOLLOWUP_REMINDERS_ENABLED:
        logger.info("Follow-up reminder scheduler disabled")
        return

    task = getattr(app.state, "followup_reminder_task", None)
    if task is not None and not task.done():
        return

    stop_event = asyncio.Event()
    app.state.followup_reminder_stop_event = stop_event
    app.state.followup_reminder_task = asyncio.create_task(
        followup_reminder_loop(stop_event)
    )


async def stop_followup_reminder_scheduler(app: FastAPI) -> None:
    stop_event = getattr(app.state, "followup_reminder_stop_event", None)
    task = getattr(app.state, "followup_reminder_task", None)
    if stop_event is not None:
        stop_event.set()
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    app.state.followup_reminder_stop_event = None
    app.state.followup_reminder_task = None
