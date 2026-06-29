from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base

from .models import (
    DOCUMENT_TABLES,
    DocumentCommentRecord,
    DocumentExportRecord,
    DocumentOperationRecord,
    DocumentPreviewJobRecord,
    DocumentRecord,
    DocumentTemplateRecord,
    DocumentVersionRecord,
)
from .schemas.models import (
    DocumentCommentSummary,
    DocumentExportJob,
    DocumentOperationSummary,
    DocumentPreviewJob,
    DocumentPreviewPage,
    DocumentSnapshot,
    DocumentSummary,
    DocumentTemplateSummary,
    DocumentVersionSummary,
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class DocumentRepository:
    async def ensure_schema(self, session: AsyncSession) -> None:
        await session.run_sync(
            lambda sync_session: Base.metadata.create_all(
                bind=sync_session.connection(),
                tables=DOCUMENT_TABLES,
                checkfirst=True,
            )
        )

    async def list_documents(self, session: AsyncSession, offset: int = 0, limit: int = 50) -> list[DocumentSummary]:
        stmt = (
            select(DocumentRecord)
            .order_by(DocumentRecord.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [self._to_document_summary(row) for row in rows]

    async def create_document(
        self,
        session: AsyncSession,
        title: str,
        owner_user_id: str | None,
        snapshot: DocumentSnapshot,
    ) -> DocumentSummary:
        now = _now_utc()
        record = DocumentRecord(
            id=str(uuid4()),
            title=title,
            status="draft",
            owner_user_id=owner_user_id,
            current_version_id=None,
            draft_snapshot_json=snapshot.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return self._to_document_summary(record)

    async def get_document_record(self, session: AsyncSession, document_id: str) -> DocumentRecord | None:
        return await session.get(DocumentRecord, document_id)

    async def update_document(
        self,
        session: AsyncSession,
        record: DocumentRecord,
        title: str | None,
        status: str | None,
        draft_snapshot: DocumentSnapshot | None,
    ) -> DocumentSummary:
        if title is not None:
            record.title = title
        if status is not None:
            record.status = status
        if draft_snapshot is not None:
            record.draft_snapshot_json = draft_snapshot.model_dump(mode="json")
        record.updated_at = _now_utc()
        await session.commit()
        await session.refresh(record)
        return self._to_document_summary(record)

    async def delete_document(self, session: AsyncSession, record: DocumentRecord) -> None:
        await session.delete(record)
        await session.commit()

    async def create_version(
        self,
        session: AsyncSession,
        record: DocumentRecord,
        snapshot: DocumentSnapshot,
        created_by: str | None,
        change_summary: str | None,
    ) -> DocumentVersionSummary:
        next_version_stmt = select(func.max(DocumentVersionRecord.version_number)).where(
            DocumentVersionRecord.document_id == record.id
        )
        max_version = (await session.execute(next_version_stmt)).scalar_one_or_none() or 0
        snapshot_payload = snapshot.model_dump(mode="json")
        version = DocumentVersionRecord(
            id=str(uuid4()),
            document_id=record.id,
            version_number=int(max_version) + 1,
            base_version_id=record.current_version_id,
            snapshot_json=snapshot_payload,
            snapshot_hash=sha256(str(snapshot_payload).encode("utf-8")).hexdigest(),
            created_by=created_by,
            change_summary=change_summary,
            created_at=_now_utc(),
        )
        record.current_version_id = version.id
        record.draft_snapshot_json = snapshot_payload
        record.updated_at = _now_utc()
        session.add(version)
        await session.commit()
        await session.refresh(version)
        return self._to_version_summary(version)

    async def list_versions(self, session: AsyncSession, document_id: str) -> list[DocumentVersionSummary]:
        stmt = (
            select(DocumentVersionRecord)
            .where(DocumentVersionRecord.document_id == document_id)
            .order_by(DocumentVersionRecord.version_number.desc())
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [self._to_version_summary(row) for row in rows]

    async def get_version_record(
        self,
        session: AsyncSession,
        document_id: str,
        version_id: str,
    ) -> DocumentVersionRecord | None:
        stmt = select(DocumentVersionRecord).where(
            DocumentVersionRecord.document_id == document_id,
            DocumentVersionRecord.id == version_id,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def create_preview_job(
        self,
        session: AsyncSession,
        document_id: str,
        document_version_id: str | None,
        *,
        status: str = "queued",
        page_count: int | None = None,
        preview_html: str | None = None,
        pages: list[DocumentPreviewPage] | None = None,
        completed_at: datetime | None = None,
    ) -> DocumentPreviewJobRecord:
        now = datetime.now(timezone.utc)
        record = DocumentPreviewJobRecord(
            id=str(uuid4()),
            document_id=document_id,
            document_version_id=document_version_id,
            status=status,
            page_count=page_count or 0,
            preview_html=preview_html,
            pages_json=[page.model_dump(mode="json") for page in pages] if pages is not None else None,
            created_at=now,
            completed_at=completed_at or (now if status in {"completed", "failed"} else None),
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return record

    async def get_preview_job_record(
        self,
        session: AsyncSession,
        document_id: str,
        preview_job_id: str,
    ) -> DocumentPreviewJobRecord | None:
        result = await session.execute(
            select(DocumentPreviewJobRecord).where(
                DocumentPreviewJobRecord.id == preview_job_id,
                DocumentPreviewJobRecord.document_id == document_id,
            )
        )
        return result.scalar_one_or_none()

    async def update_preview_job(
        self,
        session: AsyncSession,
        preview_job: DocumentPreviewJobRecord,
        *,
        status: str,
        page_count: int | None = None,
        preview_html: str | None = None,
        pages: list[DocumentPreviewPage] | None = None,
        completed_at: datetime | None = None,
    ) -> DocumentPreviewJobRecord:
        preview_job.status = status
        if page_count is not None:
            preview_job.page_count = page_count
        if preview_html is not None:
            preview_job.preview_html = preview_html
        if pages is not None:
            preview_job.pages_json = [page.model_dump(mode="json") for page in pages]
        if completed_at is not None:
            preview_job.completed_at = completed_at
        elif status in {"completed", "failed"}:
            preview_job.completed_at = datetime.now(timezone.utc)
        session.add(preview_job)
        await session.commit()
        await session.refresh(preview_job)
        return preview_job

    async def create_export_job(
        self,
        session: AsyncSession,
        document_id: str,
        document_version_id: str | None,
        export_format: str,
        *,
        status: str = "queued",
        mime_type: str | None = None,
        file_name: str | None = None,
        storage_key: str | None = None,
        checksum: str | None = None,
        artifact_base64: str | None = None,
        completed_at: datetime | None = None,
    ) -> DocumentExportRecord:
        now = datetime.now(timezone.utc)
        record = DocumentExportRecord(
            id=str(uuid4()),
            document_id=document_id,
            document_version_id=document_version_id,
            format=export_format,
            status=status,
            mime_type=mime_type,
            file_name=file_name,
            storage_key=storage_key,
            checksum=checksum,
            artifact_base64=artifact_base64,
            created_at=now,
            completed_at=completed_at or (now if status in {"completed", "failed"} else None),
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return record

    async def get_export_job_record(
        self,
        session: AsyncSession,
        document_id: str,
        export_id: str,
    ) -> DocumentExportRecord | None:
        result = await session.execute(
            select(DocumentExportRecord).where(
                DocumentExportRecord.id == export_id,
                DocumentExportRecord.document_id == document_id,
            )
        )
        return result.scalar_one_or_none()

    async def update_export_job(
        self,
        session: AsyncSession,
        export_job: DocumentExportRecord,
        *,
        status: str,
        mime_type: str | None = None,
        file_name: str | None = None,
        storage_key: str | None = None,
        checksum: str | None = None,
        artifact_base64: str | None = None,
        completed_at: datetime | None = None,
    ) -> DocumentExportRecord:
        export_job.status = status
        if mime_type is not None:
            export_job.mime_type = mime_type
        if file_name is not None:
            export_job.file_name = file_name
        if storage_key is not None:
            export_job.storage_key = storage_key
        if checksum is not None:
            export_job.checksum = checksum
        export_job.artifact_base64 = artifact_base64
        if completed_at is not None:
            export_job.completed_at = completed_at
        elif status in {"completed", "failed"}:
            export_job.completed_at = datetime.now(timezone.utc)
        session.add(export_job)
        await session.commit()
        await session.refresh(export_job)
        return export_job

    async def list_operations(self, session: AsyncSession, document_id: str, limit: int = 100) -> list[DocumentOperationSummary]:
        stmt = (
            select(DocumentOperationRecord)
            .where(DocumentOperationRecord.document_id == document_id)
            .order_by(DocumentOperationRecord.created_at.desc(), DocumentOperationRecord.op_seq.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [self._to_operation_summary(row) for row in rows]

    async def create_operation(
        self,
        session: AsyncSession,
        document_id: str,
        base_version_id: str | None,
        client_session_id: str | None,
        op_seq: int,
        op_type: str,
        op_payload_json: dict,
        created_by: str | None,
    ) -> DocumentOperationSummary:
        record = DocumentOperationRecord(
            id=str(uuid4()),
            document_id=document_id,
            base_version_id=base_version_id,
            client_session_id=client_session_id,
            op_seq=op_seq,
            op_type=op_type,
            op_payload_json=op_payload_json,
            created_by=created_by,
            created_at=_now_utc(),
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return self._to_operation_summary(record)

    async def list_templates(self, session: AsyncSession, limit: int = 100) -> list[DocumentTemplateSummary]:
        stmt = select(DocumentTemplateRecord).order_by(DocumentTemplateRecord.updated_at.desc()).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
        return [self._to_template_summary(row) for row in rows]

    async def create_template(
        self,
        session: AsyncSession,
        name: str,
        category: str,
        description: str | None,
        snapshot: DocumentSnapshot,
        created_by: str | None,
    ) -> DocumentTemplateSummary:
        now = _now_utc()
        record = DocumentTemplateRecord(
            id=str(uuid4()),
            name=name,
            category=category,
            description=description,
            template_snapshot_json=snapshot.model_dump(mode="json"),
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return self._to_template_summary(record)

    async def get_template_record(self, session: AsyncSession, template_id: str) -> DocumentTemplateRecord | None:
        return await session.get(DocumentTemplateRecord, template_id)

    async def list_comments(self, session: AsyncSession, document_id: str) -> list[DocumentCommentSummary]:
        stmt = (
            select(DocumentCommentRecord)
            .where(DocumentCommentRecord.document_id == document_id)
            .order_by(DocumentCommentRecord.created_at.desc())
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [self._to_comment_summary(row) for row in rows]

    async def create_comment(
        self,
        session: AsyncSession,
        document_id: str,
        section_id: str | None,
        anchor_text: str | None,
        body: str,
        author: str | None,
        status: str,
    ) -> DocumentCommentSummary:
        record = DocumentCommentRecord(
            id=str(uuid4()),
            document_id=document_id,
            section_id=section_id,
            anchor_text=anchor_text,
            body=body,
            author=author,
            status=status,
            created_at=_now_utc(),
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return self._to_comment_summary(record)

    def _to_document_summary(self, record: DocumentRecord) -> DocumentSummary:
        return DocumentSummary(
            id=record.id,
            title=record.title,
            status=record.status,
            owner_user_id=record.owner_user_id,
            current_version_id=record.current_version_id,
            draft_snapshot=DocumentSnapshot.model_validate(record.draft_snapshot_json),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _to_version_summary(self, record: DocumentVersionRecord) -> DocumentVersionSummary:
        return DocumentVersionSummary(
            id=record.id,
            document_id=record.document_id,
            version_number=record.version_number,
            base_version_id=record.base_version_id,
            snapshot_hash=record.snapshot_hash,
            created_by=record.created_by,
            change_summary=record.change_summary,
            created_at=record.created_at,
        )

    def _to_preview_job(self, record: DocumentPreviewJobRecord) -> DocumentPreviewJob:
        pages = [DocumentPreviewPage.model_validate(page) for page in (record.pages_json or [])]
        return DocumentPreviewJob(
            id=record.id,
            document_id=record.document_id,
            document_version_id=record.document_version_id,
            status=record.status,
            page_count=record.page_count,
            preview_html=record.preview_html,
            pages=pages,
            created_at=record.created_at,
            completed_at=record.completed_at,
        )

    def _to_export_job(self, record: DocumentExportRecord) -> DocumentExportJob:
        return DocumentExportJob(
            id=record.id,
            document_id=record.document_id,
            document_version_id=record.document_version_id,
            format=record.format,
            status=record.status,
            mime_type=record.mime_type,
            file_name=record.file_name,
            artifact_base64=record.artifact_base64,
            created_at=record.created_at,
            completed_at=record.completed_at,
        )

    def _to_operation_summary(self, record: DocumentOperationRecord) -> DocumentOperationSummary:
        return DocumentOperationSummary(
            id=record.id,
            document_id=record.document_id,
            base_version_id=record.base_version_id,
            client_session_id=record.client_session_id,
            op_seq=record.op_seq,
            op_type=record.op_type,
            op_payload_json=record.op_payload_json or {},
            created_by=record.created_by,
            created_at=record.created_at,
        )

    def _to_template_summary(self, record: DocumentTemplateRecord) -> DocumentTemplateSummary:
        return DocumentTemplateSummary(
            id=record.id,
            name=record.name,
            category=record.category,
            description=record.description,
            template_snapshot=DocumentSnapshot.model_validate(record.template_snapshot_json),
            created_by=record.created_by,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _to_comment_summary(self, record: DocumentCommentRecord) -> DocumentCommentSummary:
        return DocumentCommentSummary(
            id=record.id,
            document_id=record.document_id,
            section_id=record.section_id,
            anchor_text=record.anchor_text,
            body=record.body,
            author=record.author,
            status=record.status,
            created_at=record.created_at,
        )
