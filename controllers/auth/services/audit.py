"""Audit event storage and query helpers for auth v2."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession


async def write_audit_event(
    db: AsyncSession,
    *,
    event_type: str,
    outcome: str,
    reason_code: Optional[str] = None,
    country_code: Optional[str] = None,
    mobile: Optional[str] = None,
    contact_id: Optional[int] = None,
    employee_id: Optional[int] = None,
    user_id: Optional[int] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    request_id: Optional[str] = None,
    details_json: Optional[Dict[str, Any]] = None,
) -> None:
    serialized_details = None
    if details_json is not None:
        # `text()` queries do not auto-coerce dict -> JSON for DBAPI binds.
        serialized_details = json.dumps(details_json, separators=(",", ":"), ensure_ascii=False)

    await db.execute(
        text(
            """
            INSERT INTO auth_audit_event_v2 (
                event_type, outcome, reason_code, country_code, mobile,
                contact_id, employee_id, user_id, ip, user_agent,
                request_id, details_json, created_at
            ) VALUES (
                :event_type, :outcome, :reason_code, :country_code, :mobile,
                :contact_id, :employee_id, :user_id, :ip, :user_agent,
                :request_id, :details_json, :created_at
            )
            """
        ),
        {
            "event_type": event_type,
            "outcome": outcome,
            "reason_code": reason_code,
            "country_code": country_code,
            "mobile": mobile,
            "contact_id": contact_id,
            "employee_id": employee_id,
            "user_id": user_id,
            "ip": ip,
            "user_agent": user_agent,
            "request_id": request_id,
            "details_json": serialized_details,
            "created_at": datetime.utcnow(),
        },
    )


async def count_events(
    db: AsyncSession,
    *,
    event_types: Iterable[str],
    created_after: datetime,
    outcome: Optional[str] = None,
    ip: Optional[str] = None,
    country_code: Optional[str] = None,
    mobile: Optional[str] = None,
) -> int:
    params: Dict[str, Any] = {
        "event_types": tuple(event_types),
        "created_after": created_after,
    }
    where_clauses = ["event_type IN :event_types", "created_at >= :created_after"]

    if outcome is not None:
        where_clauses.append("outcome = :outcome")
        params["outcome"] = outcome
    if ip is not None:
        where_clauses.append("ip = :ip")
        params["ip"] = ip
    if country_code is not None:
        where_clauses.append("country_code = :country_code")
        params["country_code"] = country_code
    if mobile is not None:
        where_clauses.append("mobile = :mobile")
        params["mobile"] = mobile

    query = text(
        f"""
        SELECT COUNT(1) AS total
        FROM auth_audit_event_v2
        WHERE {' AND '.join(where_clauses)}
        """
    ).bindparams(bindparam("event_types", expanding=True))
    result = await db.execute(query, params)
    value = result.scalar()
    return int(value or 0)
