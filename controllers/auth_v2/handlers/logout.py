"""POST /auth/logout handler."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from controllers.auth_v2.constants import (
    AUTH_SERVICE_UNAVAILABLE,
    EVENT_LOGOUT,
    OUTCOME_SUCCESS,
    REVOKE_REASON_LOGOUT,
)
from controllers.auth_v2.schemas.models import LogoutRequest
from controllers.auth_v2.services.audit import write_audit_event
from controllers.auth_v2.services.common import (
    client_ip,
    error_json_response,
    request_id,
    success_json_response,
    user_agent,
)
from controllers.auth_v2.services.session_revocation import revoke_session_family
from controllers.auth_v2.services.token_service import verify_refresh_token
from core.database_v2 import get_central_db_session

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/logout")
async def logout(
    payload: LogoutRequest,
    request: Request,
    central_db: AsyncSession = Depends(get_central_db_session),
):
    rid = request_id(request)
    ip_value = client_ip(request)
    ua_value = user_agent(request)

    user_id = None
    contact_id = None
    employee_id = None

    try:
        claims = verify_refresh_token(payload.refresh_token)
        user_id = int(claims.get("user_id"))
        contact_id = int(claims.get("contact_id"))
        employee_id = int(claims.get("employee_id"))
    except Exception:
        # Idempotent success even for invalid/unresolvable token payload.
        return success_json_response(
            {"revoked": 0},
            request_id_value=rid,
            message="Logout successful",
        )

    try:
        async with central_db.begin():
            token_hash = None
            try:
                from controllers.auth_v2.services.common import refresh_token_hash

                token_hash = refresh_token_hash(payload.refresh_token)
            except Exception:
                token_hash = None

            if token_hash:
                row_result = await central_db.execute(
                    text(
                        """
                        SELECT id
                        FROM auth_refresh_token
                        WHERE user_id = :user_id
                          AND employee_id = :employee_id
                          AND token_hash = :token_hash
                        LIMIT 1
                        FOR UPDATE
                        """
                    ),
                    {
                        "user_id": int(user_id),
                        "employee_id": int(employee_id),
                        "token_hash": token_hash,
                    },
                )
                row = row_result.fetchone()
                if row is not None:
                    await central_db.execute(
                        text(
                            """
                            UPDATE auth_refresh_token
                            SET revoked_at = :revoked_at,
                                revoke_reason = :revoke_reason
                            WHERE id = :id
                            """
                        ),
                        {
                            "revoked_at": datetime.utcnow(),
                            "revoke_reason": REVOKE_REASON_LOGOUT,
                            "id": int(row._mapping["id"]),
                        },
                    )

            revoked_count = await revoke_session_family(
                user_id=int(user_id),
                employee_id=int(employee_id),
                reason=REVOKE_REASON_LOGOUT,
                db=central_db,
            )

            await write_audit_event(
                central_db,
                event_type=EVENT_LOGOUT,
                outcome=OUTCOME_SUCCESS,
                reason_code=REVOKE_REASON_LOGOUT,
                user_id=int(user_id),
                contact_id=int(contact_id),
                employee_id=int(employee_id),
                ip=ip_value,
                user_agent=ua_value,
                request_id=rid,
                details_json={"revoked_count": int(revoked_count)},
            )

        return success_json_response(
            {"revoked": int(revoked_count)},
            request_id_value=rid,
            message="Logout successful",
        )
    except Exception:
        return error_json_response(
            AUTH_SERVICE_UNAVAILABLE,
            "Auth v2 service unavailable",
            503,
            rid,
            details={},
        )
