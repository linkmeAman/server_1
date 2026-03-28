"""POST /auth/check-contact handler."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.constants import (
    AUTH_CONTACT_NOT_FOUND,
    AUTH_FLOW_DISABLED,
    AUTH_RATE_LIMITED,
    AUTH_SERVICE_UNAVAILABLE,
    EVENT_CHECK_CONTACT,
    EVENT_CHECK_CONTACT_RATE_LIMIT,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
)
from app.modules.auth.schemas.models import CheckContactRequest, EmployeeSummary
from app.modules.auth.services.audit import count_events, write_audit_event
from app.modules.auth.services.authorization import AuthorizationResolver
from app.modules.auth.services.common import (
    apply_timing_floor,
    attach_rate_limit_headers,
    client_ip,
    error_json_response,
    request_id,
    success_json_response,
    user_agent,
    utcnow,
)
from core.database import get_central_db_session, get_main_db_session
from core.settings import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


def _contact_name(row: Dict[str, Any]) -> str:
    parts = [str(row.get("fname") or "").strip(), str(row.get("mname") or "").strip(), str(row.get("lname") or "").strip()]
    text_name = " ".join([part for part in parts if part]).strip()
    return text_name or f"Contact #{row['id']}"


def _employee_label(row: Dict[str, Any]) -> str:
    e_id = int(row["employee_id"])
    ecode = str(row.get("ecode") or "").strip()
    position_name = str(row.get("position") or "").strip()
    department_name = str(row.get("department") or "").strip()

    base = ecode or f"Employee #{e_id}"
    details = " - ".join([part for part in [position_name, department_name] if part])
    if details:
        return f"{base} ({details})"
    return base


async def _rate_limited(
    *,
    central_db: AsyncSession,
    ip: str,
    country_code: str,
    mobile: str,
) -> Dict[str, int] | None:
    settings = get_settings()
    now = utcnow()
    window_start = (now - timedelta(minutes=10)).replace(tzinfo=None)

    ip_count = await count_events(
        central_db,
        event_types=[EVENT_CHECK_CONTACT],
        created_after=window_start,
        ip=ip,
    )
    if ip_count >= int(settings.AUTH_V2_RATE_LIMIT_IP_10M):
        return {
            "limit": int(settings.AUTH_V2_RATE_LIMIT_IP_10M),
            "remaining": 0,
            "retry_after": 600,
            "reset": int(now.timestamp()) + 600,
        }

    ip_mobile_count = await count_events(
        central_db,
        event_types=[EVENT_CHECK_CONTACT],
        created_after=window_start,
        ip=ip,
        country_code=country_code,
        mobile=mobile,
    )
    if ip_mobile_count >= int(settings.AUTH_V2_RATE_LIMIT_IP_MOBILE_10M):
        return {
            "limit": int(settings.AUTH_V2_RATE_LIMIT_IP_MOBILE_10M),
            "remaining": 0,
            "retry_after": 600,
            "reset": int(now.timestamp()) + 600,
        }

    mobile_count = await count_events(
        central_db,
        event_types=[EVENT_CHECK_CONTACT],
        created_after=window_start,
        country_code=country_code,
        mobile=mobile,
    )
    if mobile_count >= int(settings.AUTH_V2_RATE_LIMIT_MOBILE_GLOBAL_10M):
        return {
            "limit": int(settings.AUTH_V2_RATE_LIMIT_MOBILE_GLOBAL_10M),
            "remaining": 0,
            "retry_after": 600,
            "reset": int(now.timestamp()) + 600,
        }

    return None


async def _suspicious_failure_threshold_reached(
    *,
    central_db: AsyncSession,
    country_code: str,
    mobile: str,
) -> bool:
    settings = get_settings()
    window_start = (utcnow() - timedelta(minutes=int(settings.AUTH_V2_LOGIN_FAIL_WINDOW_MINUTES))).replace(
        tzinfo=None
    )
    failures = await count_events(
        central_db,
        event_types=[EVENT_CHECK_CONTACT],
        created_after=window_start,
        outcome=OUTCOME_FAILURE,
        country_code=country_code,
        mobile=mobile,
    )
    return failures >= int(settings.AUTH_V2_LOGIN_FAIL_THRESHOLD)


async def _generic_not_found(
    *,
    central_db: AsyncSession,
    request: Request,
    request_id_value: str,
    country_code: str,
    mobile: str,
    reason: str,
    audit_enabled: bool,
) -> JSONResponse:
    if audit_enabled:
        try:
            await write_audit_event(
                central_db,
                event_type=EVENT_CHECK_CONTACT,
                outcome=OUTCOME_FAILURE,
                reason_code=reason,
                country_code=country_code,
                mobile=mobile,
                ip=client_ip(request),
                user_agent=user_agent(request),
                request_id=request_id_value,
                details_json={"anti_enumeration": True},
            )
            await central_db.commit()
        except Exception:
            logger.warning("check-contact degraded: central audit write failed request_id=%s", request_id_value)
    return error_json_response(
        AUTH_CONTACT_NOT_FOUND,
        "Contact not found",
        404,
        request_id_value,
        details={},
    )


@router.post("/check-contact")
async def check_contact(
    payload: CheckContactRequest,
    request: Request,
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
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

    started = utcnow()
    rid = request_id(request)
    response: JSONResponse | None = None

    try:
        ip_value = client_ip(request)
        ua_value = user_agent(request)
        country_code = payload.country_code.strip()
        mobile = payload.mobile.strip()
        resolver = AuthorizationResolver(main_db, central_db)
        central_audit_enabled = True

        try:
            limit_state = await _rate_limited(
                central_db=central_db,
                ip=ip_value,
                country_code=country_code,
                mobile=mobile,
            )
        except Exception:
            logger.warning("check-contact degraded: central rate-limit unavailable request_id=%s", rid)
            central_audit_enabled = False
            limit_state = None

        if limit_state is not None:
            if central_audit_enabled:
                try:
                    await write_audit_event(
                        central_db,
                        event_type=EVENT_CHECK_CONTACT_RATE_LIMIT,
                        outcome=OUTCOME_FAILURE,
                        reason_code=AUTH_RATE_LIMITED,
                        country_code=country_code,
                        mobile=mobile,
                        ip=ip_value,
                        user_agent=ua_value,
                        request_id=rid,
                        details_json={"limit": limit_state["limit"]},
                    )
                    await central_db.commit()
                except Exception:
                    logger.warning("check-contact degraded: central rate-limit audit failed request_id=%s", rid)
                    central_audit_enabled = False
            response = error_json_response(
                AUTH_RATE_LIMITED,
                "Rate limit exceeded",
                429,
                rid,
                details={},
            )
            attach_rate_limit_headers(
                response,
                limit=limit_state["limit"],
                remaining=limit_state["remaining"],
                reset_epoch_seconds=limit_state["reset"],
                retry_after_seconds=limit_state["retry_after"],
            )
        else:
            suspicious_failure = False
            if central_audit_enabled:
                try:
                    suspicious_failure = await _suspicious_failure_threshold_reached(
                        central_db=central_db,
                        country_code=country_code,
                        mobile=mobile,
                    )
                except Exception:
                    logger.warning("check-contact degraded: central suspicious-check failed request_id=%s", rid)
                    central_audit_enabled = False

            if suspicious_failure:
                response = await _generic_not_found(
                    central_db=central_db,
                    request=request,
                    request_id_value=rid,
                    country_code=country_code,
                    mobile=mobile,
                    reason="SUSPICIOUS_FAILURE_THRESHOLD",
                    audit_enabled=central_audit_enabled,
                )
            else:
                # ── Primary lookup: pf_central.user ──────────────────────
                user_result = await central_db.execute(
                    text(
                        """
                        SELECT id, contact_id, fname, lname
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
                    response = await _generic_not_found(
                        central_db=central_db,
                        request=request,
                        request_id_value=rid,
                        country_code=country_code,
                        mobile=mobile,
                        reason="CONTACT_NOT_FOUND",
                        audit_enabled=central_audit_enabled,
                    )
                else:
                    user_rec = dict(user_row._mapping)
                    contact_id_val = int(user_rec["contact_id"])

                    # ── Fetch contact from client DB by PK ───────────────
                    contact_result = await main_db.execute(
                        text(
                            """
                            SELECT id, fname, mname, lname
                            FROM contact
                            WHERE id = :contact_id
                              AND (park IS NULL OR park = 0)
                            LIMIT 1
                            """
                        ),
                        {"contact_id": contact_id_val},
                    )
                    contact_row = contact_result.fetchone()
                    if contact_row is None:
                        response = await _generic_not_found(
                            central_db=central_db,
                            request=request,
                            request_id_value=rid,
                            country_code=country_code,
                            mobile=mobile,
                            reason="CONTACT_NOT_FOUND",
                            audit_enabled=central_audit_enabled,
                        )
                    else:
                        contact = dict(contact_row._mapping)
                        # Override contact["id"] to ensure it matches user.contact_id
                        contact["id"] = contact_id_val
                        employee_result = await main_db.execute(
                            text(
                                """
                                SELECT
                                    e.id AS employee_id,
                                    e.ecode AS ecode,
                                    e.position_id AS position_id,
                                    e.department_id AS department_id
                                FROM employee e
                                WHERE e.contact_id = :contact_id
                                  AND e.status = 1
                                  AND (e.park IS NULL OR e.park = 0)
                                ORDER BY e.id ASC
                                """
                            ),
                            {"contact_id": contact_id_val},
                        )
                        employee_rows = [dict(row._mapping) for row in employee_result.fetchall()]

                        employees = []
                        degraded_name_lookup = False
                        for row in employee_rows:
                            employee_id = int(row["employee_id"])
                            try:
                                org_context = await resolver.get_org_context(
                                    employee_id=employee_id,
                                    fail_open_name_lookup=True,
                                )
                            except Exception:
                                org_context = {
                                    "position_id": row.get("position_id"),
                                    "position": None,
                                    "department_id": row.get("department_id"),
                                    "department": None,
                                    "degraded_name_lookup": True,
                                }
                            if bool(org_context.get("degraded_name_lookup")):
                                degraded_name_lookup = True

                            row["position_id"] = org_context.get("position_id")
                            row["position"] = org_context.get("position")
                            row["department_id"] = org_context.get("department_id")
                            row["department"] = org_context.get("department")
                            employees.append(
                                EmployeeSummary(
                                    employee_id=employee_id,
                                    display_label=_employee_label(row),
                                    position_id=(
                                        int(row["position_id"]) if row.get("position_id") is not None else None
                                    ),
                                    position=(
                                        str(row["position"]).strip() if row.get("position") is not None else None
                                    ),
                                    department_id=(
                                        int(row["department_id"]) if row.get("department_id") is not None else None
                                    ),
                                    department=(
                                        str(row["department"]).strip() if row.get("department") is not None else None
                                    ),
                                ).model_dump()
                            )

                        if degraded_name_lookup:
                            logger.warning(
                                "check-contact degraded: central org-name lookup unavailable request_id=%s contact_id=%s",
                                rid,
                                contact_id_val,
                            )

                        if not employees:
                            response = await _generic_not_found(
                                central_db=central_db,
                                request=request,
                                request_id_value=rid,
                                country_code=country_code,
                                mobile=mobile,
                                reason="NO_ACTIVE_EMPLOYEES",
                                audit_enabled=central_audit_enabled,
                            )
                        else:
                            if central_audit_enabled:
                                try:
                                    await write_audit_event(
                                        central_db,
                                        event_type=EVENT_CHECK_CONTACT,
                                        outcome=OUTCOME_SUCCESS,
                                        country_code=country_code,
                                        mobile=mobile,
                                        contact_id=contact_id_val,
                                        ip=ip_value,
                                        user_agent=ua_value,
                                        request_id=rid,
                                        details_json={"employee_count": len(employees)},
                                    )
                                    await central_db.commit()
                                except Exception:
                                    logger.warning(
                                        "check-contact degraded: central success audit write failed request_id=%s",
                                        rid,
                                    )

                            response = success_json_response(
                                {
                                    "contact_id": contact_id_val,
                                    "contact_name": _contact_name(contact),
                                    "employees": employees,
                                },
                                request_id_value=rid,
                                message="Contact resolved",
                            )
    except Exception:
        logger.exception("Auth v2 check-contact failed request_id=%s", rid)
        try:
            await central_db.rollback()
        except Exception:
            pass
        response = error_json_response(
            AUTH_SERVICE_UNAVAILABLE,
            "Auth v2 service unavailable",
            503,
            rid,
            details={},
        )

    await apply_timing_floor(started)
    if response is None:
        response = error_json_response(
            AUTH_SERVICE_UNAVAILABLE,
            "Auth v2 service unavailable",
            503,
            rid,
            details={},
        )
    return response


