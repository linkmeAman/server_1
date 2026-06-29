from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text

from app.core.database import Base


class DocumentRecord(Base):
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True)
    title = Column(String(255), nullable=False)
    status = Column(String(32), nullable=False, default="draft")
    owner_user_id = Column(String(64), nullable=True)
    current_version_id = Column(String(36), nullable=True)
    draft_snapshot_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class DocumentVersionRecord(Base):
    __tablename__ = "document_versions"

    id = Column(String(36), primary_key=True)
    document_id = Column(String(36), ForeignKey("documents.id"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    base_version_id = Column(String(36), nullable=True)
    snapshot_json = Column(JSON, nullable=False)
    snapshot_hash = Column(String(64), nullable=False)
    created_by = Column(String(64), nullable=True)
    change_summary = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


class DocumentPreviewJobRecord(Base):
    __tablename__ = "document_preview_jobs"

    id = Column(String(36), primary_key=True)
    document_id = Column(String(36), ForeignKey("documents.id"), nullable=False, index=True)
    document_version_id = Column(String(36), ForeignKey("document_versions.id"), nullable=True)
    status = Column(String(32), nullable=False)
    page_count = Column(Integer, nullable=False, default=0)
    preview_html = Column(Text, nullable=False)
    pages_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class DocumentExportRecord(Base):
    __tablename__ = "document_exports"

    id = Column(String(36), primary_key=True)
    document_id = Column(String(36), ForeignKey("documents.id"), nullable=False, index=True)
    document_version_id = Column(String(36), ForeignKey("document_versions.id"), nullable=True)
    format = Column(String(16), nullable=False)
    status = Column(String(32), nullable=False)
    requested_by = Column(String(64), nullable=True)
    mime_type = Column(String(128), nullable=False)
    file_name = Column(String(255), nullable=False)
    artifact_base64 = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class DocumentOperationRecord(Base):
    __tablename__ = "document_operations"

    id = Column(String(36), primary_key=True)
    document_id = Column(String(36), ForeignKey("documents.id"), nullable=False, index=True)
    base_version_id = Column(String(36), ForeignKey("document_versions.id"), nullable=True)
    client_session_id = Column(String(64), nullable=True)
    op_seq = Column(Integer, nullable=False, default=1)
    op_type = Column(String(64), nullable=False)
    op_payload_json = Column(JSON, nullable=False)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


class DocumentTemplateRecord(Base):
    __tablename__ = "document_templates"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    category = Column(String(64), nullable=False, default="general")
    description = Column(Text, nullable=True)
    template_snapshot_json = Column(JSON, nullable=False)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class DocumentCommentRecord(Base):
    __tablename__ = "document_comments"

    id = Column(String(36), primary_key=True)
    document_id = Column(String(36), ForeignKey("documents.id"), nullable=False, index=True)
    section_id = Column(String(36), nullable=True)
    anchor_text = Column(String(255), nullable=True)
    body = Column(Text, nullable=False)
    author = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="open")
    created_at = Column(DateTime(timezone=True), nullable=False)


DOCUMENT_TABLES = [
    DocumentRecord.__table__,
    DocumentVersionRecord.__table__,
    DocumentPreviewJobRecord.__table__,
    DocumentExportRecord.__table__,
    DocumentOperationRecord.__table__,
    DocumentTemplateRecord.__table__,
    DocumentCommentRecord.__table__,
]
