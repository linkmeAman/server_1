"""Database persistence for universal notifications."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import bindparam, text

from app.core.database import main_session_context
from app.modules.notifications.schemas.models import (
    DEFAULT_FOLLOWUP_OFFSETS,
    DEFAULT_RECIPIENT_SCOPE,
    FOLLOWUP_EVENT_TYPE,
    FOLLOWUP_SOURCE,
    NotificationDeliveryRule,
    NotificationEvent,
    NotificationPreferencePatch,
    NotificationPreferences,
    NotificationRulePatch,
)

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {
    "info": 10,
    "success": 20,
    "warning": 30,
    "error": 40,
    "critical": 50,
}


def _user_id(value: int | str | None) -> str | None:
    return None if value is None else str(value)


def _metadata_json(value: dict[str, Any]) -> str:
    return json.dumps(value or {}, separators=(",", ":"), ensure_ascii=False)


def _metadata_from_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        logger.warning("Invalid notification metadata JSON ignored")
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _offsets_json(value: list[int]) -> str:
    offsets = sorted({int(item) for item in value if 0 <= int(item) <= 1440})
    return json.dumps(offsets or DEFAULT_FOLLOWUP_OFFSETS, separators=(",", ":"))


def _offsets_from_json(value: str | None) -> list[int]:
    if not value:
        return DEFAULT_FOLLOWUP_OFFSETS.copy()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        logger.warning("Invalid notification reminder offsets JSON ignored")
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


def _datetime_from_event_timestamp(value: str) -> datetime:
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.now(timezone.utc)

    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _timestamp_from_row(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _event_from_row(row: dict[str, Any]) -> NotificationEvent:
    return NotificationEvent(
        event_id=str(row["event_id"]),
        request_id=str(row["request_id"]),
        event_type=str(row["event_type"]),
        severity=str(row["severity"]),  # type: ignore[arg-type]
        source=str(row["source"]),
        timestamp=_timestamp_from_row(row["event_timestamp"]),
        message=str(row["message"]),
        metadata=_metadata_from_json(row.get("metadata_json")),
        user_id=row.get("user_id"),
        group_key=row.get("group_key"),
        dedupe_key=row.get("dedupe_key"),
        read=row.get("read_at") is not None,
    )


def _default_followup_rule() -> NotificationDeliveryRule:
    return NotificationDeliveryRule(
        source=FOLLOWUP_SOURCE,
        event_type=FOLLOWUP_EVENT_TYPE,
        enabled=True,
        reminder_offsets_minutes=DEFAULT_FOLLOWUP_OFFSETS.copy(),
        recipient_scope=DEFAULT_RECIPIENT_SCOPE,
        updated_at=None,
    )


def _rule_from_row(row: dict[str, Any]) -> NotificationDeliveryRule:
    return NotificationDeliveryRule(
        source=str(row["source"]),
        event_type=str(row["event_type"]),
        enabled=bool(row["enabled"]),
        reminder_offsets_minutes=_offsets_from_json(row.get("reminder_offsets_json")),
        recipient_scope=str(row["recipient_scope"]),  # type: ignore[arg-type]
        updated_at=_timestamp_from_row(row["updated_at"]) if row.get("updated_at") else None,
    )


async def save_notification_event(event: NotificationEvent) -> None:
    """Append a notification event to the durable event log."""
    async with main_session_context() as session:
        await session.execute(
            text(
                """
                INSERT INTO notification_event (
                    event_id,
                    request_id,
                    event_type,
                    severity,
                    source,
                    event_timestamp,
                    message,
                    metadata_json,
                    user_id,
                    group_key,
                    dedupe_key
                )
                VALUES (
                    :event_id,
                    :request_id,
                    :event_type,
                    :severity,
                    :source,
                    :event_timestamp,
                    :message,
                    :metadata_json,
                    :user_id,
                    :group_key,
                    :dedupe_key
                )
                ON DUPLICATE KEY UPDATE
                    request_id = VALUES(request_id),
                    event_type = VALUES(event_type),
                    severity = VALUES(severity),
                    source = VALUES(source),
                    event_timestamp = VALUES(event_timestamp),
                    message = VALUES(message),
                    metadata_json = VALUES(metadata_json),
                    user_id = VALUES(user_id),
                    group_key = VALUES(group_key),
                    dedupe_key = VALUES(dedupe_key)
                """
            ),
            {
                "event_id": event.event_id,
                "request_id": event.request_id,
                "event_type": event.event_type,
                "severity": event.severity,
                "source": event.source,
                "event_timestamp": _datetime_from_event_timestamp(event.timestamp),
                "message": event.message,
                "metadata_json": _metadata_json(event.metadata),
                "user_id": _user_id(event.user_id),
                "group_key": event.group_key,
                "dedupe_key": event.dedupe_key,
            },
        )
        await session.commit()


async def list_recent_notifications(
    *,
    user_id: int | str,
    min_severity: str | None = None,
    limit: int = 100,
) -> list[NotificationEvent]:
    """Return visible, uncleared notifications for a user."""
    limit = max(1, min(limit, 500))
    min_rank = _SEVERITY_RANK.get(min_severity or "info", 10)
    allowed_severities = [
        severity for severity, rank in _SEVERITY_RANK.items() if rank >= min_rank
    ]

    async with main_session_context() as session:
        result = await session.execute(
            text(
                """
                SELECT
                    event.event_id,
                    event.created_at,
                    event.request_id,
                    event.event_type,
                    event.severity,
                    event.source,
                    event.event_timestamp,
                    event.message,
                    event.metadata_json,
                    event.user_id,
                    event.group_key,
                    event.dedupe_key,
                    state.read_at
                FROM notification_event AS event
                LEFT JOIN notification_user_state AS state
                    ON state.event_id = event.event_id
                    AND state.user_id = :viewer_user_id
                WHERE event.user_id = :viewer_user_id
                    AND state.cleared_at IS NULL
                    AND event.severity IN :allowed_user_severities
                UNION ALL
                SELECT
                    event.event_id,
                    event.created_at,
                    event.request_id,
                    event.event_type,
                    event.severity,
                    event.source,
                    event.event_timestamp,
                    event.message,
                    event.metadata_json,
                    event.user_id,
                    event.group_key,
                    event.dedupe_key,
                    state.read_at
                FROM notification_event AS event
                LEFT JOIN notification_user_state AS state
                    ON state.event_id = event.event_id
                    AND state.user_id = :viewer_user_id
                WHERE event.user_id IS NULL
                    AND state.cleared_at IS NULL
                    AND event.severity IN :allowed_global_severities
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ).bindparams(
                bindparam("allowed_user_severities", expanding=True),
                bindparam("allowed_global_severities", expanding=True),
            ),
            {
                "viewer_user_id": str(user_id),
                "allowed_user_severities": allowed_severities,
                "allowed_global_severities": allowed_severities,
                "limit": limit,
            },
        )
        return [_event_from_row(dict(row)) for row in result.mappings().all()]


