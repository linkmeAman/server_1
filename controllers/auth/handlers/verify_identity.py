"""POST /auth/verify-identity handler.

Step 1 of the redesigned two-step login flow.

Verifies the user's password and returns a short-lived identity_token
plus the list of employee roles the user can select from.  The frontend
holds this token and presents the role-selection screen; no session is
issued here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from controllers.auth.constants import (
    AUTH_CONTACT_NOT_FOUND,
    AUTH_FLOW_DISABLED,
    AUTH_INVALID_CREDENTIALS,
    AUTH_LOGIN_COOLDOWN,
    AUTH_SERVICE_UNAVAILABLE,
    EVENT_VERIFY_IDENTITY,
    LOCK_KEY_TYPE_VERIFY_IDENTITY,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
)
from controllers.auth.schemas.models import VerifyIdentityRequest
from controllers.auth.services.audit import write_audit_event
from controllers.auth.services.common import (
    AuthError,
    client_ip,
    error_json_response,
    request_id,
    sha256_hex,
    success_json_response,
    user_agent,
    utcnow,
)
from controllers.auth.services.token_service import issue_identity_token
from core.database import get_central_db_session, get_main_db_session
from core.security import verify_password
from core.settings import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


def _lock_key_hash(country_code: str, mobile: str) -> str:
    return sha256_hex(f"{country_code.strip()}|{mobile.strip()}")


async def _load_lock_state(db: AsyncSession, key_hash: str) -> Optional[Dict[str, Any]]:
    result = await db.execute(
        text(
            """
            SELECT id, fail_count, first_fail_at, last_fail_at, locked_until
            FROM auth_lock_state
            WHERE key_type = :key_type AND key_hash = :key_hash
            LIMIT 1
            """
        ),
        {"key_type": LOCK_KEY_TYPE_VERIFY_IDENTITY, "key_hash": key_hash},
    )
    row = result.fetchone()
    return dict(row._mapping) if row else None


async def _record_failed_attempt(
    *, db: AsyncSession, key_hash: str, country_code: str, mobile: str
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
                INSERT INTO auth_lock_state (
                    key_type, country_code, mobile, employee_id, key_hash,
                    fail_count, first_fail_at, last_fail_at, locked_until, created_at, modified_at
                ) VALUES (
                    :key_type, :country_code, :mobile, NULL, :key_hash,
                    1, :now, :now, NULL, :now, :now
                )
                """
            ),
            {
                "key_type": LOCK_KEY_TYPE_VERIFY_IDENTITY,
                "country_code": country_code,
                "mobile": mobile,
                "key_hash": key_hash,
                "now": now,
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
            UPDATE auth_lock_state
            SET fail_count = :fail_count,
                first_fail_at = :first_fail_at,
                last_fail_at = :now,
                locked_until = :locked_until,
                modified_at = :now
            WHERE key_type = :key_type AND key_hash = :key_hash
            """
        ),
        {
            "fail_count": fail_count,
            "first_fail_at": first_fail_at,
            "now": now,
            "locked_until": locked_until,
            "key_type": LOCK_KEY_TYPE_VERIFY_IDENTITY,
            "key_hash": key_hash,
        },
    )
    return {"fail_count": fail_count, "locked_until": locked_until}


async def _reset_lock_state(db: AsyncSession, key_hash: str) -> None:
    await db.execute(
        text(
            """
            UPDATE auth_lock_state
            SET fail_count = 0,
                first_fail_at = NULL,
                last_fail_at = NULL,
                locked_until = NULL,
                modified_at = :now
            WHERE key_type = :key_type AND key_hash = :key_hash
            """
        ),
        {"now": datetime.utcnow(), "key_type": LOCK_KEY_TYPE_VERIFY_IDENTITY, "key_hash": key_hash},
    )


async def _load_employees_for_user(
    main_db: AsyncSession,
    contact_id: int,
) -> List[Dict[str, Any]]:
    """Return all active employees for this contact directly from the client DB."""
    emp_result = await main_db.execute(
        text(
            """
            SELECT
                e.id          AS employee_id,
                e.ecode,
                e.position_id,
                e.department_id,
                e.status,
                e.doj,
                ep.position,
                ed.department
            FROM employee e
            LEFT JOIN employee_position ep  ON ep.id = e.position_id
            LEFT JOIN employee_department ed ON ed.id = e.department_id
            WHERE e.contact_id = :contact_id
              AND e.status = 1
              AND (e.park IS NULL OR e.park = 0)
            ORDER BY e.id ASC
            """
        ),
        {"contact_id": contact_id},
    )

    employees: List[Dict[str, Any]] = []
    for row in emp_result.fetchall():
        r = dict(row._mapping)
        eid = int(r["employee_id"])
        ecode = str(r.get("ecode") or "").strip()
        position = str(r.get("position") or "").strip()
        department = str(r.get("department") or "").strip()
        base = ecode or f"Employee #{eid}"
        details = " - ".join(part for part in [position, department] if part)
        display_label = f"{base} ({details})" if details else base
        employees.append(
            {
                "employee_id": eid,
                "ecode": ecode or None,
                "display_label": display_label,
                "position_id": int(r["position_id"]) if r.get("position_id") is not None else None,
                "position": position or None,
                "department_id": int(r["department_id"]) if r.get("department_id") is not None else None,
                "department": department or None,
            }
        )
    return employees


@router.post("/verify-identity")
async def verify_identity(
    payload: VerifyIdentityRequest,
    request: Request,
    central_db: AsyncSession = Depends(get_central_db_session),
    main_db: AsyncSession = Depends(get_main_db_session),
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
    country_code = payload.country_code.strip()
    mobile = payload.mobile.strip()
    key_hash = _lock_key_hash(country_code, mobile)

    try:
        # ── 1. Check lock ────────────────────────────────────────────────────
        lock_state = await _load_lock_state(central_db, key_hash)
        now = datetime.utcnow()
        if lock_state and lock_state.get("locked_until") and lock_state["locked_until"] > now:
            return error_json_response(
                AUTH_LOGIN_COOLDOWN,
                "Too many failed attempts. Please try again later.",
                429,
                rid,
                details={},
            )

        # ── 2. Find user by phone ────────────────────────────────────────────
        user_result = await central_db.execute(
            text(
                """
                SELECT id, fname, lname, contact_id, country_code, mobile,
                       admin, type, client_id, inactive, password_hash
                FROM user
                WHERE country_code = :country_code
                  AND mobile = :mobile
                  AND inactive = 0
                  AND (park IS NULL OR park = 0)
                LIMIT 1
                """
            ),
            {"country_code": country_code, "mobile": mobile},
        )
        user_row = user_result.fetchone()
        if user_row is None:
            lock_state = await _record_failed_attempt(
                db=central_db, key_hash=key_hash, country_code=country_code, mobile=mobile
            )
            await write_audit_event(
                central_db,
                event_type=EVENT_VERIFY_IDENTITY,
                outcome=OUTCOME_FAILURE,
                reason_code=AUTH_CONTACT_NOT_FOUND,
                country_code=country_code,
                mobile=mobile,
                ip=ip_value,
                user_agent=ua_value,
                request_id=rid,
            )
            await central_db.commit()
            return error_json_response(AUTH_INVALID_CREDENTIALS, "Invalid credentials", 401, rid, details={})

        user = dict(user_row._mapping)
        user_id = int(user["id"])
        contact_id = int(user["contact_id"])

        # ── 3. Verify password ───────────────────────────────────────────────
        existing_hash = str(user.get("password_hash") or "").strip()
        if not existing_hash or not verify_password(payload.password, existing_hash):
            lock_state = await _record_failed_attempt(
                db=central_db, key_hash=key_hash, country_code=country_code, mobile=mobile
            )
            await write_audit_event(
                central_db,
                event_type=EVENT_VERIFY_IDENTITY,
                outcome=OUTCOME_FAILURE,
                reason_code=AUTH_INVALID_CREDENTIALS,
                country_code=country_code,
                mobile=mobile,
                user_id=user_id,
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
            return error_json_response(AUTH_INVALID_CREDENTIALS, "Invalid credentials", 401, rid, details={})

        # ── 4. Load employee roles ───────────────────────────────────────────
        employees = await _load_employees_for_user(main_db, contact_id)

        # ── 5. Issue short-lived identity token ──────────────────────────────
        identity_token = issue_identity_token(
            user_id=user_id,
            contact_id=contact_id,
            mobile=mobile,
            country_code=country_code,
        )

        await _reset_lock_state(central_db, key_hash)
        await write_audit_event(
            central_db,
            event_type=EVENT_VERIFY_IDENTITY,
            outcome=OUTCOME_SUCCESS,
            country_code=country_code,
            mobile=mobile,
            user_id=user_id,
            contact_id=contact_id,
            ip=ip_value,
            user_agent=ua_value,
            request_id=rid,
            details_json={"employee_count": len(employees)},
        )
        await central_db.commit()

        fname = str(user.get("fname") or "").strip()
        lname = str(user.get("lname") or "").strip()
        contact_name = " ".join(p for p in [fname, lname] if p) or f"User #{user_id}"

        return success_json_response(
            {
                "identity_token": identity_token,
                "expires_in": 300,
                "user_id": user_id,
                "contact_id": contact_id,
                "contact_name": contact_name,
                "employees": employees,
            },
            request_id_value=rid,
            message="Identity verified. Select a role to continue.",
        )

    except AuthError as exc:
        return error_json_response(exc.code, exc.message, exc.status_code, rid, details={})
    except Exception:
        logger.exception("verify-identity failed request_id=%s", rid)
        return error_json_response(AUTH_SERVICE_UNAVAILABLE, "Service unavailable", 503, rid, details={})

