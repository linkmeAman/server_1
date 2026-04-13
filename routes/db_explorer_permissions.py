"""PRISM authorization dependency for DB Explorer endpoints."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.core.database import central_session_context
from app.core.prism_guard import CallerContext, require_any_caller
from app.core.prism_pdp import PDPRequest, evaluate

_DB_EXPLORER_ACTION = "db-explorer:read"
_DB_EXPLORER_RESOURCE_TYPE = "db-explorer"
_DB_EXPLORER_RESOURCE_ID = "*"


async def require_db_explorer_access(
    request: Request,
    caller: CallerContext = Depends(require_any_caller),
) -> CallerContext:
    """Allow only callers explicitly authorized for DB Explorer access."""

    # Supreme users bypass PRISM policy checks by design.
    if caller.is_super:
        return caller

    request_context = {
        "path": request.url.path,
        "method": request.method,
    }
    if request.client and request.client.host:
        request_context["sourceIp"] = request.client.host

    try:
        async with central_session_context() as db:
            result = await evaluate(
                PDPRequest(
                    user_id=caller.user_id,
                    action=_DB_EXPLORER_ACTION,
                    resource_type=_DB_EXPLORER_RESOURCE_TYPE,
                    resource_id=_DB_EXPLORER_RESOURCE_ID,
                    request_context=request_context,
                ),
                db,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not verify DB Explorer permission: {exc}",
        ) from exc

    if result.decision != "Allow":
        raise HTTPException(status_code=403, detail="DB Explorer access denied by PRISM policy")

    return caller
