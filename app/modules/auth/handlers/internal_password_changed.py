"""POST /internal/auth/events/password-changed.

Deployment note: this endpoint must be exposed only on internal network paths.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.constants import (
    AUTH_FORBIDDEN,
    AUTH_SERVICE_UNAVAILABLE,
    EVENT_PASSWORD_CHANGED_WEBHOOK,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    REVOKE_REASON_PASSWORD_CHANGE,
)
from app.modules.auth.schemas.models import PasswordChangedRequest
from app.modules.auth.services.audit import write_audit_event
from app.modules.auth.services.common import (
    AuthError,
    client_ip,
    error_json_response,
    request_id,
    success_json_response,
    user_agent,
)
from app.modules.auth.services.session_revocation import revoke_all_sessions_for_user
from app.core.database import get_central_db_session
from app.core.settings import get_settings

router = APIRouter(prefix="/internal/auth/events", tags=["auth-internal"])


@router.post("/password-changed")
async def internal_password_changed(
    payload: PasswordChangedRequest,
    request: Request,
    api_key: str | None = Header(default=None, alias="X-API-Key"),
    internal_caller: str | None = Header(default=None, alias="X-Internal-Caller"),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    rid = request_id(request)
    caller = (internal_caller or client_ip(request) or "unknown").strip()[:128]

    try:
        settings = get_settings()
        # Additional explicit guard for internal callers, even if global middleware is disabled.
        if not api_key or api_key not in settings.API_KEYS:
            raise AuthError(AUTH_FORBIDDEN, "Forbidden", 403)

        async with central_db.begin():
            revoked_count = await revoke_all_sessions_for_user(
                int(payload.user_id),
                REVOKE_REASON_PASSWORD_CHANGE,
                central_db,
            )
            await write_audit_event(
                central_db,
                event_type=EVENT_PASSWORD_CHANGED_WEBHOOK,
                outcome=OUTCOME_SUCCESS,
                reason_code=REVOKE_REASON_PASSWORD_CHANGE,
                user_id=int(payload.user_id),
                ip=client_ip(request),
                user_agent=user_agent(request),
                request_id=rid,
                details_json={
                    "caller": caller,
                    "reason": payload.reason or REVOKE_REASON_PASSWORD_CHANGE,
                    "revoked_count": int(revoked_count),
                    "guard": "x-api-key",
                },
            )

        return success_json_response(
            {
                "user_id": int(payload.user_id),
                "revoked": int(revoked_count),
                "caller": caller,
            },
            request_id_value=rid,
            message="Password change processed",
        )
    except AuthError as exc:
        try:
            await write_audit_event(
                central_db,
                event_type=EVENT_PASSWORD_CHANGED_WEBHOOK,
                outcome=OUTCOME_FAILURE,
                reason_code=exc.code,
                user_id=int(payload.user_id),
                ip=client_ip(request),
                user_agent=user_agent(request),
                request_id=rid,
                details_json={"caller": caller},
            )
            await central_db.commit()
        except Exception:
            await central_db.rollback()
        return error_json_response(exc.code, exc.message, exc.status_code, rid, details={})
    except Exception:
        await central_db.rollback()
        return error_json_response(
            AUTH_SERVICE_UNAVAILABLE,
            "Auth v2 service unavailable",
            503,
            rid,
            details={},
        )