async def mark_notification_read(*, user_id: int | str, event_id: str) -> bool:
    async with main_session_context() as session:
        exists = await session.execute(
            text(
                """
                SELECT 1
                FROM notification_event
                WHERE event_id = :event_id
                    AND user_id = :viewer_user_id
                UNION ALL
                SELECT 1
                FROM notification_event
                WHERE event_id = :event_id
                    AND user_id IS NULL
                LIMIT 1
                """
            ),
            {"event_id": event_id, "viewer_user_id": str(user_id)},
        )
        if exists.scalar_one_or_none() is None:
            return False

        await session.execute(
            text(
                """
                INSERT INTO notification_user_state (event_id, user_id, read_at)
                VALUES (:event_id, :viewer_user_id, UTC_TIMESTAMP(6))
                ON DUPLICATE KEY UPDATE read_at = COALESCE(read_at, UTC_TIMESTAMP(6))
                """
            ),
            {"event_id": event_id, "viewer_user_id": str(user_id)},
        )
        await session.commit()
        return True


async def mark_all_notifications_read(*, user_id: int | str) -> int:
    async with main_session_context() as session:
        user_result = await session.execute(
            text(
                """
                INSERT INTO notification_user_state (event_id, user_id, read_at)
                SELECT event.event_id, :viewer_user_id, UTC_TIMESTAMP(6)
                FROM notification_event AS event
                LEFT JOIN notification_user_state AS state
                    ON state.event_id = event.event_id
                    AND state.user_id = :viewer_user_id
                WHERE event.user_id = :viewer_user_id
                    AND state.cleared_at IS NULL
                ON DUPLICATE KEY UPDATE read_at = COALESCE(read_at, UTC_TIMESTAMP(6))
                """
            ),
            {"viewer_user_id": str(user_id)},
        )
        global_result = await session.execute(
            text(
                """
                INSERT INTO notification_user_state (event_id, user_id, read_at)
                SELECT event.event_id, :viewer_user_id, UTC_TIMESTAMP(6)
                FROM notification_event AS event
                LEFT JOIN notification_user_state AS state
                    ON state.event_id = event.event_id
                    AND state.user_id = :viewer_user_id
                WHERE event.user_id IS NULL
                    AND state.cleared_at IS NULL
                ON DUPLICATE KEY UPDATE read_at = COALESCE(read_at, UTC_TIMESTAMP(6))
                """
            ),
            {"viewer_user_id": str(user_id)},
        )
        await session.commit()
        return int(user_result.rowcount or 0) + int(global_result.rowcount or 0)


