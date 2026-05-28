"""Notification event schemas used by the SSE broker and API routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

NotificationSeverity = Literal["info", "success", "warning", "error", "critical"]
NotificationRecipientScope = Literal["assigned_to_me", "managed_team", "branch"]

FOLLOWUP_SOURCE = "followups"
FOLLOWUP_EVENT_TYPE = "FOLLOWUP_REMINDER_DUE"
DEFAULT_FOLLOWUP_OFFSETS = [5]
DEFAULT_RECIPIENT_SCOPE: NotificationRecipientScope = "assigned_to_me"


class NotificationEvent(BaseModel):
    event_id: StrictStr = Field(default_factory=lambda: str(uuid4()))
    request_id: StrictStr
    event_type: StrictStr
    severity: NotificationSeverity
    source: StrictStr
    timestamp: StrictStr = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    message: StrictStr
    metadata: dict[str, Any] = Field(default_factory=dict)
    user_id: int | str | None = None
    group_key: str | None = None
    dedupe_key: str | None = None
    read: bool = False

    model_config = ConfigDict(extra="allow")

    @field_validator("request_id", "event_type", "source", "message")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("field must not be blank")
        return normalized


class NotificationPublishRequest(BaseModel):
    request_id: StrictStr
    event_type: StrictStr
    severity: NotificationSeverity = "info"
    source: StrictStr = "debug"
    message: StrictStr
    metadata: dict[str, Any] = Field(default_factory=dict)
    user_id: int | str | None = None
    group_key: str | None = None
    dedupe_key: str | None = None

    model_config = ConfigDict(extra="ignore")


class NotificationPreferencePatch(BaseModel):
    desktop_enabled: bool | None = None
    silent_mode: bool | None = None
    toast_enabled: bool | None = None
    minimum_toast_severity: NotificationSeverity | None = None
    minimum_desktop_severity: NotificationSeverity | None = None
    center_severity_filter: NotificationSeverity | Literal["all"] | None = None

    model_config = ConfigDict(extra="ignore")


class NotificationPreferences(BaseModel):
    toast_enabled: bool = True
    desktop_enabled: bool = True
    silent_mode: bool = False
    minimum_toast_severity: NotificationSeverity = "info"
    minimum_desktop_severity: NotificationSeverity = "info"
    center_severity_filter: NotificationSeverity | Literal["all"] = "all"

    model_config = ConfigDict(extra="ignore")


class NotificationDeliveryRule(BaseModel):
    source: StrictStr
    event_type: StrictStr
    enabled: bool = True
    reminder_offsets_minutes: list[int] = Field(
        default_factory=lambda: DEFAULT_FOLLOWUP_OFFSETS.copy()
    )
    recipient_scope: NotificationRecipientScope = DEFAULT_RECIPIENT_SCOPE
    updated_at: str | None = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("source", "event_type")
    @classmethod
    def strip_rule_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("field must not be blank")
        return normalized

    @field_validator("reminder_offsets_minutes")
    @classmethod
    def normalize_offsets(cls, value: list[int]) -> list[int]:
        offsets = sorted({int(item) for item in value if 0 <= int(item) <= 1440})
        return offsets or DEFAULT_FOLLOWUP_OFFSETS.copy()


class NotificationRulePatch(BaseModel):
    source: StrictStr
    event_type: StrictStr
    enabled: bool | None = None
    reminder_offsets_minutes: list[int] | None = None
    recipient_scope: NotificationRecipientScope | None = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("source", "event_type")
    @classmethod
    def strip_patch_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("field must not be blank")
        return normalized

    @field_validator("reminder_offsets_minutes")
    @classmethod
    def normalize_patch_offsets(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        offsets = sorted({int(item) for item in value if 0 <= int(item) <= 1440})
        return offsets or DEFAULT_FOLLOWUP_OFFSETS.copy()
