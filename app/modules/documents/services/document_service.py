from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from difflib import unified_diff
from hashlib import sha256
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.documents.document_repository import DocumentRepository
from app.modules.documents.schemas.models import (
    DocumentCommentSummary,
    DocumentExportJob,
    DocumentOperationSummary,
    DocumentPreviewJob,
    DocumentPreviewPage,
    DocumentSnapshot,
    DocumentSummary,
    DocumentTemplateSummary,
    DocumentVersionSummary,
    PlaceholderResolutionResult,
    TrackedChangeItem,
    TrackedChangesResult,
    default_document_snapshot,
)
from app.modules.documents.schemas.requests import (
    CreateDocumentCommentRequest,
    CreateDocumentOperationRequest,
    CreateDocumentRequest,
    CreateDocumentTemplateRequest,
    CreateExportRequest,
    CreatePreviewRequest,
    ResolvePlaceholdersRequest,
    SaveDocumentRequest,
    TrackedChangesRequest,
    UpdateDocumentRequest,
)

from .export_service import ExportService
from .preview_service import PreviewService

_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")


class DocumentService:
    def __init__(
        self,
        repository: DocumentRepository | None = None,
        preview_service: PreviewService | None = None,
        export_service: ExportService | None = None,
    ) -> None:
        self._repository = repository or DocumentRepository()
        self._preview_service = preview_service or PreviewService()
        self._export_service = export_service or ExportService(self._preview_service)
        self._artifact_root = Path(__file__).resolve().parents[1] / "_artifacts" / "exports"
        self._artifact_root.mkdir(parents=True, exist_ok=True)

    async def list_documents(self, session: AsyncSession, offset: int = 0, limit: int = 50) -> list[DocumentSummary]:
        await self._repository.ensure_schema(session)
        return await self._repository.list_documents(session, offset=offset, limit=limit)

    async def create_document(self, session: AsyncSession, payload: CreateDocumentRequest) -> DocumentSummary:
        await self._repository.ensure_schema(session)
        snapshot = payload.snapshot or default_document_snapshot(payload.title)
        snapshot.title = payload.title
        return await self._repository.create_document(session, payload.title, payload.owner_user_id, snapshot)

    async def get_document(self, session: AsyncSession, document_id: str) -> DocumentSummary:
        await self._repository.ensure_schema(session)
        record = await self._require_document(session, document_id)
        return self._repository._to_document_summary(record)

    async def update_document(
        self,
        session: AsyncSession,
        document_id: str,
        payload: UpdateDocumentRequest,
    ) -> DocumentSummary:
        await self._repository.ensure_schema(session)
        record = await self._require_document(session, document_id)
        draft_snapshot = payload.draft_snapshot
        if draft_snapshot is not None and payload.title:
            draft_snapshot.title = payload.title
        return await self._repository.update_document(
            session,
            record,
            title=payload.title,
            status=payload.status,
            draft_snapshot=draft_snapshot,
        )

    async def delete_document(self, session: AsyncSession, document_id: str) -> None:
        await self._repository.ensure_schema(session)
        record = await self._require_document(session, document_id)
        await self._repository.delete_document(session, record)

    async def save_document(
        self,
        session: AsyncSession,
        document_id: str,
        payload: SaveDocumentRequest,
    ) -> DocumentVersionSummary:
        await self._repository.ensure_schema(session)
        record = await self._require_document(session, document_id)
        snapshot = payload.snapshot or DocumentSnapshot.model_validate(record.draft_snapshot_json)
        return await self._repository.create_version(
            session,
            record,
            snapshot=snapshot,
            created_by=payload.created_by,
            change_summary=payload.change_summary,
        )

    async def list_versions(self, session: AsyncSession, document_id: str) -> list[DocumentVersionSummary]:
        await self._repository.ensure_schema(session)
        await self._require_document(session, document_id)
        return await self._repository.list_versions(session, document_id)

    async def get_version(
        self,
        session: AsyncSession,
        document_id: str,
        version_id: str,
    ) -> DocumentVersionSummary:
        await self._repository.ensure_schema(session)
        version = await self._repository.get_version_record(session, document_id, version_id)
        if version is None:
            raise HTTPException(status_code=404, detail="Document version not found")
        return self._repository._to_version_summary(version)

    async def create_preview(
        self,
        session: AsyncSession,
        document_id: str,
        payload: CreatePreviewRequest,
    ):
        await self._require_document(session, document_id)
        snapshot = await self._resolve_snapshot(session, document_id, payload.document_version_id, payload.snapshot)
        preview_record = await self._repository.create_preview_job(
            session,
            document_id,
            payload.document_version_id,
            status="queued",
        )
        preview_record = await self._repository.update_preview_job(session, preview_record, status="running")
        try:
            preview = self._preview_service.render(snapshot)
        except Exception as exc:
            await self._repository.update_preview_job(
                session,
                preview_record,
                status="failed",
                completed_at=datetime.now(timezone.utc),
            )
            raise HTTPException(status_code=500, detail="Preview generation failed") from exc

        preview_record = await self._repository.update_preview_job(
            session,
            preview_record,
            status="completed",
            page_count=preview.page_count,
            preview_html=preview.preview_html,
            pages=preview.pages,
            completed_at=preview.completed_at,
        )
        return self._repository._to_preview_job(preview_record)


    async def get_preview(
        self,
        session: AsyncSession,
        document_id: str,
        preview_job_id: str,
    ):
        preview = await self._repository.get_preview_job_record(session, document_id, preview_job_id)
        if preview is None:
            raise HTTPException(status_code=404, detail="Preview job not found")
        return self._repository._to_preview_job(preview)


    async def get_preview_page(
        self,
        session: AsyncSession,
        document_id: str,
        preview_job_id: str,
        page_no: int,
    ):
        preview = await self._repository.get_preview_job_record(session, document_id, preview_job_id)
        if preview is None:
            raise HTTPException(status_code=404, detail="Preview job not found")
        if preview.status != "completed":
            raise HTTPException(status_code=409, detail="Preview job is not completed yet")

        preview_job = self._repository._to_preview_job(preview)
        for page in preview_job.pages:
            if page.page_number == page_no:
                return page
        raise HTTPException(status_code=404, detail="Preview page not found")


    async def create_export(
        self,
        session: AsyncSession,
        document_id: str,
        export_format: str,
        payload: CreateExportRequest,
    ):
        document = await self._require_document(session, document_id)
        snapshot = await self._resolve_snapshot(session, document_id, payload.document_version_id, payload.snapshot)
        file_stem = self._build_export_file_stem(payload.file_name, document.title)
        export_record = await self._repository.create_export_job(
            session,
            document_id,
            payload.document_version_id,
            export_format,
            status="queued",
            file_name=f"{file_stem}.{export_format.lower()}",
        )
        export_record = await self._repository.update_export_job(session, export_record, status="running")
        try:
            export_job = self._export_service.export(export_format, snapshot, file_stem)
            artifact_base64 = export_job.artifact_base64
            storage_key, checksum = self._persist_export_artifact(
                document_id,
                export_record.id,
                export_job.file_name,
                artifact_base64,
            )
        except Exception as exc:
            await self._repository.update_export_job(
                session,
                export_record,
                status="failed",
                completed_at=datetime.now(timezone.utc),
            )
            raise HTTPException(status_code=500, detail="Export generation failed") from exc

        export_record = await self._repository.update_export_job(
            session,
            export_record,
            status="completed",
            mime_type=export_job.mime_type,
            file_name=export_job.file_name,
            storage_key=storage_key,
            checksum=checksum,
            artifact_base64=None,
            completed_at=export_job.completed_at,
        )
        export_response = self._repository._to_export_job(export_record)
        export_response.artifact_base64 = artifact_base64
        return export_response


    async def get_export(
        self,
        session: AsyncSession,
        document_id: str,
        export_id: str,
    ):
        export_record = await self._repository.get_export_job_record(session, document_id, export_id)
        if export_record is None:
            raise HTTPException(status_code=404, detail="Export job not found")

        export_job = self._repository._to_export_job(export_record)
        if export_job.status == "completed" and not export_job.artifact_base64:
            export_job.artifact_base64 = self._load_export_artifact(export_record.storage_key)
            if export_job.artifact_base64 is None:
                raise HTTPException(status_code=404, detail="Export artifact not found")
        return export_job


    async def list_operations(self, session: AsyncSession, document_id: str, limit: int = 100) -> list[DocumentOperationSummary]:
        await self._repository.ensure_schema(session)
        await self._require_document(session, document_id)
        return await self._repository.list_operations(session, document_id, limit=limit)

    async def create_operation(
        self,
        session: AsyncSession,
        document_id: str,
        payload: CreateDocumentOperationRequest,
    ) -> DocumentOperationSummary:
        await self._repository.ensure_schema(session)
        await self._require_document(session, document_id)
        return await self._repository.create_operation(
            session,
            document_id=document_id,
            base_version_id=payload.base_version_id,
            client_session_id=payload.client_session_id,
            op_seq=payload.op_seq,
            op_type=payload.op_type,
            op_payload_json=payload.op_payload_json,
            created_by=payload.created_by,
        )

    async def list_templates(self, session: AsyncSession, limit: int = 100) -> list[DocumentTemplateSummary]:
        await self._repository.ensure_schema(session)
        return await self._repository.list_templates(session, limit=limit)

    async def create_template(self, session: AsyncSession, payload: CreateDocumentTemplateRequest) -> DocumentTemplateSummary:
        await self._repository.ensure_schema(session)
        return await self._repository.create_template(
            session,
            name=payload.name,
            category=payload.category,
            description=payload.description,
            snapshot=payload.snapshot,
            created_by=payload.created_by,
        )

    async def get_template(self, session: AsyncSession, template_id: str) -> DocumentTemplateSummary:
        await self._repository.ensure_schema(session)
        record = await self._repository.get_template_record(session, template_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Document template not found")
        return self._repository._to_template_summary(record)

    async def resolve_placeholders(
        self,
        session: AsyncSession,
        document_id: str,
        payload: ResolvePlaceholdersRequest,
    ) -> PlaceholderResolutionResult:
        await self._repository.ensure_schema(session)
        record = await self._require_document(session, document_id)
        snapshot = payload.snapshot or DocumentSnapshot.model_validate(record.draft_snapshot_json)
        missing_keys: set[str] = set()

        resolved_sections = []
        for section in snapshot.sections:
            content = section.content

            def replace(match: re.Match[str]) -> str:
                key = match.group(1)
                value = payload.values.get(key)
                if value is None:
                    missing_keys.add(key)
                    return match.group(0)
                return value

            section.content = _PLACEHOLDER_PATTERN.sub(replace, content)
            resolved_sections.append(section.content)

        return PlaceholderResolutionResult(
            snapshot=snapshot,
            resolved_sections=resolved_sections,
            missing_keys=sorted(missing_keys),
        )

    async def list_comments(self, session: AsyncSession, document_id: str) -> list[DocumentCommentSummary]:
        await self._repository.ensure_schema(session)
        await self._require_document(session, document_id)
        return await self._repository.list_comments(session, document_id)

    async def create_comment(
        self,
        session: AsyncSession,
        document_id: str,
        payload: CreateDocumentCommentRequest,
    ) -> DocumentCommentSummary:
        await self._repository.ensure_schema(session)
        await self._require_document(session, document_id)
        return await self._repository.create_comment(
            session,
            document_id=document_id,
            section_id=payload.section_id,
            anchor_text=payload.anchor_text,
            body=payload.body,
            author=payload.author,
            status=payload.status,
        )

    async def get_tracked_changes(
        self,
        session: AsyncSession,
        document_id: str,
        payload: TrackedChangesRequest,
    ) -> TrackedChangesResult:
        await self._repository.ensure_schema(session)
        record = await self._require_document(session, document_id)
        current_snapshot = payload.snapshot or DocumentSnapshot.model_validate(record.draft_snapshot_json)

        baseline_version_id = payload.baseline_version_id or record.current_version_id
        baseline_snapshot: DocumentSnapshot | None = None
        if baseline_version_id:
            version = await self._repository.get_version_record(session, document_id, baseline_version_id)
            if version is not None:
                baseline_snapshot = DocumentSnapshot.model_validate(version.snapshot_json)

        changes: list[TrackedChangeItem] = []
        if baseline_snapshot is None:
            changes.append(
                TrackedChangeItem(
                    kind="info",
                    title="No baseline version",
                    detail="Create at least one saved version to generate tracked change comparisons.",
                )
            )
            return TrackedChangesResult(baseline_version_id=None, changes=changes)

        if baseline_snapshot.title != current_snapshot.title:
            changes.append(
                TrackedChangeItem(
                    kind="title",
                    title="Document title changed",
                    detail=f"'{baseline_snapshot.title}' -> '{current_snapshot.title}'",
                )
            )

        if len(baseline_snapshot.sections) != len(current_snapshot.sections):
            changes.append(
                TrackedChangeItem(
                    kind="structure",
                    title="Section count changed",
                    detail=f"{len(baseline_snapshot.sections)} -> {len(current_snapshot.sections)} sections",
                )
            )

        baseline_by_id = {section.id: section for section in baseline_snapshot.sections}
        for section in current_snapshot.sections:
            original = baseline_by_id.get(section.id)
            if original is None:
                changes.append(
                    TrackedChangeItem(
                        kind="section_added",
                        section_id=section.id,
                        title=f"Section added: {section.title}",
                        detail="This section does not exist in the baseline version.",
                    )
                )
                continue
            if original.title != section.title:
                changes.append(
                    TrackedChangeItem(
                        kind="section_title",
                        section_id=section.id,
                        title=f"Section renamed: {section.title}",
                        detail=f"'{original.title}' -> '{section.title}'",
                    )
                )
            if original.content != section.content:
                diff_lines = list(
                    unified_diff(
                        original.content.splitlines(),
                        section.content.splitlines(),
                        fromfile="baseline",
                        tofile="draft",
                        lineterm="",
                    )
                )
                preview = "\n".join(diff_lines[:12]) if diff_lines else "Content changed"
                changes.append(
                    TrackedChangeItem(
                        kind="content",
                        section_id=section.id,
                        title=f"Content changed: {section.title}",
                        detail=preview,
                    )
                )

        removed_ids = set(baseline_by_id) - {section.id for section in current_snapshot.sections}
        for removed_id in sorted(removed_ids):
            removed = baseline_by_id[removed_id]
            changes.append(
                TrackedChangeItem(
                    kind="section_removed",
                    section_id=removed_id,
                    title=f"Section removed: {removed.title}",
                    detail="This baseline section no longer exists in the current draft.",
                )
            )

        return TrackedChangesResult(baseline_version_id=baseline_version_id, changes=changes)

    def _build_export_file_stem(self, requested_file_name: str | None, fallback_title: str | None) -> str:
        source_name = requested_file_name or fallback_title or "document"
        stem = Path(source_name).stem
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
        return stem or "document"

    def _persist_export_artifact(
        self,
        document_id: str,
        export_id: str,
        file_name: str,
        artifact_base64: str,
    ) -> tuple[str, str]:
        payload = base64.b64decode(artifact_base64.encode("ascii"))
        document_dir = self._artifact_root / document_id
        document_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", file_name).strip("-._") or f"{export_id}.bin"
        artifact_path = document_dir / f"{export_id}-{safe_name}"
        artifact_path.write_bytes(payload)
        return artifact_path.relative_to(self._artifact_root).as_posix(), sha256(payload).hexdigest()

    def _load_export_artifact(self, storage_key: str | None) -> str | None:
        if not storage_key:
            return None
        artifact_path = self._artifact_root / storage_key
        if not artifact_path.exists():
            return None
        return base64.b64encode(artifact_path.read_bytes()).decode("ascii")

    async def _require_document(self, session: AsyncSession, document_id: str):
        record = await self._repository.get_document_record(session, document_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return record

    async def _resolve_snapshot(
        self,
        session: AsyncSession,
        document_id: str,
        document_version_id: str | None,
        snapshot_override: DocumentSnapshot | None,
    ) -> tuple[DocumentSnapshot, str | None]:
        if snapshot_override is not None:
            return snapshot_override, document_version_id
        if document_version_id is not None:
            version = await self._repository.get_version_record(session, document_id, document_version_id)
            if version is None:
                raise HTTPException(status_code=404, detail="Document version not found")
            return DocumentSnapshot.model_validate(version.snapshot_json), version.id
        record = await self._require_document(session, document_id)
        return DocumentSnapshot.model_validate(record.draft_snapshot_json), record.current_version_id
