"""Refresh-session revocation routines for auth v2."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.constants import (
    ALLOWED_REVOKE_REASONS,
    EVENT_SESSION_REVOKED,
    OUTCOME_SECURITY,
)
from app.modules.auth.services.audit import write_audit_event


async def revoke_all_sessions_for_user(user_id: int, reason: str, db: AsyncSession) -> int:
    """Revoke all active auth v2 refresh rows for a user and audit once."""
    if reason not in ALLOWED_REVOKE_REASONS:
        raise ValueError(f"Invalid revoke reason: {reason}")

    now = datetime.utcnow()
    result = await db.execute(
        text(
            """
            UPDATE auth_refresh_token
            SET revoked_at = :now,
                revoke_reason = :reason
            WHERE user_id = :user_id
              AND revoked_at IS NULL
            """
        ),
        {"now": now, "reason": reason, "user_id": int(user_id)},
    )
    count = int(result.rowcount or 0)

    await write_audit_event(
        db,
        event_type=EVENT_SESSION_REVOKED,
        outcome=OUTCOME_SECURITY,
        reason_code=reason,
        user_id=int(user_id),
        details_json={"revoked_count": count, "reason": reason},
    )
    return count


async def revoke_session_family(
    *,
    user_id: int,
    employee_id: int,
    reason: str,
    db: AsyncSession,
) -> int:
    if reason not in ALLOWED_REVOKE_REASONS:
        raise ValueError(f"Invalid revoke reason: {reason}")

    now = datetime.utcnow()
    result = await db.execute(
        text(
            """
            UPDATE auth_refresh_token
            SET revoked_at = :now,
                revoke_reason = :reason
            WHERE user_id = :user_id
              AND employee_id = :employee_id
              AND revoked_at IS NULL
            """
        ),
        {
            "now": now,
            "reason": reason,
            "user_id": int(user_id),
            "employee_id": int(employee_id),
        },
    )
    count = int(result.rowcount or 0)
    await write_audit_event(
        db,
        event_type=EVENT_SESSION_REVOKED,
        outcome=OUTCOME_SECURITY,
        reason_code=reason,
        user_id=int(user_id),
        employee_id=int(employee_id),
        details_json={"revoked_count": count, "reason": reason, "scope": "session_family"},
    )
    return count

