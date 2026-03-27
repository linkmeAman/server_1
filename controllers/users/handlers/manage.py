"""User management CRUD + session handlers."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.dependencies import require_super_auth
from app.modules.auth.services.common import (
    error_json_response,
    request_id,
    success_json_response,
    utcnow,
)
from controllers.users.constants import (
    USER_ALREADY_EXISTS,
    USER_INVALID_PASSWORD,
    USER_INVALID_USERNAME,
    USER_NOT_FOUND,
    USER_SERVICE_UNAVAILABLE,
    USER_SESSION_NOT_FOUND,
)
from controllers.users.schemas.models import CreateUserRequest
from core.database import get_central_db_session
from core.security import hash_password

router = APIRouter(prefix="/auth/users", tags=["users"])


# ── Table bootstrap ────────────────────────────────────────────────────────────

async def _ensure_user_tables(central_db: AsyncSession) -> None:
    """Create users and user_sessions tables if they do not exist."""
    await central_db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                username VARCHAR(50) NOT NULL,
                email VARCHAR(255) NULL,
                display_name VARCHAR(120) NULL,
                password_hash VARCHAR(255) NOT NULL,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                notes TEXT NULL,
                created_by BIGINT NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                modified_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_users_username (username),
                UNIQUE KEY uq_users_email (email)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )
    await central_db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS user_sessions (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                user_id BIGINT NOT NULL,
                session_token_hash VARCHAR(64) NOT NULL,
                issued_at DATETIME NOT NULL,
                expires_at DATETIME NOT NULL,
                last_used_at DATETIME NULL,
                issued_ip VARCHAR(64) NULL,
                issued_user_agent TEXT NULL,
                revoked_at DATETIME NULL,
                revoke_reason VARCHAR(32) NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_user_sessions_token (session_token_hash),
                KEY ix_user_sessions_user_revoked (user_id, revoked_at),
                KEY ix_user_sessions_expires_at (expires_at),
                CONSTRAINT fk_user_sessions_user
                    FOREIGN KEY (user_id) REFERENCES users (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fmt(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


def _row_to_user(r) -> dict:
    m = r._mapping
    return {
        "id": m["id"],
        "username": m["username"],
        "email": m["email"],
        "display_name": m["display_name"],
        "is_active": bool(m["is_active"]),
        "notes": m["notes"],
        "created_by": m["created_by"],
        "created_at": _fmt(m["created_at"]),
        "modified_at": _fmt(m["modified_at"]),
    }


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    central_db: AsyncSession = Depends(get_central_db_session),
    current_user=Depends(require_super_auth),
):
    """Create a new normal user (supreme auth required)."""
    rid = request_id(request)
    username = payload.username.strip()

    if len(username) < 3 or len(username) > 50:
        return error_json_response(
            USER_INVALID_USERNAME,
            "Username must be 3–50 characters",
            400, rid,
        )

    if len(payload.password) < 8:
        return error_json_response(
            USER_INVALID_PASSWORD,
            "Password must be at least 8 characters",
            400, rid,
        )

    try:
        async with central_db.begin():
            await _ensure_user_tables(central_db)

            existing = await central_db.execute(
                text("SELECT id FROM users WHERE username = :u LIMIT 1"),
                {"u": username},
            )
            if existing.fetchone():
                return error_json_response(
                    USER_ALREADY_EXISTS,
                    "A user with this username already exists",
                    409, rid,
                )

            email = payload.email.strip() if payload.email else None
            if email:
                existing_email = await central_db.execute(
                    text("SELECT id FROM users WHERE email = :e LIMIT 1"),
                    {"e": email},
                )
                if existing_email.fetchone():
                    return error_json_response(
                        USER_ALREADY_EXISTS,
                        "A user with this email already exists",
                        409, rid,
                    )

            now = utcnow().replace(tzinfo=None)
            await central_db.execute(
                text(
                    """
                    INSERT INTO users
                        (username, email, display_name, password_hash,
                         is_active, notes, created_by, created_at, modified_at)
                    VALUES
                        (:username, :email, :display_name, :password_hash,
                         1, :notes, :created_by, :created_at, :modified_at)
                    """
                ),
                {
                    "username": username,
                    "email": email,
                    "display_name": payload.display_name.strip() if payload.display_name else None,
                    "password_hash": hash_password(payload.password),
                    "notes": payload.notes.strip() if payload.notes else None,
                    "created_by": int(current_user.user_id),
                    "created_at": now,
                    "modified_at": now,
                },
            )

            row_result = await central_db.execute(
                text("SELECT * FROM users WHERE username = :u LIMIT 1"),
                {"u": username},
            )
            user_row = row_result.fetchone()

        return success_json_response(
            _row_to_user(user_row),
            request_id_value=rid,
            status_code=201,
            message="User created",
        )
    except Exception:
        await central_db.rollback()
        return error_json_response(USER_SERVICE_UNAVAILABLE, "Unable to create user", 503, rid)


@router.get("")
async def list_users(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    central_db: AsyncSession = Depends(get_central_db_session),
    _=Depends(require_super_auth),
):
    """List all users with pagination (supreme auth required)."""
    rid = request_id(request)
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    offset = (page - 1) * page_size

    try:
        await _ensure_user_tables(central_db)

        count_result = await central_db.execute(
            text("SELECT COUNT(*) AS total FROM users")
        )
        total = int(count_result.fetchone()._mapping["total"])

        rows_result = await central_db.execute(
            text(
                """
                SELECT id, username, email, display_name, is_active,
                       notes, created_by, created_at, modified_at
                  FROM users
                ORDER BY created_at DESC
                LIMIT :lim OFFSET :off
                """
            ),
            {"lim": page_size, "off": offset},
        )
        rows = rows_result.fetchall()
        await central_db.commit()

        return success_json_response(
            {
                "users": [_row_to_user(r) for r in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
            },
            request_id_value=rid,
            message="Users fetched",
        )
    except Exception:
        await central_db.rollback()
        return error_json_response(USER_SERVICE_UNAVAILABLE, "Unable to list users", 503, rid)


@router.get("/{user_id}")
async def get_user(
    user_id: int,
    request: Request,
    central_db: AsyncSession = Depends(get_central_db_session),
    _=Depends(require_super_auth),
):
    """Get user detail including their sessions (supreme auth required)."""
    rid = request_id(request)
    try:
        await _ensure_user_tables(central_db)

        user_result = await central_db.execute(
            text("SELECT * FROM users WHERE id = :id LIMIT 1"),
            {"id": user_id},
        )
        user_row = user_result.fetchone()
        if user_row is None:
            return error_json_response(USER_NOT_FOUND, "User not found", 404, rid)

        sessions_result = await central_db.execute(
            text(
                """
                SELECT id, issued_at, expires_at, last_used_at,
                       issued_ip, issued_user_agent, revoked_at
                  FROM user_sessions
                WHERE user_id = :uid
                ORDER BY issued_at DESC
                LIMIT 50
                """
            ),
            {"uid": user_id},
        )
        session_rows = sessions_result.fetchall()
        await central_db.commit()

        now = utcnow().replace(tzinfo=None)
        sessions = [
            {
                "id": s._mapping["id"],
                "issued_at": _fmt(s._mapping["issued_at"]),
                "expires_at": _fmt(s._mapping["expires_at"]),
                "last_used_at": _fmt(s._mapping["last_used_at"]),
                "issued_ip": s._mapping["issued_ip"],
                "issued_user_agent": s._mapping["issued_user_agent"],
                "revoked_at": _fmt(s._mapping["revoked_at"]),
                "is_active": (
                    s._mapping["revoked_at"] is None
                    and s._mapping["expires_at"] > now
                ),
            }
            for s in session_rows
        ]

        return success_json_response(
            {**_row_to_user(user_row), "sessions": sessions},
            request_id_value=rid,
            message="User fetched",
        )
    except Exception:
        await central_db.rollback()
        return error_json_response(USER_SERVICE_UNAVAILABLE, "Unable to fetch user", 503, rid)


@router.patch("/{user_id}/deactivate")
async def deactivate_user(
    user_id: int,
    request: Request,
    central_db: AsyncSession = Depends(get_central_db_session),
    _=Depends(require_super_auth),
):
    """Deactivate a user and revoke all their active sessions (supreme auth required)."""
    rid = request_id(request)
    try:
        async with central_db.begin():
            await _ensure_user_tables(central_db)

            user_result = await central_db.execute(
                text("SELECT id, is_active FROM users WHERE id = :id LIMIT 1"),
                {"id": user_id},
            )
            user_row = user_result.fetchone()
            if user_row is None:
                return error_json_response(USER_NOT_FOUND, "User not found", 404, rid)

            now = utcnow().replace(tzinfo=None)
            await central_db.execute(
                text(
                    "UPDATE users SET is_active = 0, modified_at = :now WHERE id = :id"
                ),
                {"id": user_id, "now": now},
            )
            # Revoke all active sessions
            await central_db.execute(
                text(
                    """
                    UPDATE user_sessions
                    SET revoked_at = :now, revoke_reason = 'user_deactivated'
                    WHERE user_id = :uid AND revoked_at IS NULL
                    """
                ),
                {"uid": user_id, "now": now},
            )

        return success_json_response(
            {"user_id": user_id, "is_active": False},
            request_id_value=rid,
            message="User deactivated",
        )
    except Exception:
        await central_db.rollback()
        return error_json_response(USER_SERVICE_UNAVAILABLE, "Unable to deactivate user", 503, rid)


@router.delete("/{user_id}/sessions/{session_id}")
async def revoke_session(
    user_id: int,
    session_id: int,
    request: Request,
    central_db: AsyncSession = Depends(get_central_db_session),
    _=Depends(require_super_auth),
):
    """Revoke a specific user session (supreme auth required)."""
    rid = request_id(request)
    try:
        async with central_db.begin():
            await _ensure_user_tables(central_db)

            session_result = await central_db.execute(
                text(
                    """
                    SELECT id, revoked_at FROM user_sessions
                    WHERE id = :sid AND user_id = :uid
                    LIMIT 1
                    """
                ),
                {"sid": session_id, "uid": user_id},
            )
            session_row = session_result.fetchone()
            if session_row is None:
                return error_json_response(
                    USER_SESSION_NOT_FOUND, "Session not found", 404, rid
                )

            if session_row._mapping["revoked_at"] is not None:
                return success_json_response(
                    {"session_id": session_id, "already_revoked": True},
                    request_id_value=rid,
                    message="Session already revoked",
                )

            now = utcnow().replace(tzinfo=None)
            await central_db.execute(
                text(
                    """
                    UPDATE user_sessions
                    SET revoked_at = :now, revoke_reason = 'admin_revoke'
                    WHERE id = :sid
                    """
                ),
                {"sid": session_id, "now": now},
            )

        return success_json_response(
            {"session_id": session_id, "revoked": True},
            request_id_value=rid,
            message="Session revoked",
        )
    except Exception:
        await central_db.rollback()
        return error_json_response(USER_SERVICE_UNAVAILABLE, "Unable to revoke session", 503, rid)


