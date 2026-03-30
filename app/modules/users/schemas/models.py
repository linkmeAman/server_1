"""Pydantic schemas for user management."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, field_validator


class CreateUserRequest(BaseModel):
    username: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    password: str
    notes: Optional[str] = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3 or len(v) > 50:
            raise ValueError("Username must be 3–50 characters")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class UserResponse(BaseModel):
    id: int
    username: str
    display_name: Optional[str]
    email: Optional[str]
    is_active: bool
    notes: Optional[str]
    created_by: int
    created_at: Optional[str]
    modified_at: Optional[str]


class SessionInfo(BaseModel):
    id: int
    issued_at: Optional[str]
    expires_at: Optional[str]
    last_used_at: Optional[str]
    issued_ip: Optional[str]
    issued_user_agent: Optional[str]
    revoked_at: Optional[str]
    is_active: bool


class UserDetailResponse(UserResponse):
    sessions: List[SessionInfo] = []


class UserListResponse(BaseModel):
    users: List[UserResponse]
    total: int
    page: int
    page_size: int
