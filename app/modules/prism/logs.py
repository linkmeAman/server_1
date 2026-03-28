"""PRISM — Access Log Viewer

Routes:
  GET  /prism/logs    query the access decision log with filters
"""

from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import text

from core.database import central_session_context

router = APIRouter(prefix="/prism/logs", tags=["PRISM — Access Logs"])


def _rows(result) -> list[dict]:
    return [dict(r._mapping) for r in result.fetchall()]


@router.get("")
async def list_access_logs(
    user_id: Optional[int] = Query(None),
    decision: Optional[str] = Query(None, description="Allow | Deny"),
    action: Optional[str] = Query(None),
    resource: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Paginated access decision log. All parameters are optional filters."""
    async with central_session_context() as db:
        where_parts = ["1=1"]
        params: dict = {"limit": limit, "offset": offset}

        if user_id is not None:
            where_parts.append("user_id = :user_id")
            params["user_id"] = user_id
        if decision:
            where_parts.append("decision = :decision")
            params["decision"] = decision
        if action:
            where_parts.append("action LIKE :action")
            params["action"] = f"%{action}%"
        if resource:
            where_parts.append("resource_type LIKE :resource")
            params["resource"] = f"%{resource}%"

        where = " AND ".join(where_parts)

        count_result = await db.execute(
            text(f"SELECT COUNT(*) as total FROM prism_access_logs WHERE {where}"),
            {k: v for k, v in params.items() if k not in ("limit", "offset")},
        )
        total = count_result.fetchone()[0]

        rows = _rows(await db.execute(
            text(
                f"SELECT id, user_id, action, resource_type, resource_id, decision, matched_policy_id, "
                f"matched_statement_id, request_context_json, evaluated_at "
                f"FROM prism_access_logs WHERE {where} "
                f"ORDER BY evaluated_at DESC "
                f"LIMIT :limit OFFSET :offset"
            ),
            params,
        ))

    return {"logs": rows, "total": total, "limit": limit, "offset": offset}

