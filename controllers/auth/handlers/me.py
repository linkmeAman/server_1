"""GET /auth/me handler.

Access tokens are stateless. Employee/role state changes are effective
at next refresh or login. This is accepted given 15m TTL.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from controllers.auth.dependencies import require_auth
from controllers.auth.schemas.models import CurrentV2User
from controllers.auth.services.common import request_id, success_json_response

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
async def me(request: Request, current_user: CurrentV2User = Depends(require_auth)):
    rid = request_id(request)
    return success_json_response(
        current_user.model_dump(),
        request_id_value=rid,
        message="Current user",
    )