async def clear_notification(*, user_id: int | str, event_id: str) -> bool:
    async with main_session_context() as session:
        exists = await session.execute(
            text(
                """
                SELECT 1
                FROM notification_event
                WHERE event_id = :event_id
                    AND user_id = :viewer_user_id
                UNION ALL
                SELECT 1
                FROM notification_event
                WHERE event_id = :event_id
                    AND user_id IS NULL
                LIMIT 1
                """
            ),
            {"event_id": event_id, "viewer_user_id": str(user_id)},
        )
        if exists.scalar_one_or_none() is None:
            return False

        await session.execute(
            text(
                """
                INSERT INTO notification_user_state (event_id, user_id, read_at, cleared_at)
                VALUES (
                    :event_id,
                    :viewer_user_id,
                    UTC_TIMESTAMP(6),
                    UTC_TIMESTAMP(6)
                )
                ON DUPLICATE KEY UPDATE
                    read_at = COALESCE(read_at, UTC_TIMESTAMP(6)),
                    cleared_at = COALESCE(cleared_at, UTC_TIMESTAMP(6))
                """
            ),
            {"event_id": event_id, "viewer_user_id": str(user_id)},
        )
        await session.commit()
        return True


async def clear_all_notifications(*, user_id: int | str) -> int:
    async with main_session_context() as session:
        user_result = await session.execute(
            text(
                """
                INSERT INTO notification_user_state (event_id, user_id, read_at, cleared_at)
                SELECT
                    event.event_id,
                    :viewer_user_id,
                    UTC_TIMESTAMP(6),
                    UTC_TIMESTAMP(6)
                FROM notification_event AS event
                LEFT JOIN notification_user_state AS state
                    ON state.event_id = event.event_id
                    AND state.user_id = :viewer_user_id
                WHERE event.user_id = :viewer_user_id
                    AND state.cleared_at IS NULL
                ON DUPLICATE KEY UPDATE
                    read_at = COALESCE(read_at, UTC_TIMESTAMP(6)),
                    cleared_at = COALESCE(cleared_at, UTC_TIMESTAMP(6))
                """
            ),
            {"viewer_user_id": str(user_id)},
        )
        global_result = await session.execute(
            text(
                """
                INSERT INTO notification_user_state (event_id, user_id, read_at, cleared_at)
                SELECT
                    event.event_id,
                    :viewer_user_id,
                    UTC_TIMESTAMP(6),
                    UTC_TIMESTAMP(6)
                FROM notification_event AS event
                LEFT JOIN notification_user_state AS state
                    ON state.event_id = event.event_id
                    AND state.user_id = :viewer_user_id
                WHERE event.user_id IS NULL
                    AND state.cleared_at IS NULL
                ON DUPLICATE KEY UPDATE
                    read_at = COALESCE(read_at, UTC_TIMESTAMP(6)),
                    cleared_at = COALESCE(cleared_at, UTC_TIMESTAMP(6))
                """
            ),
            {"viewer_user_id": str(user_id)},
        )
        await session.commit()
        return int(user_result.rowcount or 0) + int(global_result.rowcount or 0)


async def get_notification_preferences(*, user_id: int | str) -> NotificationPreferences:
    async with main_session_context() as session:
        result = await session.execute(
            text(
                """
                SELECT
                    toast_enabled,
                    desktop_enabled,
                    silent_mode,
                    minimum_toast_severity,
                    minimum_desktop_severity,
                    center_severity_filter
                FROM notification_user_preference
                WHERE user_id = :viewer_user_id
                LIMIT 1
                """
            ),
            {"viewer_user_id": str(user_id)},
        )
        row = result.mappings().first()

    if row is None:
        return NotificationPreferences()

    return NotificationPreferences(
        toast_enabled=bool(row["toast_enabled"]),
        desktop_enabled=bool(row["desktop_enabled"]),
        silent_mode=bool(row["silent_mode"]),
        minimum_toast_severity=str(row["minimum_toast_severity"]),  # type: ignore[arg-type]
        minimum_desktop_severity=str(row["minimum_desktop_severity"]),  # type: ignore[arg-type]
        center_severity_filter=str(row["center_severity_filter"]),  # type: ignore[arg-type]
    )


