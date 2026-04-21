"""Refresh-session revocation routines for auth v2."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import bindparam, text
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


async def _load_token_node(db: AsyncSession, token_id: int) -> dict | None:
    result = await db.execute(
        text(
            """
            SELECT id, user_id, employee_id, rotated_from_id
            FROM auth_refresh_token
            WHERE id = :id
            LIMIT 1
            """
        ),
        {"id": int(token_id)},
    )
    row = result.fetchone()
    return dict(row._mapping) if row is not None else None


async def _resolve_chain_root_id(db: AsyncSession, anchor_token_id: int) -> int | None:
    node = await _load_token_node(db, anchor_token_id)
    if node is None:
        return None

    current_id = int(node["id"])
    seen = {current_id}
    parent_id = node.get("rotated_from_id")

    while parent_id is not None:
        parent_int = int(parent_id)
        if parent_int in seen:
            break
        seen.add(parent_int)

        parent_node = await _load_token_node(db, parent_int)
        if parent_node is None:
            break

        current_id = int(parent_node["id"])
        parent_id = parent_node.get("rotated_from_id")

    return current_id


async def _collect_chain_ids(db: AsyncSession, root_token_id: int) -> list[int]:
    pending = [int(root_token_id)]
    seen: set[int] = set()

    while pending:
        current = pending.pop(0)
        if current in seen:
            continue
        seen.add(current)

        child_rows = await db.execute(
            text(
                """
                SELECT id
                FROM auth_refresh_token
                WHERE rotated_from_id = :rotated_from_id
                """
            ),
            {"rotated_from_id": current},
        )
        for row in child_rows.fetchall():
            child_id = int(row._mapping["id"])
            if child_id not in seen:
                pending.append(child_id)

    return list(seen)


async def revoke_session_chain(
    *,
    anchor_token_id: int,
    reason: str,
    db: AsyncSession,
    user_id: int | None = None,
    employee_id: int | None = None,
) -> int:
    """Revoke only the refresh-token chain containing the anchor token."""
    if reason not in ALLOWED_REVOKE_REASONS:
        raise ValueError(f"Invalid revoke reason: {reason}")

    root_id = await _resolve_chain_root_id(db, anchor_token_id)
    if root_id is None:
        return 0

    token_ids = await _collect_chain_ids(db, root_id)
    if not token_ids:
        return 0

    now = datetime.utcnow()
    statement = text(
        """
        UPDATE auth_refresh_token
        SET revoked_at = :now,
            revoke_reason = :reason
        WHERE id IN :token_ids
          AND revoked_at IS NULL
        """
    ).bindparams(bindparam("token_ids", expanding=True))
    result = await db.execute(
        statement,
        {"now": now, "reason": reason, "token_ids": token_ids},
    )
    count = int(result.rowcount or 0)

    await write_audit_event(
        db,
        event_type=EVENT_SESSION_REVOKED,
        outcome=OUTCOME_SECURITY,
        reason_code=reason,
        user_id=int(user_id) if user_id is not None else None,
        employee_id=int(employee_id) if employee_id is not None else None,
        details_json={
            "revoked_count": count,
            "reason": reason,
            "scope": "session_chain",
            "anchor_token_id": int(anchor_token_id),
            "root_token_id": int(root_id),
        },
    )
    return count

