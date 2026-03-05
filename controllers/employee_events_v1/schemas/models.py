"""Pydantic models for Employee Events V1."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class AllowanceItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    amount: float = Field(...)


class CheckConflictRequest(BaseModel):
    date: str = Field(..., min_length=8, max_length=20)
    start_time: str = Field(..., min_length=4, max_length=16)
    end_time: str = Field(..., min_length=4, max_length=16)
    contact_id: int = Field(..., ge=1)
    exclude_event_id: Optional[int] = Field(default=None, ge=1)


class CreateEmployeeEventRequest(BaseModel):
    category: str = Field(..., min_length=1, max_length=255)
    contact_id: int = Field(..., ge=1)
    branch: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=5000)
    type: str = Field(..., min_length=1, max_length=255)
    lease_type: str = Field(..., min_length=0, max_length=255)
    amount: float = Field(...)
    deduction_amount: float = Field(...)
    date: str = Field(..., min_length=8, max_length=20)
    start_time: str = Field(..., min_length=4, max_length=16)
    end_time: str = Field(..., min_length=4, max_length=16)
    allowance: int = Field(..., ge=0)
    allowance_items: List[AllowanceItem] = Field(default_factory=list)


class UpdateEmployeeEventRequest(BaseModel):
    category: str = Field(..., min_length=1, max_length=255)
    contact_id: int = Field(..., ge=1)
    branch: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=5000)
    type: str = Field(..., min_length=1, max_length=255)
    lease_type: str = Field(..., min_length=0, max_length=255)
    amount: float = Field(...)
    deduction_amount: float = Field(...)
    date: str = Field(..., min_length=8, max_length=20)
    start_time: str = Field(..., min_length=4, max_length=16)
    end_time: str = Field(..., min_length=4, max_length=16)
    allowance: int = Field(..., ge=0)
    allowance_items: List[AllowanceItem] = Field(default_factory=list)


class ParkEmployeeEventRequest(BaseModel):
    park_value: int = Field(...)


class ApproveEmployeeEventRequest(BaseModel):
    status: Optional[int] = Field(default=None)
