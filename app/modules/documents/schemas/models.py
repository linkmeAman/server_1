from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class DocumentMargins(BaseModel):
    top: int = 72
    right: int = 72
    bottom: int = 72
    left: int = 72
    gutter: int = 0
    bleed: int = 0


class DocumentLayout(BaseModel):
    page_size: str = "A4"
    orientation: str = "portrait"
    margins: DocumentMargins = Field(default_factory=DocumentMargins)
    different_first_page: bool = False
    different_odd_even: bool = False


class DocumentSection(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = "Section"
    content: str = ""
    layout: DocumentLayout = Field(default_factory=DocumentLayout)


class DocumentSnapshot(BaseModel):
    title: str = "Untitled document"
    sections: list[DocumentSection] = Field(default_factory=list)
    placeholders: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ensure_one_section(self) -> "DocumentSnapshot":
        if not self.sections:
            self.sections = [DocumentSection(title="Section 1")]
        return self


class DocumentSummary(BaseModel):
    id: str
    title: str
    status: str
    owner_user_id: str | None = None
    current_version_id: str | None = None
    draft_snapshot: DocumentSnapshot
    created_at: datetime
    updated_at: datetime


class DocumentVersionSummary(BaseModel):
    id: str
    document_id: str
    version_number: int
    base_version_id: str | None = None
    snapshot_hash: str
    created_by: str | None = None
    change_summary: str | None = None
    created_at: datetime


class DocumentPreviewPage(BaseModel):
    page_number: int
    section_id: str
    title: str
    html: str


class DocumentPreviewJob(BaseModel):
    id: str
    document_id: str
    document_version_id: str | None = None
    status: str
    page_count: int
    preview_html: str
    pages: list[DocumentPreviewPage]
    created_at: datetime
    completed_at: datetime | None = None


class DocumentExportJob(BaseModel):
    id: str
    document_id: str
    document_version_id: str | None = None
    format: str
    status: str
    mime_type: str
    file_name: str
    artifact_base64: str
    created_at: datetime
    completed_at: datetime | None = None


class DocumentOperationSummary(BaseModel):
    id: str
    document_id: str
    base_version_id: str | None = None
    client_session_id: str | None = None
    op_seq: int
    op_type: str
    op_payload_json: dict[str, Any]
    created_by: str | None = None
    created_at: datetime


class DocumentTemplateSummary(BaseModel):
    id: str
    name: str
    category: str
    description: str | None = None
    template_snapshot: DocumentSnapshot
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class DocumentCommentSummary(BaseModel):
    id: str
    document_id: str
    section_id: str | None = None
    anchor_text: str | None = None
    body: str
    author: str | None = None
    status: str
    created_at: datetime


class PlaceholderResolutionResult(BaseModel):
    snapshot: DocumentSnapshot
    resolved_sections: list[str]
    missing_keys: list[str]


class TrackedChangeItem(BaseModel):
    kind: str
    section_id: str | None = None
    title: str
    detail: str


class TrackedChangesResult(BaseModel):
    baseline_version_id: str | None = None
    changes: list[TrackedChangeItem]


def default_document_snapshot(title: str = "Untitled document") -> DocumentSnapshot:
    return DocumentSnapshot(
        title=title,
        sections=[
            DocumentSection(
                title="Section 1",
                content="Start writing here.\n\nThis first implementation keeps the document model JSON-backed and section-aware.",
            )
        ],
    )
