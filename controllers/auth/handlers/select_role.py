"""POST /auth/select-role handler.

Step 2 of the redesigned two-step login flow.

Receives the short-lived identity_token (from /auth/verify-identity) and
the chosen employee_id, then:
  - Validates the identity token
  - Verifies the employee belongs to this user
  - Resolves authorization (roles, permissions)
  - Issues a full session (access + refresh tokens)
  - Returns a rich profile object: user + contact + employee + authz

Session JSON shape
------------------
{
    "access_token":  "...",
    "refresh_token": "...",
    "token_type":    "Bearer",
    "user_id":       123,
    "contact_id":    456,
    "employee_id":   789,
    "user": {
        "id": 123, "fname": "John", "lname": "Doe",
        "mobile": "9876543210", "country_code": "+91",
        "admin": 0, "type": "employee", "client_id": 1
    },
    "contact": {
        "id": 456, "fname": "John", "mname": "", "lname": "Doe",
        "full_name": "John Doe",
        "email": "", "gender": "", "dob": null,
        "mobile": "9876543210", "country_code": "+91"
    },
    "employee": {
        "id": 789, "ecode": "EMP001",
        "position_id": 10, "position": "Software Engineer",
        "department_id": 5, "department": "Engineering",
        "status": 1, "doj": "2023-01-15"
    },
    "authz": {
        "is_super": false,
        "roles": [{"role_code": "employee", "role_name": "Employee"}],
        "permissions": ["attendance.view"],
        "permissions_version": 1,
        "permissions_schema_version": 1
    }
}
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from controllers.auth.constants import (
    AUTH_EMPLOYEE_INACTIVE,
    AUTH_EMPLOYEE_USER_MAPPING_MISSING,
    AUTH_FLOW_DISABLED,
    AUTH_IDENTITY_MISMATCH,
    AUTH_INVALID_TOKEN,
    AUTH_SERVICE_UNAVAILABLE,
    EVENT_SELECT_ROLE,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
)
from controllers.auth.schemas.models import SelectRoleRequest
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
from controllers.auth.services.token_service import issue_token_pair, verify_identity_token
from core.database import get_central_db_session, get_main_db_session
from core.prism_cache import build_prism_cache, sync_prism_employee_attrs
from core.settings import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


def _str(value: Any, fallback: str = "") -> str:
    return str(value).strip() if value is not None else fallback


def _int_or_none(value: Any) -> Optional[int]:
    return int(value) if value is not None else None


def _full_name(fname: str, mname: str, lname: str) -> str:
    return " ".join(p for p in [fname, mname, lname] if p).strip()


async def _load_employee(main_db: AsyncSession, employee_id: int) -> Dict[str, Any]:
    result = await main_db.execute(
        text(
            """
            SELECT
                e.id, e.contact_id, e.ecode, e.position_id, e.department_id,
                e.status, e.doj,
                ep.position,
                ed.department
            FROM employee e
            LEFT JOIN employee_position ep  ON ep.id = e.position_id
            LEFT JOIN employee_department ed ON ed.id = e.department_id
            WHERE e.id = :employee_id
              AND (e.park IS NULL OR e.park = 0)
            LIMIT 1
            """
        ),
        {"employee_id": int(employee_id)},
    )
    row = result.fetchone()
    if row is None:
        raise AuthError(AUTH_EMPLOYEE_INACTIVE, "Employee not found", 403)
    emp = dict(row._mapping)
    if int(emp.get("status") or 0) != 1:
        raise AuthError(AUTH_EMPLOYEE_INACTIVE, "Employee is inactive", 403)
    return emp


async def _load_contact(main_db: AsyncSession, contact_id: int) -> Dict[str, Any]:
    result = await main_db.execute(
        text(
            """
            SELECT id, fname, mname, lname, country_code, mobile, email, gender, dob
            FROM contact
            WHERE id = :contact_id
              AND (park IS NULL OR park = 0)
            LIMIT 1
            """
        ),
        {"contact_id": int(contact_id)},
    )
    row = result.fetchone()
    if row is None:
        raise AuthError(AUTH_EMPLOYEE_USER_MAPPING_MISSING, "Contact not found", 401)
    return dict(row._mapping)


async def _load_user(central_db: AsyncSession, user_id: int) -> Dict[str, Any]:
    result = await central_db.execute(
        text(
            """
            SELECT id, fname, lname, contact_id, country_code, mobile,
                   admin, type, client_id, inactive
            FROM user
            WHERE id = :user_id
              AND inactive = 0
              AND (park IS NULL OR park = 0)
            LIMIT 1
            """
        ),
        {"user_id": int(user_id)},
    )
    row = result.fetchone()
    if row is None:
        raise AuthError(AUTH_EMPLOYEE_USER_MAPPING_MISSING, "User not found", 401)
    return dict(row._mapping)


@router.post("/select-role")
async def select_role(
    payload: SelectRoleRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
) -> JSONResponse:
    settings = get_settings()
    if bool(settings.AUTH_V2_BOOTSTRAP_ONLY):
        rid = request_id(request)
        return error_json_response(
            AUTH_FLOW_DISABLED,
            "Supreme-setup-only auth mode is enabled. Use /auth/onboarding endpoints.",
            403,
            rid,
            details={},
        )

    rid = request_id(request)
    ip_value = client_ip(request)
    ua_value = user_agent(request)

    try:
        # ── 1. Validate identity token ───────────────────────────────────────
        identity = verify_identity_token(payload.identity_token)
        user_id = int(identity["user_id"])
        contact_id = int(identity["contact_id"])
        mobile = str(identity["mobile"])
        country_code = str(identity["country_code"])
        employee_id = int(payload.employee_id)

        # ── 2. Load full profile ─────────────────────────────────────────────
        employee = await _load_employee(main_db, employee_id)
        contact = await _load_contact(main_db, contact_id)
        user = await _load_user(central_db, user_id)

        # Confirm employee's contact matches
        if int(employee.get("contact_id") or 0) != contact_id:
            raise AuthError(AUTH_IDENTITY_MISMATCH, "Employee/contact mismatch", 401)

        # ── 3. Resolve authorization ─────────────────────────────────────────
        authz = await AuthorizationResolver(main_db, central_db).resolve_employee_authorization(employee_id)

        # ── 4. Issue session tokens ──────────────────────────────────────────
        token_pair = issue_token_pair(
            user_id=user_id,
            contact_id=contact_id,
            employee_id=employee_id,
            roles=authz["roles"],
            mobile=mobile,
            authorization=authz,
        )
        r_hash = refresh_token_hash(token_pair["refresh_token"])
        now_utc = utcnow()
        refresh_expiry = now_utc + timedelta(days=int(settings.AUTH_V2_REFRESH_TOKEN_DAYS))

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
                "user_id": user_id,
                "contact_id": contact_id,
                "employee_id": employee_id,
                "token_jti": token_pair["jti"],
                "token_hash": r_hash,
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
            event_type=EVENT_SELECT_ROLE,
            outcome=OUTCOME_SUCCESS,
            country_code=country_code,
            mobile=mobile,
            contact_id=contact_id,
            employee_id=employee_id,
            user_id=user_id,
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

        # Rebuild PRISM permissions cache for this user in the background.
        background_tasks.add_task(build_prism_cache, user_id)
        # Sync employee ABAC attributes so the PDP has fresh department/designation context.
        background_tasks.add_task(sync_prism_employee_attrs, user_id, contact_id)

        # ── 6. Build rich profile response ───────────────────────────────────
        fname_c = _str(contact.get("fname"))
        mname_c = _str(contact.get("mname"))
        lname_c = _str(contact.get("lname"))
        doj_val = employee.get("doj")
        dob_val = contact.get("dob")

        return success_json_response(
            {
                "access_token": token_pair["access_token"],
                "refresh_token": token_pair["refresh_token"],
                "token_type": "Bearer",
                "user_id": user_id,
                "contact_id": contact_id,
                "employee_id": employee_id,
                # ── user (auth record from pf_central) ──
                "user": {
                    "id": user_id,
                    "fname": _str(user.get("fname")),
                    "lname": _str(user.get("lname")),
                    "mobile": _str(user.get("mobile")),
                    "country_code": _str(user.get("country_code")),
                    "admin": int(user.get("admin") or 0),
                    "type": _str(user.get("type")),
                    "client_id": _int_or_none(user.get("client_id")),
                },
                # ── contact (person record from client DB) ──
                "contact": {
                    "id": contact_id,
                    "fname": fname_c,
                    "mname": mname_c,
                    "lname": lname_c,
                    "full_name": _full_name(fname_c, mname_c, lname_c),
                    "mobile": _str(contact.get("mobile")),
                    "country_code": _str(contact.get("country_code")),
                    "email": _str(contact.get("email")),
                    "gender": _str(contact.get("gender")),
                    "dob": dob_val.isoformat() if dob_val else None,
                },
                # ── employee (role record from client DB) ──
                "employee": {
                    "id": employee_id,
                    "ecode": _str(employee.get("ecode")) or None,
                    "position_id": _int_or_none(employee.get("position_id")),
                    "position": _str(employee.get("position")) or None,
                    "department_id": _int_or_none(employee.get("department_id")),
                    "department": _str(employee.get("department")) or None,
                    "status": int(employee.get("status") or 1),
                    "doj": doj_val.isoformat() if doj_val else None,
                },
                # ── authz (RBAC / permissions) ──
                "authz": {
                    "is_super": bool(authz["is_super"]),
                    "roles": authz["roles"],
                    "permissions": authz["permissions"],
                    "permissions_version": int(authz.get("permissions_version") or 0),
                    "permissions_schema_version": int(authz.get("permissions_schema_version") or 1),
                },
            },
            request_id_value=rid,
            message="Login successful",
        )

    except AuthError as exc:
        await write_audit_event(
            central_db,
            event_type=EVENT_SELECT_ROLE,
            outcome=OUTCOME_FAILURE,
            reason_code=exc.code,
            ip=ip_value,
            user_agent=ua_value,
            request_id=rid,
        )
        try:
            await central_db.commit()
        except Exception:
            pass
        return error_json_response(exc.code, exc.message, exc.status_code, rid, details={})
    except Exception:
        logger.exception("select-role failed request_id=%s", rid)
        return error_json_response(AUTH_SERVICE_UNAVAILABLE, "Service unavailable", 503, rid, details={})

