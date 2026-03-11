"""GET /auth/v2/me handler.

Access tokens are stateless. Employee/role state changes are effective
at next refresh or login. This is accepted given 15m TTL.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from controllers.auth_v2.dependencies import require_v2_auth
from controllers.auth_v2.schemas.models import CurrentV2User
from controllers.auth_v2.services.common import request_id, success_json_response

router = APIRouter(prefix="/auth/v2", tags=["auth-v2"])


@router.get("/me")
async def me_v2(request: Request, current_user: CurrentV2User = Depends(require_v2_auth)):
    rid = request_id(request)
    return success_json_response(
        current_user.model_dump(),
        request_id_value=rid,
        message="Current user",
    )
