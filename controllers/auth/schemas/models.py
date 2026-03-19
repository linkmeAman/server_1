"""Pydantic schemas for auth v2 endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CheckContactRequest(BaseModel):
    country_code: str = Field(..., min_length=1, max_length=8)
    mobile: str = Field(..., min_length=6, max_length=20)


class EmployeeSummary(BaseModel):
    employee_id: int
    display_label: str
    position_id: Optional[int] = None
    position: Optional[str] = None
    department_id: Optional[int] = None
    department: Optional[str] = None


class CheckContactResponseData(BaseModel):
    contact_id: int
    contact_name: str
    employees: List[EmployeeSummary]


class LoginEmployeeRequest(BaseModel):
    country_code: str = Field(..., min_length=1, max_length=8)
    mobile: str = Field(..., min_length=6, max_length=20)
    employee_id: int
    password: str = Field(..., min_length=1)


class SupremeStatusData(BaseModel):
    supreme_required: bool
    total_users: int


class SupremeCreateRequest(BaseModel):
    country_code: str = Field(..., min_length=1, max_length=8)
    mobile: str = Field(..., min_length=6, max_length=20)
    password: str = Field(..., min_length=8, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=120)


class SupremeLoginRequest(BaseModel):
    country_code: str = Field(..., min_length=1, max_length=8)
    mobile: str = Field(..., min_length=6, max_length=20)
    password: str = Field(..., min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class PasswordChangedRequest(BaseModel):
    user_id: int
    reason: Optional[str] = None


class RoleSummary(BaseModel):
    role_code: str
    role_name: str


class TokenPairResponseData(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    user_id: int
    contact_id: int
    employee_id: int
    roles: List[RoleSummary]
    position_id: Optional[int] = None
    position: Optional[str] = None
    department_id: Optional[int] = None
    department: Optional[str] = None
    permissions: List[str] = Field(default_factory=list)
    is_super: bool = False
    permissions_version: int = 0
    permissions_schema_version: int = 1


class CurrentV2User(BaseModel):
    sub: str
    user_id: int
    contact_id: int
    employee_id: int
    roles: List[RoleSummary] = Field(default_factory=list)
    mobile: str
    jti: str
    iat: int
    exp: int
    iss: str
    aud: str
    auth_ver: int
    position_id: Optional[int] = None
    position: Optional[str] = None
    department_id: Optional[int] = None
    department: Optional[str] = None
    permissions: List[str] = Field(default_factory=list)
    is_super: bool = False
    permissions_version: int = 0
    permissions_schema_version: int = 1
    typ: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)
