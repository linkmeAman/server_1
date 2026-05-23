"""Authorization dependency for the NL2SQL integration routes."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.core.database import central_session_context
from app.core.prism_guard import CallerContext, require_any_caller
from app.core.prism_pdp import PDPRequest, evaluate

_NL2SQL_ACTION = "ai-chat:read"
_NL2SQL_RESOURCE_TYPE = "ai-chat"
_NL2SQL_RESOURCE_ID = "*"


async def require_nl2sql_access(
    request: Request,
    caller: CallerContext = Depends(require_any_caller),
) -> CallerContext:
    """Allow only callers explicitly authorized for NL2SQL access."""

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
                    action=_NL2SQL_ACTION,
                    resource_type=_NL2SQL_RESOURCE_TYPE,
                    resource_id=_NL2SQL_RESOURCE_ID,
                    request_context=request_context,
                ),
                db,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not verify NL2SQL permission: {exc}",
        ) from exc

    if result.decision != "Allow":
        raise HTTPException(status_code=403, detail="NL2SQL access denied by PRISM policy")

    return caller

