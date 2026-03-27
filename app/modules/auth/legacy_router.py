"""PASETO auth endpoints with hybrid legacy-password migration."""

import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

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
            claims = validate_token(payload.refresh_token, expected_type="refresh")
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
            ) from exc

        user_id = int(claims.get("sub", 0))
        if user_id <= 0:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
            )

        identity = db.query(AuthIdentity).filter(AuthIdentity.user_id == user_id).first()
        if not identity or identity.refresh_token != payload.refresh_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
            )

        user = _get_active_user_by_id(db, user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive or missing"
            )

        new_claims = {"mobile": user.mobile}
        access_token = create_access_token(subject=str(user.id), extra_claims=new_claims)
        refresh_token = create_refresh_token(subject=str(user.id), extra_claims=new_claims)

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
            claims = validate_token(payload.refresh_token, expected_type="refresh")
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
            ) from exc

        user_id = int(claims.get("sub", 0))
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
