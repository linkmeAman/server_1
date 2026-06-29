from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .models import DocumentSnapshot


class CreateDocumentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    owner_user_id: str | None = None
    snapshot: DocumentSnapshot | None = None


class UpdateDocumentRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    status: str | None = None
    draft_snapshot: DocumentSnapshot | None = None


class SaveDocumentRequest(BaseModel):
    snapshot: DocumentSnapshot | None = None
    created_by: str | None = None
    change_summary: str | None = None


class CreatePreviewRequest(BaseModel):
    document_version_id: str | None = None
    snapshot: DocumentSnapshot | None = None


class CreateExportRequest(BaseModel):
    document_version_id: str | None = None
    snapshot: DocumentSnapshot | None = None
    requested_by: str | None = None
    file_name: str | None = None


class CreateDocumentOperationRequest(BaseModel):
    base_version_id: str | None = None
    client_session_id: str | None = None
    op_seq: int = 1
    op_type: str = Field(min_length=1, max_length=64)
    op_payload_json: dict[str, Any] = Field(default_factory=dict)
    created_by: str | None = None


class CreateDocumentTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    category: str = Field(default="general", min_length=1, max_length=64)
    description: str | None = None
    snapshot: DocumentSnapshot
    created_by: str | None = None


class ResolvePlaceholdersRequest(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)
    snapshot: DocumentSnapshot | None = None


class CreateDocumentCommentRequest(BaseModel):
    section_id: str | None = None
    anchor_text: str | None = None
    body: str = Field(min_length=1)
    author: str | None = None
    status: str = "open"


class TrackedChangesRequest(BaseModel):
    baseline_version_id: str | None = None
    snapshot: DocumentSnapshot | None = None
