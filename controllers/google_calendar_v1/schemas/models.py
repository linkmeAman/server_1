"""Pydantic request models for Google Calendar V1 endpoints."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class DeleteMode(str, Enum):
    full = "full"
    next_instance = "next_instance"


class CreateCalendarEventRequest(BaseModel):
    actor_name: str = Field(..., min_length=1, max_length=255)
    actor_email: str = Field(..., min_length=3, max_length=255)
    event: Dict[str, Any] = Field(default_factory=dict)


class UpdateCalendarEventRequest(BaseModel):
    actor_name: str = Field(..., min_length=1, max_length=255)
    actor_email: str = Field(..., min_length=3, max_length=255)
    event: Dict[str, Any] = Field(default_factory=dict)
    log_row_id: Optional[int] = Field(default=None, ge=1)
