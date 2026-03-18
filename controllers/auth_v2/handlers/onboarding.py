"""Auth v2 onboarding endpoints for first-time supreme user bootstrap."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from controllers.auth_v2.constants import (
    AUTH_BOOTSTRAP_ALREADY_INITIALIZED,
    AUTH_BOOTSTRAP_USER_NOT_FOUND,
    AUTH_INVALID_CREDENTIALS,
    AUTH_SERVICE_UNAVAILABLE,
)
from controllers.auth_v2.schemas.models import BootstrapLoginRequest, BootstrapSupremeRequest
from controllers.auth_v2.services.common import (
    client_ip,
    error_json_response,
    refresh_token_hash,
    request_id,
    success_json_response,
    user_agent,
    utcnow,
)
from controllers.auth_v2.services.device_fingerprint import compute_device_fingerprint
from controllers.auth_v2.services.token_service import issue_token_pair
from core.database_v2 import get_central_db_session
from core.security import hash_password, verify_password
from core.settings import get_settings

router = APIRouter(prefix="/auth/onboarding", tags=["auth-onboarding"])


async def _ensure_bootstrap_tables(central_db: AsyncSession) -> None:
    await central_db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS auth_bootstrap_user (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                country_code VARCHAR(8) NOT NULL,
                mobile VARCHAR(20) NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                display_name VARCHAR(120) NULL,
                is_super TINYINT(1) NOT NULL DEFAULT 1,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                modified_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_auth_bootstrap_user_mobile (country_code, mobile)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )

    await central_db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS auth_refresh_token (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                user_id BIGINT NOT NULL,
                contact_id BIGINT NOT NULL,
                employee_id BIGINT NOT NULL,
                token_jti VARCHAR(128) NOT NULL,
                token_hash VARCHAR(64) NOT NULL,
                issued_at DATETIME NOT NULL,
                expires_at DATETIME NOT NULL,
                used_at DATETIME NULL,
                revoked_at DATETIME NULL,
                rotated_from_id BIGINT NULL,
                revoke_reason VARCHAR(32) NULL,
                issued_ip VARCHAR(64) NULL,
                issued_user_agent TEXT NULL,
                issued_device_fingerprint_hash VARCHAR(64) NULL,
                last_ip VARCHAR(64) NULL,
                last_user_agent TEXT NULL,
                last_used_at DATETIME NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_auth_refresh_token_token_hash (token_hash),
                UNIQUE KEY uq_auth_refresh_token_token_jti (token_jti),
                KEY ix_auth_refresh_token_user_employee_revoked (user_id, employee_id, revoked_at),
                KEY ix_auth_refresh_token_expires_at (expires_at),
                CONSTRAINT fk_auth_refresh_token_rotated
                    FOREIGN KEY (rotated_from_id) REFERENCES auth_refresh_token (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )


async def _active_bootstrap_user_count(central_db: AsyncSession) -> int:
    result = await central_db.execute(
        text(
            """
            SELECT COUNT(*) AS total
            FROM auth_bootstrap_user
            WHERE is_active = 1
            """
        )
    )
    row = result.fetchone()
    return int((row._mapping.get("total") if row else 0) or 0)


def _normalize_country_code(country_code: str) -> str:
    value = country_code.strip()
    if not value:
        return "+91"
    return value if value.startswith("+") else f"+{value}"


def _normalize_mobile(mobile: str) -> str:
    return "".join(ch for ch in mobile.strip() if ch.isdigit())


def _bootstrap_authz() -> dict:
    return {
        "position_id": None,
        "position": None,
        "department_id": None,
        "department": None,
        "permissions": ["global:super"],
        "is_super": True,
        "permissions_version": 1,
        "permissions_schema_version": 1,
    }


async def _issue_bootstrap_tokens(
    *,
    central_db: AsyncSession,
    request: Request,
    user_id: int,
    country_code: str,
    mobile: str,
) -> dict:
    token_pair = issue_token_pair(
        user_id=int(user_id),
        contact_id=int(user_id),
        employee_id=0,
        roles=[{"role_code": "SUPREME", "role_name": "Supreme User"}],
        mobile=f"{country_code}{mobile}",
        authorization=_bootstrap_authz(),
        extra_claims={"bootstrap_user": True},
    )

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
            "user_id": int(user_id),
            "contact_id": int(user_id),
            "employee_id": 0,
            "token_jti": token_pair["jti"],
            "token_hash": refresh_token_hash(token_pair["refresh_token"]),
            "issued_at": now_utc.replace(tzinfo=None),
            "expires_at": refresh_expiry.replace(tzinfo=None),
            "issued_ip": client_ip(request),
            "issued_user_agent": user_agent(request),
            "issued_device_fingerprint_hash": compute_device_fingerprint(request),
            "last_ip": client_ip(request),
            "last_user_agent": user_agent(request),
            "created_at": now_utc.replace(tzinfo=None),
        },
    )

    return token_pair


@router.get("/status")
async def onboarding_status(
    request: Request,
    central_db: AsyncSession = Depends(get_central_db_session),
):
    rid = request_id(request)
    try:
        await _ensure_bootstrap_tables(central_db)
        total_users = await _active_bootstrap_user_count(central_db)
        await central_db.commit()
        return success_json_response(
            {
                "bootstrap_required": total_users == 0,
                "total_users": total_users,
            },
            request_id_value=rid,
            message="Onboarding status fetched",
        )
    except Exception:
        await central_db.rollback()
        return error_json_response(
            AUTH_SERVICE_UNAVAILABLE,
            "Auth v2 onboarding unavailable",
            503,
            rid,
            details={},
        )


@router.post("/supreme")
async def create_supreme_user(
    payload: BootstrapSupremeRequest,
    request: Request,
    central_db: AsyncSession = Depends(get_central_db_session),
):
    rid = request_id(request)
    country_code = _normalize_country_code(payload.country_code)
    mobile = _normalize_mobile(payload.mobile)

    if len(mobile) < 6:
        return error_json_response(
            AUTH_INVALID_CREDENTIALS,
            "Enter a valid mobile number",
            400,
            rid,
            details={},
        )

    try:
        async with central_db.begin():
            await _ensure_bootstrap_tables(central_db)

            total_users = await _active_bootstrap_user_count(central_db)
            if total_users > 0:
                return error_json_response(
                    AUTH_BOOTSTRAP_ALREADY_INITIALIZED,
                    "Supreme user already initialized",
                    409,
                    rid,
                    details={},
                )

            await central_db.execute(
                text(
                    """
                    INSERT INTO auth_bootstrap_user (
                        country_code,
                        mobile,
                        password_hash,
                        display_name,
                        is_super,
                        is_active,
                        created_at,
                        modified_at
                    ) VALUES (
                        :country_code,
                        :mobile,
                        :password_hash,
                        :display_name,
                        1,
                        1,
                        :created_at,
                        :modified_at
                    )
                    """
                ),
                {
                    "country_code": country_code,
                    "mobile": mobile,
                    "password_hash": hash_password(payload.password),
                    "display_name": payload.display_name.strip() if payload.display_name else None,
                    "created_at": utcnow().replace(tzinfo=None),
                    "modified_at": utcnow().replace(tzinfo=None),
                },
            )

            row_result = await central_db.execute(
                text(
                    """
                    SELECT id, display_name
                    FROM auth_bootstrap_user
                    WHERE country_code = :country_code
                      AND mobile = :mobile
                      AND is_active = 1
                    LIMIT 1
                    """
                ),
                {
                    "country_code": country_code,
                    "mobile": mobile,
                },
            )
            user_row = row_result.fetchone()
            if user_row is None:
                return error_json_response(
                    AUTH_SERVICE_UNAVAILABLE,
                    "Unable to create supreme user",
                    503,
                    rid,
                    details={},
                )

            user_id = int(user_row._mapping["id"])
            token_pair = await _issue_bootstrap_tokens(
                central_db=central_db,
                request=request,
                user_id=user_id,
                country_code=country_code,
                mobile=mobile,
            )

        return success_json_response(
            {
                "access_token": token_pair["access_token"],
                "refresh_token": token_pair["refresh_token"],
                "token_type": "Bearer",
                "user_id": user_id,
                "contact_id": user_id,
                "employee_id": 0,
                "roles": [{"role_code": "SUPREME", "role_name": "Supreme User"}],
                "permissions": ["global:super"],
                "is_super": True,
                "permissions_version": 1,
                "permissions_schema_version": 1,
                "display_name": str(user_row._mapping.get("display_name") or "Supreme User"),
            },
            request_id_value=rid,
            message="Supreme user created",
        )
    except Exception:
        await central_db.rollback()
        return error_json_response(
            AUTH_SERVICE_UNAVAILABLE,
            "Auth v2 onboarding unavailable",
            503,
            rid,
            details={},
        )


@router.post("/login")
async def login_supreme_user(
    payload: BootstrapLoginRequest,
    request: Request,
    central_db: AsyncSession = Depends(get_central_db_session),
):
    rid = request_id(request)
    country_code = _normalize_country_code(payload.country_code)
    mobile = _normalize_mobile(payload.mobile)

    try:
        async with central_db.begin():
            await _ensure_bootstrap_tables(central_db)

            row_result = await central_db.execute(
                text(
                    """
                    SELECT id, password_hash, display_name
                    FROM auth_bootstrap_user
                    WHERE country_code = :country_code
                      AND mobile = :mobile
                      AND is_active = 1
                    LIMIT 1
                    """
                ),
                {
                    "country_code": country_code,
                    "mobile": mobile,
                },
            )
            user_row = row_result.fetchone()
            if user_row is None:
                return error_json_response(
                    AUTH_BOOTSTRAP_USER_NOT_FOUND,
                    "Supreme user not found",
                    404,
                    rid,
                    details={},
                )

            user_id = int(user_row._mapping["id"])
            password_hash = str(user_row._mapping.get("password_hash") or "")
            if not verify_password(payload.password, password_hash):
                return error_json_response(
                    AUTH_INVALID_CREDENTIALS,
                    "Invalid credentials",
                    401,
                    rid,
                    details={},
                )

            token_pair = await _issue_bootstrap_tokens(
                central_db=central_db,
                request=request,
                user_id=user_id,
                country_code=country_code,
                mobile=mobile,
            )

        return success_json_response(
            {
                "access_token": token_pair["access_token"],
                "refresh_token": token_pair["refresh_token"],
                "token_type": "Bearer",
                "user_id": user_id,
                "contact_id": user_id,
                "employee_id": 0,
                "roles": [{"role_code": "SUPREME", "role_name": "Supreme User"}],
                "permissions": ["global:super"],
                "is_super": True,
                "permissions_version": 1,
                "permissions_schema_version": 1,
                "display_name": str(user_row._mapping.get("display_name") or "Supreme User"),
            },
            request_id_value=rid,
            message="Login successful",
        )
    except Exception:
        await central_db.rollback()
        return error_json_response(
            AUTH_SERVICE_UNAVAILABLE,
            "Auth v2 onboarding unavailable",
            503,
            rid,
            details={},
        )
