"""POST /auth/refresh handler."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from controllers.auth.constants import (
    AUTH_EMPLOYEE_INACTIVE,
    AUTH_INVALID_TOKEN,
    AUTH_REFRESH_REPLAY_DETECTED,
    AUTH_SERVICE_UNAVAILABLE,
    AUTH_SESSION_BINDING_FAILED,
    AUTH_SUPREME_USER_NOT_FOUND,
    EVENT_REFRESH,
    EVENT_REFRESH_SESSION_FAMILY_WIPE,
    OUTCOME_FAILURE,
    OUTCOME_SECURITY,
    OUTCOME_SUCCESS,
    REVOKE_REASON_EMPLOYEE_INACTIVE,
    REVOKE_REASON_REPLAY,
    REVOKE_REASON_SESSION_FAMILY_WIPE,
)
from controllers.auth.schemas.models import RefreshRequest
from controllers.auth.services.audit import write_audit_event
from controllers.auth.services.authorization import AuthorizationResolver
from controllers.auth.services.common import (
    AuthError,
    client_ip,
    error_json_response,
    refresh_token_hash,
    request_id,
    success_json_response,
    user_agent,
    utcnow,
)
from controllers.auth.services.device_fingerprint import compute_device_fingerprint
from controllers.auth.services.session_revocation import revoke_session_family
from controllers.auth.services.token_service import issue_token_pair, verify_refresh_token
from core.database_v2 import get_central_db_session, get_main_db_session
from core.settings import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


async def _active_employee(main_db: AsyncSession, employee_id: int) -> bool:
    result = await main_db.execute(
        text(
            """
            SELECT id
            FROM employee
            WHERE id = :employee_id
              AND status = 1
              AND (park IS NULL OR park = 0)
            LIMIT 1
            """
        ),
        {"employee_id": int(employee_id)},
    )
    return result.fetchone() is not None


async def _active_supreme_user(central_db: AsyncSession, user_id: int) -> bool:
    result = await central_db.execute(
        text(
            """
            SELECT id
            FROM auth_supreme_user
            WHERE id = :user_id
              AND is_active = 1
            LIMIT 1
            """
        ),
        {"user_id": int(user_id)},
    )
    return result.fetchone() is not None


@router.post("/refresh")
async def refresh(
    payload: RefreshRequest,
    request: Request,
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    rid = request_id(request)
    ip_value = client_ip(request)
    ua_value = user_agent(request)

    try:
        claims = verify_refresh_token(payload.refresh_token)
    except AuthError as exc:
        return error_json_response(exc.code, exc.message, exc.status_code, rid, details=exc.details)
    except Exception:
        return error_json_response(AUTH_INVALID_TOKEN, "Invalid refresh token", 401, rid, details={})

    token_hash = refresh_token_hash(payload.refresh_token)
    token_jti = str(claims.get("jti"))
    user_id = int(claims.get("user_id"))
    contact_id = int(claims.get("contact_id"))
    employee_id = int(claims.get("employee_id"))
    supreme_user = bool(claims.get("supreme_user", False))

    pending_error: Optional[AuthError] = None
    token_pair = None
    authz = None

    try:
        async with central_db.begin():
            result = await central_db.execute(
                text(
                    """
                    SELECT id, user_id, contact_id, employee_id, token_jti, token_hash,
                           used_at, revoked_at, issued_device_fingerprint_hash
                    FROM auth_refresh_token
                    WHERE token_hash = :token_hash
                      AND token_jti = :token_jti
                    LIMIT 1
                    FOR UPDATE
                    """
                ),
                {"token_hash": token_hash, "token_jti": token_jti},
            )
            row = result.fetchone()
            if row is None:
                pending_error = AuthError(AUTH_INVALID_TOKEN, "Invalid refresh token", 401)
            else:
                token_row = dict(row._mapping)
                if token_row.get("used_at") is not None or token_row.get("revoked_at") is not None:
                    now = datetime.utcnow()
                    await central_db.execute(
                        text(
                            """
                            UPDATE auth_refresh_token
                            SET revoked_at = COALESCE(revoked_at, :revoked_at),
                                revoke_reason = :revoke_reason
                            WHERE id = :id
                            """
                        ),
                        {
                            "revoked_at": now,
                            "revoke_reason": REVOKE_REASON_REPLAY,
                            "id": int(token_row["id"]),
                        },
                    )
                    await revoke_session_family(
                        user_id=user_id,
                        employee_id=employee_id,
                        reason=REVOKE_REASON_SESSION_FAMILY_WIPE,
                        db=central_db,
                    )
                    await write_audit_event(
                        central_db,
                        event_type=EVENT_REFRESH_SESSION_FAMILY_WIPE,
                        outcome=OUTCOME_SECURITY,
                        reason_code=AUTH_REFRESH_REPLAY_DETECTED,
                        user_id=user_id,
                        employee_id=employee_id,
                        contact_id=contact_id,
                        ip=ip_value,
                        user_agent=ua_value,
                        request_id=rid,
                        details_json={"token_id": int(token_row["id"])},
                    )
                    pending_error = AuthError(
                        AUTH_REFRESH_REPLAY_DETECTED,
                        "Refresh replay detected",
                        401,
                    )
                else:
                    presented_fingerprint = compute_device_fingerprint(request)
                    issued_fingerprint = str(token_row.get("issued_device_fingerprint_hash") or "")
                    if issued_fingerprint and issued_fingerprint != presented_fingerprint:
                        await write_audit_event(
                            central_db,
                            event_type=EVENT_REFRESH,
                            outcome=OUTCOME_SECURITY,
                            reason_code=AUTH_SESSION_BINDING_FAILED,
                            user_id=user_id,
                            employee_id=employee_id,
                            contact_id=contact_id,
                            ip=ip_value,
                            user_agent=ua_value,
                            request_id=rid,
                            details_json={"token_id": int(token_row["id"])},
                        )
                        pending_error = AuthError(
                            AUTH_SESSION_BINDING_FAILED,
                            "Session binding check failed",
                            401,
                        )
                    elif supreme_user and not await _active_supreme_user(central_db, user_id):
                        now = datetime.utcnow()
                        await central_db.execute(
                            text(
                                """
                                UPDATE auth_refresh_token
                                SET revoked_at = :now,
                                    revoke_reason = :reason
                                WHERE id = :id
                                """
                            ),
                            {
                                "now": now,
                                "reason": REVOKE_REASON_EMPLOYEE_INACTIVE,
                                "id": int(token_row["id"]),
                            },
                        )
                        await write_audit_event(
                            central_db,
                            event_type=EVENT_REFRESH,
                            outcome=OUTCOME_FAILURE,
                            reason_code=AUTH_SUPREME_USER_NOT_FOUND,
                            user_id=user_id,
                            employee_id=employee_id,
                            contact_id=contact_id,
                            ip=ip_value,
                            user_agent=ua_value,
                            request_id=rid,
                            details_json={"token_id": int(token_row["id"]), "supreme_user": True},
                        )
                        pending_error = AuthError(
                            AUTH_SUPREME_USER_NOT_FOUND,
                            "Supreme user is inactive",
                            401,
                        )
                    elif not supreme_user and not await _active_employee(main_db, employee_id):
                        now = datetime.utcnow()
                        await central_db.execute(
                            text(
                                """
                                UPDATE auth_refresh_token
                                SET revoked_at = :now,
                                    revoke_reason = :reason
                                WHERE id = :id
                                """
                            ),
                            {
                                "now": now,
                                "reason": REVOKE_REASON_EMPLOYEE_INACTIVE,
                                "id": int(token_row["id"]),
                            },
                        )
                        await write_audit_event(
                            central_db,
                            event_type=EVENT_REFRESH,
                            outcome=OUTCOME_FAILURE,
                            reason_code=AUTH_EMPLOYEE_INACTIVE,
                            user_id=user_id,
                            employee_id=employee_id,
                            contact_id=contact_id,
                            ip=ip_value,
                            user_agent=ua_value,
                            request_id=rid,
                            details_json={"token_id": int(token_row["id"])},
                        )
                        pending_error = AuthError(AUTH_EMPLOYEE_INACTIVE, "Employee is inactive", 403)
                    else:
                        if supreme_user:
                            authz = {
                                "roles": [{"role_code": "SUPREME", "role_name": "Supreme User"}],
                                "position_id": None,
                                "position": None,
                                "department_id": None,
                                "department": None,
                                "permissions": ["global:super"],
                                "is_super": True,
                                "permissions_version": 1,
                                "permissions_schema_version": 1,
                            }
                        else:
                            authz = await AuthorizationResolver(main_db, central_db).resolve_employee_authorization(
                                employee_id
                            )
                        token_pair = issue_token_pair(
                            user_id=user_id,
                            contact_id=contact_id,
                            employee_id=employee_id,
                            roles=authz["roles"],
                            mobile=str(claims.get("mobile", "")),
                            authorization=authz,
                            extra_claims={"supreme_user": True} if supreme_user else None,
                        )

                        now = utcnow()
                        await central_db.execute(
                            text(
                                """
                                UPDATE auth_refresh_token
                                SET used_at = :used_at,
                                    last_ip = :last_ip,
                                    last_user_agent = :last_user_agent,
                                    last_used_at = :last_used_at
                                WHERE id = :id
                                """
                            ),
                            {
                                "used_at": now.replace(tzinfo=None),
                                "last_ip": ip_value,
                                "last_user_agent": ua_value,
                                "last_used_at": now.replace(tzinfo=None),
                                "id": int(token_row["id"]),
                            },
                        )

                        refresh_expiry = now + timedelta(days=int(get_settings().AUTH_V2_REFRESH_TOKEN_DAYS))
                        await central_db.execute(
                            text(
                                """
                                INSERT INTO auth_refresh_token (
                                    user_id, contact_id, employee_id, token_jti, token_hash,
                                    issued_at, expires_at, used_at, revoked_at,
                                    rotated_from_id, revoke_reason,
                                    issued_ip, issued_user_agent, issued_device_fingerprint_hash,
                                    last_ip, last_user_agent, last_used_at, created_at
                                ) VALUES (
                                    :user_id, :contact_id, :employee_id, :token_jti, :token_hash,
                                    :issued_at, :expires_at, NULL, NULL,
                                    :rotated_from_id, NULL,
                                    :issued_ip, :issued_user_agent, :issued_device_fingerprint_hash,
                                    :last_ip, :last_user_agent, NULL, :created_at
                                )
                                """
                            ),
                            {
                                "user_id": user_id,
                                "contact_id": contact_id,
                                "employee_id": employee_id,
                                "token_jti": token_pair["jti"],
                                "token_hash": refresh_token_hash(token_pair["refresh_token"]),
                                "issued_at": now.replace(tzinfo=None),
                                "expires_at": refresh_expiry.replace(tzinfo=None),
                                "rotated_from_id": int(token_row["id"]),
                                "issued_ip": ip_value,
                                "issued_user_agent": ua_value,
                                "issued_device_fingerprint_hash": presented_fingerprint,
                                "last_ip": ip_value,
                                "last_user_agent": ua_value,
                                "created_at": now.replace(tzinfo=None),
                            },
                        )

                        await write_audit_event(
                            central_db,
                            event_type=EVENT_REFRESH,
                            outcome=OUTCOME_SUCCESS,
                            reason_code=None,
                            user_id=user_id,
                            employee_id=employee_id,
                            contact_id=contact_id,
                            ip=ip_value,
                            user_agent=ua_value,
                            request_id=rid,
                            details_json={
                                "roles_count": len(authz["roles"]),
                                "permissions_count": len(authz["permissions"]),
                                "is_super": bool(authz["is_super"]),
                            },
                        )

        if pending_error is not None:
            if pending_error.code == AUTH_INVALID_TOKEN:
                await write_audit_event(
                    central_db,
                    event_type=EVENT_REFRESH,
                    outcome=OUTCOME_FAILURE,
                    reason_code=AUTH_INVALID_TOKEN,
                    user_id=user_id,
                    employee_id=employee_id,
                    contact_id=contact_id,
                    ip=ip_value,
                    user_agent=ua_value,
                    request_id=rid,
                    details_json={},
                )
                await central_db.commit()
            return error_json_response(
                pending_error.code,
                pending_error.message,
                pending_error.status_code,
                rid,
                details=pending_error.details,
            )

        if token_pair is None or authz is None:
            return error_json_response(
                AUTH_SERVICE_UNAVAILABLE,
                "Auth v2 service unavailable",
                503,
                rid,
                details={},
            )

        return success_json_response(
            {
                "access_token": token_pair["access_token"],
                "refresh_token": token_pair["refresh_token"],
                "token_type": "Bearer",
                "user_id": user_id,
                "contact_id": contact_id,
                "employee_id": employee_id,
                "roles": authz["roles"],
                "position_id": authz["position_id"],
                "position": authz["position"],
                "department_id": authz["department_id"],
                "department": authz["department"],
                "permissions": authz["permissions"],
                "is_super": authz["is_super"],
                "permissions_version": authz["permissions_version"],
                "permissions_schema_version": authz["permissions_schema_version"],
            },
            request_id_value=rid,
            message="Token refreshed",
        )
    except AuthError as exc:
        return error_json_response(exc.code, exc.message, exc.status_code, rid, details=exc.details)
    except Exception:
        try:
            await write_audit_event(
                central_db,
                event_type=EVENT_REFRESH,
                outcome=OUTCOME_FAILURE,
                reason_code=AUTH_SERVICE_UNAVAILABLE,
                user_id=user_id,
                employee_id=employee_id,
                contact_id=contact_id,
                ip=ip_value,
                user_agent=ua_value,
                request_id=rid,
                details_json={},
            )
            await central_db.commit()
        except Exception:
            await central_db.rollback()
        return error_json_response(
            AUTH_SERVICE_UNAVAILABLE,
            "Auth v2 service unavailable",
            503,
            rid,
            details={},
        )
