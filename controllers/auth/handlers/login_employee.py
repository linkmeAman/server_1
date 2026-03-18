"""POST /auth/login-employee handler."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from controllers.auth.constants import (
    AUTH_EMPLOYEE_INACTIVE,
    AUTH_EMPLOYEE_USER_MAPPING_MISSING,
    AUTH_FLOW_DISABLED,
    AUTH_IDENTITY_MISMATCH,
    AUTH_INVALID_CREDENTIALS,
    AUTH_LOGIN_COOLDOWN,
    AUTH_PASSWORD_MIGRATION_DEFERRED,
    AUTH_SERVICE_UNAVAILABLE,
    EVENT_LOGIN_EMPLOYEE,
    EVENT_LOGIN_EMPLOYEE_LOCKED,
    EVENT_LOGIN_PASSWORD_MIGRATION,
    LOCK_KEY_TYPE_LOGIN_EMPLOYEE,
    OUTCOME_FAILURE,
    OUTCOME_SECURITY,
    OUTCOME_SUCCESS,
)
from controllers.auth.schemas.models import LoginEmployeeRequest
from controllers.auth.services.audit import write_audit_event
from controllers.auth.services.authorization import AuthorizationResolver
from controllers.auth.services.common import (
    AuthError,
    client_ip,
    error_json_response,
    refresh_token_hash,
    request_id,
    sha256_hex,
    success_json_response,
    user_agent,
    utcnow,
)
from controllers.auth.services.device_fingerprint import compute_device_fingerprint
from controllers.auth.services.token_service import issue_token_pair
from core.database_v2 import get_central_db_session, get_main_db_session
from core.security import hash_password, verify_password
from core.settings import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _lock_key_hash(country_code: str, mobile: str, employee_id: int) -> str:
    return sha256_hex(f"{country_code.strip()}|{mobile.strip()}|{int(employee_id)}")


async def _load_lock_state(db: AsyncSession, key_hash: str) -> Optional[Dict[str, Any]]:
    result = await db.execute(
        text(
            """
            SELECT id, fail_count, first_fail_at, last_fail_at, locked_until
            FROM auth_lock_state_v2
            WHERE key_type = :key_type AND key_hash = :key_hash
            LIMIT 1
            """
        ),
        {"key_type": LOCK_KEY_TYPE_LOGIN_EMPLOYEE, "key_hash": key_hash},
    )
    row = result.fetchone()
    return dict(row._mapping) if row else None


async def _record_failed_attempt(
    *,
    db: AsyncSession,
    key_hash: str,
    country_code: str,
    mobile: str,
    employee_id: int,
) -> Dict[str, Any]:
    settings = get_settings()
    now = datetime.utcnow()
    window_minutes = int(settings.AUTH_V2_LOGIN_FAIL_WINDOW_MINUTES)
    threshold = int(settings.AUTH_V2_LOGIN_FAIL_THRESHOLD)
    cooldown_minutes = int(settings.AUTH_V2_LOGIN_COOLDOWN_MINUTES)

    state = await _load_lock_state(db, key_hash)
    if state is None:
        await db.execute(
            text(
                """
                INSERT INTO auth_lock_state_v2 (
                    key_type, country_code, mobile, employee_id, key_hash,
                    fail_count, first_fail_at, last_fail_at, locked_until, created_at, modified_at
                ) VALUES (
                    :key_type, :country_code, :mobile, :employee_id, :key_hash,
                    :fail_count, :first_fail_at, :last_fail_at, :locked_until, :created_at, :modified_at
                )
                """
            ),
            {
                "key_type": LOCK_KEY_TYPE_LOGIN_EMPLOYEE,
                "country_code": country_code,
                "mobile": mobile,
                "employee_id": int(employee_id),
                "key_hash": key_hash,
                "fail_count": 1,
                "first_fail_at": now,
                "last_fail_at": now,
                "locked_until": None,
                "created_at": now,
                "modified_at": now,
            },
        )
        return {"fail_count": 1, "locked_until": None}

    first_fail_at = state.get("first_fail_at")
    fail_count = int(state.get("fail_count") or 0)
    if first_fail_at is None or first_fail_at < now - timedelta(minutes=window_minutes):
        fail_count = 1
        first_fail_at = now
    else:
        fail_count += 1

    locked_until = None
    if fail_count >= threshold:
        locked_until = now + timedelta(minutes=cooldown_minutes)

    await db.execute(
        text(
            """
            UPDATE auth_lock_state_v2
            SET fail_count = :fail_count,
                first_fail_at = :first_fail_at,
                last_fail_at = :last_fail_at,
                locked_until = :locked_until,
                modified_at = :modified_at
            WHERE key_type = :key_type AND key_hash = :key_hash
            """
        ),
        {
            "fail_count": fail_count,
            "first_fail_at": first_fail_at,
            "last_fail_at": now,
            "locked_until": locked_until,
            "modified_at": now,
            "key_type": LOCK_KEY_TYPE_LOGIN_EMPLOYEE,
            "key_hash": key_hash,
        },
    )
    return {"fail_count": fail_count, "locked_until": locked_until}


async def _reset_lock_state(db: AsyncSession, key_hash: str) -> None:
    await db.execute(
        text(
            """
            UPDATE auth_lock_state_v2
            SET fail_count = 0,
                first_fail_at = NULL,
                last_fail_at = NULL,
                locked_until = NULL,
                modified_at = :modified_at
            WHERE key_type = :key_type AND key_hash = :key_hash
            """
        ),
        {
            "modified_at": datetime.utcnow(),
            "key_type": LOCK_KEY_TYPE_LOGIN_EMPLOYEE,
            "key_hash": key_hash,
        },
    )


async def _resolve_main_identity(
    main_db: AsyncSession,
    country_code: str,
    mobile: str,
    employee_id: int,
) -> Dict[str, Any]:
    contact_result = await main_db.execute(
        text(
            """
            SELECT id, country_code, mobile, fname, mname, lname
            FROM contact
            WHERE country_code = :country_code
              AND mobile = :mobile
              AND (park IS NULL OR park = 0)
            """
        ),
        {"country_code": country_code, "mobile": mobile},
    )
    contacts = [dict(row._mapping) for row in contact_result.fetchall()]
    if len(contacts) != 1:
        raise AuthError(AUTH_EMPLOYEE_USER_MAPPING_MISSING, "Employee-user mapping missing", 401)

    employee_result = await main_db.execute(
        text(
            """
            SELECT id, contact_id, status
            FROM employee
            WHERE id = :employee_id
              AND (park IS NULL OR park = 0)
            LIMIT 1
            """
        ),
        {"employee_id": int(employee_id)},
    )
    employee = employee_result.fetchone()
    if employee is None or int(employee._mapping.get("status") or 0) != 1:
        raise AuthError(AUTH_EMPLOYEE_INACTIVE, "Employee is inactive", 403)

    employee_row = dict(employee._mapping)
    contact = contacts[0]
    if int(employee_row.get("contact_id") or 0) != int(contact["id"]):
        raise AuthError(AUTH_IDENTITY_MISMATCH, "Employee does not belong to contact", 401)

    return {"contact": contact, "employee": employee_row}


async def _resolve_central_identity(
    central_db: AsyncSession,
    contact_id: int,
    employee_id: int,
    country_code: str,
) -> Dict[str, Any]:
    mapping_result = await central_db.execute(
        text(
            """
            SELECT id, contact_id, employee_id, user_id, is_active
            FROM auth_employee_user_map
            WHERE employee_id = :employee_id
            LIMIT 1
            """
        ),
        {"employee_id": int(employee_id)},
    )
    mapping_row = mapping_result.fetchone()
    if mapping_row is None:
        raise AuthError(AUTH_EMPLOYEE_USER_MAPPING_MISSING, "Employee-user mapping missing", 401)

    mapping = dict(mapping_row._mapping)
    if int(mapping.get("is_active") or 0) != 1:
        raise AuthError(AUTH_EMPLOYEE_USER_MAPPING_MISSING, "Employee-user mapping missing", 401)

    if int(mapping.get("contact_id") or 0) != int(contact_id):
        raise AuthError(AUTH_IDENTITY_MISMATCH, "Contact mismatch for employee mapping", 401)

    user_result = await central_db.execute(
        text(
            """
            SELECT id, contact_id, country_code, password, inactive
            FROM user
            WHERE id = :user_id
              AND (park IS NULL OR park = 0)
            LIMIT 1
            """
        ),
        {"user_id": int(mapping["user_id"])},
    )
    user_row = user_result.fetchone()
    if user_row is None:
        raise AuthError(AUTH_EMPLOYEE_USER_MAPPING_MISSING, "Employee-user mapping missing", 401)

    user = dict(user_row._mapping)
    if int(user.get("inactive") or 0) != 0:
        raise AuthError(AUTH_EMPLOYEE_USER_MAPPING_MISSING, "Employee-user mapping missing", 401)

    if int(user.get("contact_id") or 0) != int(contact_id):
        raise AuthError(AUTH_IDENTITY_MISMATCH, "User/contact mismatch", 401)

    user_country_code = str(user.get("country_code") or "").strip()
    if user_country_code and user_country_code != country_code:
        raise AuthError(AUTH_IDENTITY_MISMATCH, "Country code mismatch", 401)

    return {"mapping": mapping, "user": user}


async def _validate_password_and_maybe_migrate(
    central_db: AsyncSession,
    *,
    user_id: int,
    legacy_plain_password: str,
    provided_password: str,
    request_id_value: str,
    ip_value: str,
    ua_value: str,
) -> bool:
    identity_result = await central_db.execute(
        text(
            """
            SELECT user_id, password_hash
            FROM auth_identity
            WHERE user_id = :user_id
            LIMIT 1
            """
        ),
        {"user_id": int(user_id)},
    )
    identity_row = identity_result.fetchone()

    if identity_row is not None and identity_row._mapping.get("password_hash"):
        return bool(verify_password(provided_password, str(identity_row._mapping.get("password_hash"))))

    if legacy_plain_password != provided_password:
        return False

    try:
        new_hash = hash_password(provided_password)
        if identity_row is None:
            await central_db.execute(
                text(
                    """
                    INSERT INTO auth_identity (user_id, password_hash, created_at)
                    VALUES (:user_id, :password_hash, :created_at)
                    """
                ),
                {
                    "user_id": int(user_id),
                    "password_hash": new_hash,
                    "created_at": datetime.utcnow(),
                },
            )
        else:
            await central_db.execute(
                text(
                    """
                    UPDATE auth_identity
                    SET password_hash = :password_hash
                    WHERE user_id = :user_id
                    """
                ),
                {
                    "user_id": int(user_id),
                    "password_hash": new_hash,
                },
            )
    except Exception:
        await write_audit_event(
            central_db,
            event_type=EVENT_LOGIN_PASSWORD_MIGRATION,
            outcome=OUTCOME_SECURITY,
            reason_code=AUTH_PASSWORD_MIGRATION_DEFERRED,
            user_id=int(user_id),
            ip=ip_value,
            user_agent=ua_value,
            request_id=request_id_value,
            details_json={"deferred": True},
        )

    return True


@router.post("/login-employee")
async def login_employee(
    payload: LoginEmployeeRequest,
    request: Request,
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    settings = get_settings()
    if bool(settings.AUTH_V2_BOOTSTRAP_ONLY):
        rid = request_id(request)
        return error_json_response(
            AUTH_FLOW_DISABLED,
            "Bootstrap-only auth mode is enabled. Use /auth/onboarding endpoints.",
            403,
            rid,
            details={},
        )

    rid = request_id(request)
    ip_value = client_ip(request)
    ua_value = user_agent(request)

    country_code = payload.country_code.strip()
    mobile = payload.mobile.strip()
    employee_id = int(payload.employee_id)
    key_hash = _lock_key_hash(country_code, mobile, employee_id)

    try:
        main_identity = await _resolve_main_identity(main_db, country_code, mobile, employee_id)
        contact = main_identity["contact"]

        central_identity = await _resolve_central_identity(
            central_db,
            int(contact["id"]),
            employee_id,
            country_code,
        )
        user = central_identity["user"]

        lock_state = await _load_lock_state(central_db, key_hash)
        now = datetime.utcnow()
        if lock_state and lock_state.get("locked_until") and lock_state.get("locked_until") > now:
            await write_audit_event(
                central_db,
                event_type=EVENT_LOGIN_EMPLOYEE_LOCKED,
                outcome=OUTCOME_FAILURE,
                reason_code=AUTH_LOGIN_COOLDOWN,
                country_code=country_code,
                mobile=mobile,
                employee_id=employee_id,
                ip=ip_value,
                user_agent=ua_value,
                request_id=rid,
                details_json={"locked_until": lock_state.get("locked_until").isoformat()},
            )
            await central_db.commit()
            return error_json_response(
                AUTH_LOGIN_COOLDOWN,
                "Too many failed attempts. Please try again later.",
                429,
                rid,
                details={},
            )

        valid_password = await _validate_password_and_maybe_migrate(
            central_db,
            user_id=int(user["id"]),
            legacy_plain_password=str(user.get("password") or ""),
            provided_password=payload.password,
            request_id_value=rid,
            ip_value=ip_value,
            ua_value=ua_value,
        )
        if not valid_password:
            lock_state = await _record_failed_attempt(
                db=central_db,
                key_hash=key_hash,
                country_code=country_code,
                mobile=mobile,
                employee_id=employee_id,
            )
            await write_audit_event(
                central_db,
                event_type=EVENT_LOGIN_EMPLOYEE,
                outcome=OUTCOME_FAILURE,
                reason_code=AUTH_INVALID_CREDENTIALS,
                country_code=country_code,
                mobile=mobile,
                contact_id=int(contact["id"]),
                employee_id=employee_id,
                user_id=int(user["id"]),
                ip=ip_value,
                user_agent=ua_value,
                request_id=rid,
                details_json={"fail_count": int(lock_state.get("fail_count") or 0)},
            )
            await central_db.commit()
            if lock_state.get("locked_until") is not None:
                return error_json_response(
                    AUTH_LOGIN_COOLDOWN,
                    "Too many failed attempts. Please try again later.",
                    429,
                    rid,
                    details={},
                )
            return error_json_response(
                AUTH_INVALID_CREDENTIALS,
                "Invalid credentials",
                401,
                rid,
                details={},
            )

        # Success path: reset lock and write refresh state only after all checks pass.
        await _reset_lock_state(central_db, key_hash)

        authz = await AuthorizationResolver(main_db, central_db).resolve_employee_authorization(employee_id)
        token_pair = issue_token_pair(
            user_id=int(user["id"]),
            contact_id=int(contact["id"]),
            employee_id=employee_id,
            roles=authz["roles"],
            mobile=mobile,
            authorization=authz,
        )
        refresh_hash = refresh_token_hash(token_pair["refresh_token"])
        now_utc = utcnow()
        refresh_expiry = now_utc + timedelta(days=int(get_settings().AUTH_V2_REFRESH_TOKEN_DAYS))

        await central_db.execute(
            text(
                """
                INSERT INTO auth_refresh_token (
                    user_id, contact_id, employee_id, token_jti, token_hash,
                    issued_at, expires_at, used_at, revoked_at, rotated_from_id,
                    revoke_reason, issued_ip, issued_user_agent,
                    issued_device_fingerprint_hash, last_ip, last_user_agent,
                    last_used_at, created_at
                ) VALUES (
                    :user_id, :contact_id, :employee_id, :token_jti, :token_hash,
                    :issued_at, :expires_at, NULL, NULL, NULL,
                    NULL, :issued_ip, :issued_user_agent,
                    :issued_device_fingerprint_hash, :last_ip, :last_user_agent,
                    NULL, :created_at
                )
                """
            ),
            {
                "user_id": int(user["id"]),
                "contact_id": int(contact["id"]),
                "employee_id": employee_id,
                "token_jti": token_pair["jti"],
                "token_hash": refresh_hash,
                "issued_at": now_utc.replace(tzinfo=None),
                "expires_at": refresh_expiry.replace(tzinfo=None),
                "issued_ip": ip_value,
                "issued_user_agent": ua_value,
                "issued_device_fingerprint_hash": compute_device_fingerprint(request),
                "last_ip": ip_value,
                "last_user_agent": ua_value,
                "created_at": now_utc.replace(tzinfo=None),
            },
        )

        await write_audit_event(
            central_db,
            event_type=EVENT_LOGIN_EMPLOYEE,
            outcome=OUTCOME_SUCCESS,
            country_code=country_code,
            mobile=mobile,
            contact_id=int(contact["id"]),
            employee_id=employee_id,
            user_id=int(user["id"]),
            ip=ip_value,
            user_agent=ua_value,
            request_id=rid,
            details_json={
                "roles_count": len(authz["roles"]),
                "permissions_count": len(authz["permissions"]),
                "is_super": bool(authz["is_super"]),
            },
        )
        await central_db.commit()

        return success_json_response(
            {
                "access_token": token_pair["access_token"],
                "refresh_token": token_pair["refresh_token"],
                "token_type": "Bearer",
                "user_id": int(user["id"]),
                "contact_id": int(contact["id"]),
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
            message="Login successful",
        )
    except AuthError as exc:
        await _record_failed_attempt(
            db=central_db,
            key_hash=key_hash,
            country_code=country_code,
            mobile=mobile,
            employee_id=employee_id,
        )
        await write_audit_event(
            central_db,
            event_type=EVENT_LOGIN_EMPLOYEE,
            outcome=OUTCOME_FAILURE,
            reason_code=exc.code,
            country_code=country_code,
            mobile=mobile,
            employee_id=employee_id,
            ip=ip_value,
            user_agent=ua_value,
            request_id=rid,
            details_json=exc.details,
        )
        await central_db.commit()
        return error_json_response(exc.code, exc.message, exc.status_code, rid, details=exc.details)
    except Exception:
        await central_db.rollback()
        return error_json_response(
            AUTH_SERVICE_UNAVAILABLE,
            "Auth v2 service unavailable",
            503,
            rid,
            details={},
        )