async def update_notification_preferences(
    *,
    user_id: int | str,
    patch: NotificationPreferencePatch,
) -> NotificationPreferences:
    current = await get_notification_preferences(user_id=user_id)
    next_preferences = current.model_copy(update=patch.model_dump(exclude_none=True))

    async with main_session_context() as session:
        await session.execute(
            text(
                """
                INSERT INTO notification_user_preference (
                    user_id,
                    toast_enabled,
                    desktop_enabled,
                    silent_mode,
                    minimum_toast_severity,
                    minimum_desktop_severity,
                    center_severity_filter
                )
                VALUES (
                    :viewer_user_id,
                    :toast_enabled,
                    :desktop_enabled,
                    :silent_mode,
                    :minimum_toast_severity,
                    :minimum_desktop_severity,
                    :center_severity_filter
                )
                ON DUPLICATE KEY UPDATE
                    toast_enabled = VALUES(toast_enabled),
                    desktop_enabled = VALUES(desktop_enabled),
                    silent_mode = VALUES(silent_mode),
                    minimum_toast_severity = VALUES(minimum_toast_severity),
                    minimum_desktop_severity = VALUES(minimum_desktop_severity),
                    center_severity_filter = VALUES(center_severity_filter)
                """
            ),
            {
                "viewer_user_id": str(user_id),
                "toast_enabled": int(next_preferences.toast_enabled),
                "desktop_enabled": int(next_preferences.desktop_enabled),
                "silent_mode": int(next_preferences.silent_mode),
                "minimum_toast_severity": next_preferences.minimum_toast_severity,
                "minimum_desktop_severity": next_preferences.minimum_desktop_severity,
                "center_severity_filter": next_preferences.center_severity_filter,
            },
        )
        await session.commit()

    return next_preferences


async def list_notification_rules(*, user_id: int | str) -> list[NotificationDeliveryRule]:
    async with main_session_context() as session:
        result = await session.execute(
            text(
                """
                SELECT
                    source,
                    event_type,
                    enabled,
                    reminder_offsets_json,
                    recipient_scope,
                    updated_at
                FROM notification_delivery_rule
                WHERE user_id = :viewer_user_id
                ORDER BY source, event_type
                """
            ),
            {"viewer_user_id": str(user_id)},
        )
        rows = [_rule_from_row(dict(row)) for row in result.mappings().all()]

    default_rule = _default_followup_rule()
    if not any(
        rule.source == default_rule.source and rule.event_type == default_rule.event_type
        for rule in rows
    ):
        rows.insert(0, default_rule)
    return rows


async def get_notification_rule(
    *,
    user_id: int | str,
    source: str,
    event_type: str,
) -> NotificationDeliveryRule:
    async with main_session_context() as session:
        result = await session.execute(
            text(
                """
                SELECT
                    source,
                    event_type,
                    enabled,
                    reminder_offsets_json,
                    recipient_scope,
                    updated_at
                FROM notification_delivery_rule
                WHERE user_id = :viewer_user_id
                    AND source = :source
                    AND event_type = :event_type
                LIMIT 1
                """
            ),
            {
                "viewer_user_id": str(user_id),
                "source": source,
                "event_type": event_type,
            },
        )
        row = result.mappings().first()

    if row is not None:
        return _rule_from_row(dict(row))
    if source == FOLLOWUP_SOURCE and event_type == FOLLOWUP_EVENT_TYPE:
        return _default_followup_rule()
    return NotificationDeliveryRule(source=source, event_type=event_type)


async def update_notification_rule(
    *,
    user_id: int | str,
    patch: NotificationRulePatch,
) -> NotificationDeliveryRule:
    current = await get_notification_rule(
        user_id=user_id,
        source=patch.source,
        event_type=patch.event_type,
    )
    next_rule = current.model_copy(update=patch.model_dump(exclude_none=True))

    async with main_session_context() as session:
        await session.execute(
            text(
                """
                INSERT INTO notification_delivery_rule (
                    user_id,
                    source,
                    event_type,
                    enabled,
                    reminder_offsets_json,
                    recipient_scope
                )
                VALUES (
                    :viewer_user_id,
                    :source,
                    :event_type,
                    :enabled,
                    :reminder_offsets_json,
                    :recipient_scope
                )
                ON DUPLICATE KEY UPDATE
                    enabled = VALUES(enabled),
                    reminder_offsets_json = VALUES(reminder_offsets_json),
                    recipient_scope = VALUES(recipient_scope)
                """
            ),
            {
                "viewer_user_id": str(user_id),
                "source": next_rule.source,
                "event_type": next_rule.event_type,
                "enabled": int(next_rule.enabled),
                "reminder_offsets_json": _offsets_json(next_rule.reminder_offsets_minutes),
                "recipient_scope": next_rule.recipient_scope,
            },
        )
        await session.commit()

    return await get_notification_rule(
        user_id=user_id,
        source=next_rule.source,
        event_type=next_rule.event_type,
    )
