"""PASETO auth endpoints with hybrid legacy-password migration."""

import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.modules.auth.constants import (
    REVOKE_REASON_LOGOUT,
    REVOKE_REASON_PASSWORD_CHANGE,
    REVOKE_REASON_REPLAY,
)
from app.modules.auth.services.common import refresh_token_hash
from app.core.database import get_db_session
from app.core.models import AuthIdentity, User
from app.core.security import (
    SecurityDependencyError,
    create_access_token,
    create_refresh_token,
    generate_reset_token,
    hash_password,
    validate_token,
    verify_password,
)
from app.core.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="", tags=["auth"])
LEGACY_CONTACT_ID = 0
LEGACY_EMPLOYEE_ID = 0


class LoginRequest(BaseModel):
    mobile: str = Field(..., min_length=6, max_length=20)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    mobile: str = Field(..., min_length=6, max_length=20)


class ResetRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)


class MessageResponse(BaseModel):
    success: bool = True
    message: str


def _get_active_user_by_mobile(db: Session, mobile: str) -> Optional[User]:
    return db.query(User).filter(User.mobile == mobile, User.inactive == 0).first()


def _get_active_user_by_id(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(User.id == user_id, User.inactive == 0).first()


def authenticate_user(db: Session, mobile: str, password: str) -> Optional[Tuple[User, AuthIdentity]]:
    """
    Hybrid migration authentication flow.

    1) Find active legacy user in central DB.
    2) If sidecar exists: verify bcrypt hash.
    3) Else: verify legacy plaintext, then auto-migrate into AuthIdentity.
    """
    user = _get_active_user_by_mobile(db, mobile)
    if not user:
        return None

    identity = db.query(AuthIdentity).filter(AuthIdentity.user_id == user.id).first()
    if identity:
        if not verify_password(password, identity.password_hash):
            return None
        return user, identity

    if user.password != password:
        return None

    migrated_hash = hash_password(password)
    identity = AuthIdentity(user_id=user.id, password_hash=migrated_hash)
    db.add(identity)
    db.commit()
    db.refresh(identity)
    logger.info("Auto-migrated user_id=%s from plaintext to bcrypt sidecar", user.id)
    return user, identity


def _legacy_parse_refresh_claims(token: str) -> tuple[int, str, datetime, datetime]:
    claims = validate_token(token, expected_type="refresh")
    user_id = int(claims.get("sub", 0))
    if user_id <= 0:
        raise ValueError("Invalid refresh token subject")

    token_jti = str(claims.get("jti", "")).strip()
    if not token_jti:
        raise ValueError("Missing refresh token jti")

    issued_at_ts = int(claims.get("iat", 0) or 0)
    exp_ts = int(claims.get("exp", 0) or 0)
    if exp_ts <= 0:
        raise ValueError("Missing refresh token expiry")

    now = datetime.utcnow()
    issued_at = datetime.utcfromtimestamp(issued_at_ts) if issued_at_ts > 0 else now
    expires_at = datetime.utcfromtimestamp(exp_ts)
    return user_id, token_jti, issued_at, expires_at


def _legacy_insert_refresh_row(
    db: Session,
    *,
    user_id: int,
    token_jti: str,
    token_hash: str,
    issued_at: datetime,
    expires_at: datetime,
    rotated_from_id: Optional[int] = None,
) -> None:
    db.execute(
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
                :issued_at, :expires_at, NULL, NULL, :rotated_from_id,
                NULL, NULL, NULL,
                NULL, NULL, NULL,
                NULL, :created_at
            )
            """
        ),
        {
            "user_id": int(user_id),
            "contact_id": LEGACY_CONTACT_ID,
            "employee_id": LEGACY_EMPLOYEE_ID,
            "token_jti": token_jti,
            "token_hash": token_hash,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "rotated_from_id": rotated_from_id,
            "created_at": datetime.utcnow(),
        },
    )


def _legacy_load_refresh_row_for_update(
    db: Session,
    *,
    user_id: int,
    token_jti: str,
    token_hash: str,
) -> Optional[dict]:
    result = db.execute(
        text(
            """
            SELECT id, used_at, revoked_at, rotated_from_id
            FROM auth_refresh_token
            WHERE user_id = :user_id
              AND contact_id = :contact_id
              AND employee_id = :employee_id
              AND token_jti = :token_jti
              AND token_hash = :token_hash
            LIMIT 1
            FOR UPDATE
            """
        ),
        {
            "user_id": int(user_id),
            "contact_id": LEGACY_CONTACT_ID,
            "employee_id": LEGACY_EMPLOYEE_ID,
            "token_jti": token_jti,
            "token_hash": token_hash,
        },
    )
    row = result.fetchone()
    return dict(row._mapping) if row is not None else None


def _legacy_load_token_node(db: Session, token_id: int) -> Optional[dict]:
    result = db.execute(
        text(
            """
            SELECT id, rotated_from_id
            FROM auth_refresh_token
            WHERE id = :id
            LIMIT 1
            """
        ),
        {"id": int(token_id)},
    )
    row = result.fetchone()
    return dict(row._mapping) if row is not None else None


def _legacy_resolve_chain_root_id(db: Session, anchor_token_id: int) -> Optional[int]:
    node = _legacy_load_token_node(db, anchor_token_id)
    if node is None:
        return None

    current_id = int(node["id"])
    seen = {current_id}
    parent_id = node.get("rotated_from_id")
    while parent_id is not None:
        parent_int = int(parent_id)
        if parent_int in seen:
            break
        seen.add(parent_int)

        parent_node = _legacy_load_token_node(db, parent_int)
        if parent_node is None:
            break
        current_id = int(parent_node["id"])
        parent_id = parent_node.get("rotated_from_id")

    return current_id


def _legacy_collect_chain_ids(db: Session, root_token_id: int) -> list[int]:
    pending = [int(root_token_id)]
    seen: set[int] = set()

    while pending:
        current = pending.pop(0)
        if current in seen:
            continue
        seen.add(current)

        children = db.execute(
            text(
                """
                SELECT id
                FROM auth_refresh_token
                WHERE rotated_from_id = :rotated_from_id
                """
            ),
            {"rotated_from_id": current},
        )
        for row in children.fetchall():
            child_id = int(row._mapping["id"])
            if child_id not in seen:
                pending.append(child_id)

    return list(seen)


def _legacy_revoke_refresh_chain(db: Session, *, anchor_token_id: int, reason: str) -> int:
    root_id = _legacy_resolve_chain_root_id(db, anchor_token_id)
    if root_id is None:
        return 0

    token_ids = _legacy_collect_chain_ids(db, root_id)
    if not token_ids:
        return 0

    statement = text(
        """
        UPDATE auth_refresh_token
        SET revoked_at = :now,
            revoke_reason = :reason
        WHERE id IN :token_ids
          AND revoked_at IS NULL
        """
    ).bindparams(bindparam("token_ids", expanding=True))
    result = db.execute(
        statement,
        {"now": datetime.utcnow(), "reason": reason, "token_ids": token_ids},
    )
    return int(result.rowcount or 0)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest):
    db = get_db_session()
    try:
        authenticated = authenticate_user(db, payload.mobile, payload.password)
        if not authenticated:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
            )

        user, identity = authenticated
        claims = {"mobile": user.mobile}
        access_token = create_access_token(subject=str(user.id), extra_claims=claims)
        refresh_token = create_refresh_token(subject=str(user.id), extra_claims=claims)
        _, token_jti, issued_at, expires_at = _legacy_parse_refresh_claims(refresh_token)
        _legacy_insert_refresh_row(
            db,
            user_id=int(user.id),
            token_jti=token_jti,
            token_hash=refresh_token_hash(refresh_token),
            issued_at=issued_at,
            expires_at=expires_at,
            rotated_from_id=None,
        )

        # Backward-compat mirror only; source-of-truth is auth_refresh_token.
        identity.refresh_token = refresh_token
        db.add(identity)
        db.commit()

        token_response = TokenResponse(access_token=access_token, refresh_token=refresh_token)
        logger.info(
            "AUTH_RESPONSE /login status=%s user_id=%s payload=%s",
            200,
            user.id,
            token_response.model_dump(),
        )
        return token_response
    except SecurityDependencyError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        db.close()


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest):
    db = get_db_session()
    try:
        try:
            user_id, token_jti, _, _ = _legacy_parse_refresh_claims(payload.refresh_token)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
            ) from exc

        current_row = _legacy_load_refresh_row_for_update(
            db,
            user_id=user_id,
            token_jti=token_jti,
            token_hash=refresh_token_hash(payload.refresh_token),
        )
        if current_row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
            )
        if current_row.get("used_at") is not None or current_row.get("revoked_at") is not None:
            _legacy_revoke_refresh_chain(
                db,
                anchor_token_id=int(current_row["id"]),
                reason=REVOKE_REASON_REPLAY,
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
            )

        user = _get_active_user_by_id(db, user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive or missing"
            )

        identity = db.query(AuthIdentity).filter(AuthIdentity.user_id == user_id).first()
        new_claims = {"mobile": user.mobile}
        access_token = create_access_token(subject=str(user.id), extra_claims=new_claims)
        refresh_token = create_refresh_token(subject=str(user.id), extra_claims=new_claims)
        _, new_jti, issued_at, expires_at = _legacy_parse_refresh_claims(refresh_token)

        db.execute(
            text(
                """
                UPDATE auth_refresh_token
                SET used_at = :used_at,
                    last_used_at = :last_used_at
                WHERE id = :id
                """
            ),
            {
                "used_at": datetime.utcnow(),
                "last_used_at": datetime.utcnow(),
                "id": int(current_row["id"]),
            },
        )
        _legacy_insert_refresh_row(
            db,
            user_id=int(user.id),
            token_jti=new_jti,
            token_hash=refresh_token_hash(refresh_token),
            issued_at=issued_at,
            expires_at=expires_at,
            rotated_from_id=int(current_row["id"]),
        )

        if identity is not None:
            # Backward-compat mirror only; source-of-truth is auth_refresh_token.
            identity.refresh_token = refresh_token
            db.add(identity)
        db.commit()

        token_response = TokenResponse(access_token=access_token, refresh_token=refresh_token)
        logger.info(
            "AUTH_RESPONSE /refresh status=%s user_id=%s payload=%s",
            200,
            user.id,
            token_response.model_dump(),
        )
        return token_response
    except SecurityDependencyError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        db.close()


@router.post("/logout", response_model=MessageResponse)
async def logout(payload: RefreshRequest):
    db = get_db_session()
    try:
        try:
            user_id, token_jti, _, _ = _legacy_parse_refresh_claims(payload.refresh_token)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
            ) from exc

        current_row = _legacy_load_refresh_row_for_update(
            db,
            user_id=user_id,
            token_jti=token_jti,
            token_hash=refresh_token_hash(payload.refresh_token),
        )
        if current_row is not None:
            _legacy_revoke_refresh_chain(
                db,
                anchor_token_id=int(current_row["id"]),
                reason=REVOKE_REASON_LOGOUT,
            )

        identity = db.query(AuthIdentity).filter(AuthIdentity.user_id == user_id).first()
        if identity and identity.refresh_token == payload.refresh_token:
            identity.refresh_token = None
            db.add(identity)
        db.commit()

        message_response = MessageResponse(message="Logged out successfully")
        logger.info(
            "AUTH_RESPONSE /logout status=%s user_id=%s payload=%s",
            200,
            user_id,
            message_response.model_dump(),
        )
        return message_response
    except SecurityDependencyError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        db.close()


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(payload: ForgotPasswordRequest):
    db = get_db_session()
    try:
        user = _get_active_user_by_mobile(db, payload.mobile)
        if not user:
            message_response = MessageResponse(
                message="If this account exists, a reset message has been sent."
            )
            logger.info(
                "AUTH_RESPONSE /forgot-password status=%s user_found=%s payload=%s",
                200,
                False,
                message_response.model_dump(),
            )
            return message_response

        identity = db.query(AuthIdentity).filter(AuthIdentity.user_id == user.id).first()
        if not identity:
            identity = AuthIdentity(user_id=user.id, password_hash=hash_password(user.password))
            db.add(identity)
            db.flush()

        settings = get_settings()
        reset_token = generate_reset_token()
        identity.reset_token = reset_token
        identity.reset_token_expires_at = datetime.utcnow() + timedelta(
            minutes=settings.RESET_TOKEN_EXPIRE_MINUTES
        )
        db.add(identity)
        db.commit()

        print(f"Mock Email Sent to {user.mobile}: reset token={reset_token}")

        message_response = MessageResponse(
            message="If this account exists, a reset message has been sent."
        )
        logger.info(
            "AUTH_RESPONSE /forgot-password status=%s user_id=%s payload=%s",
            200,
            user.id,
            message_response.model_dump(),
        )
        return message_response
    except SecurityDependencyError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        db.close()


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(payload: ResetRequest):
    db = get_db_session()
    try:
        identity = db.query(AuthIdentity).filter(AuthIdentity.reset_token == payload.token).first()
        if not identity:
            raise HTTPException(status_code=400, detail="Invalid reset token")

        if (
            identity.reset_token_expires_at is None
            or identity.reset_token_expires_at < datetime.utcnow()
        ):
            raise HTTPException(status_code=400, detail="Reset token expired")

        identity.password_hash = hash_password(payload.new_password)
        identity.refresh_token = None
        identity.reset_token = None
        identity.reset_token_expires_at = None
        db.execute(
            text(
                """
                UPDATE auth_refresh_token
                SET revoked_at = :revoked_at,
                    revoke_reason = :revoke_reason
                WHERE user_id = :user_id
                  AND revoked_at IS NULL
                """
            ),
            {
                "revoked_at": datetime.utcnow(),
                "revoke_reason": REVOKE_REASON_PASSWORD_CHANGE,
                "user_id": int(identity.user_id),
            },
        )
        db.add(identity)
        db.commit()

        message_response = MessageResponse(message="Password reset successful")
        logger.info(
            "AUTH_RESPONSE /reset-password status=%s user_id=%s payload=%s",
            200,
            identity.user_id,
            message_response.model_dump(),
        )
        return message_response
    except SecurityDependencyError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        db.close()
