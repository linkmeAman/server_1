from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_main_db_session
from app.core.response import success_response

from .schemas.requests import (
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
from .services.document_service import DocumentService

router = APIRouter(prefix="/api/documents", tags=["documents"])

template_router = APIRouter(prefix="/api/document-templates", tags=["document-templates"])

_service = DocumentService()


@router.get("")
async def list_documents(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    documents = await _service.list_documents(main_db, offset=offset, limit=limit)
    return success_response({"documents": [item.model_dump(mode="json") for item in documents]})


@router.post("")
async def create_document(
    payload: CreateDocumentRequest,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    document = await _service.create_document(main_db, payload)
    return success_response({"document": document.model_dump(mode="json")}, "Document created")


@router.get("/{document_id}")
async def get_document(
    document_id: str,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    document = await _service.get_document(main_db, document_id)
    return success_response({"document": document.model_dump(mode="json")})


@router.patch("/{document_id}")
async def update_document(
    document_id: str,
    payload: UpdateDocumentRequest,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    document = await _service.update_document(main_db, document_id, payload)
    return success_response({"document": document.model_dump(mode="json")}, "Document updated")


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    await _service.delete_document(main_db, document_id)
    return success_response({"deleted": True}, "Document deleted")


@router.post("/{document_id}/save")
async def save_document(
    document_id: str,
    payload: SaveDocumentRequest,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    version = await _service.save_document(main_db, document_id, payload)
    return success_response({"version": version.model_dump(mode="json")}, "Document version created")


@router.post("/{document_id}/versions")
async def create_document_version(
    document_id: str,
    payload: SaveDocumentRequest,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    version = await _service.save_document(main_db, document_id, payload)
    return success_response({"version": version.model_dump(mode="json")}, "Document version created")


@router.get("/{document_id}/versions")
async def list_document_versions(
    document_id: str,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    versions = await _service.list_versions(main_db, document_id)
    return success_response({"versions": [item.model_dump(mode="json") for item in versions]})


@router.get("/{document_id}/versions/{version_id}")
async def get_document_version(
    document_id: str,
    version_id: str,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    version = await _service.get_version(main_db, document_id, version_id)
    return success_response({"version": version.model_dump(mode="json")})


@router.post("/{document_id}/ops")
async def create_document_operation(
    document_id: str,
    payload: CreateDocumentOperationRequest,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    operation = await _service.create_operation(main_db, document_id, payload)
    return success_response({"operation": operation.model_dump(mode="json")}, "Document operation logged")


@router.get("/{document_id}/ops")
async def list_document_operations(
    document_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    operations = await _service.list_operations(main_db, document_id, limit=limit)
    return success_response({"operations": [item.model_dump(mode="json") for item in operations]})


@router.post("/{document_id}/preview")
async def create_document_preview(
    document_id: str,
    payload: CreatePreviewRequest,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    preview = await _service.create_preview(main_db, document_id, payload)
    return success_response({"preview": preview.model_dump(mode="json")}, "Document preview created")


@router.get("/{document_id}/preview/{preview_job_id}")
async def get_document_preview(
    document_id: str,
    preview_job_id: str,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    preview = await _service.get_preview(main_db, document_id, preview_job_id)
    return success_response({"preview": preview.model_dump(mode="json")})


@router.get("/{document_id}/preview/{preview_job_id}/pages/{page_no}")
async def get_document_preview_page(
    document_id: str,
    preview_job_id: str,
    page_no: int,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    page = await _service.get_preview_page(main_db, document_id, preview_job_id, page_no)
    return success_response({"page": page.model_dump(mode="json")})


@router.post("/{document_id}/exports/{export_format}")
async def create_document_export(
    document_id: str,
    export_format: str,
    payload: CreateExportRequest,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    export = await _service.create_export(main_db, document_id, export_format, payload)
    return success_response({"export": export.model_dump(mode="json")}, "Document export created")


@router.get("/{document_id}/exports/{export_id}")
async def get_document_export(
    document_id: str,
    export_id: str,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    export = await _service.get_export(main_db, document_id, export_id)
    return success_response({"export": export.model_dump(mode="json")})


@router.post("/{document_id}/placeholders/resolve")
async def resolve_document_placeholders(
    document_id: str,
    payload: ResolvePlaceholdersRequest,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    result = await _service.resolve_placeholders(main_db, document_id, payload)
    return success_response({"resolution": result.model_dump(mode="json")}, "Placeholders resolved")


@router.get("/{document_id}/comments")
async def list_document_comments(
    document_id: str,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    comments = await _service.list_comments(main_db, document_id)
    return success_response({"comments": [item.model_dump(mode="json") for item in comments]})


@router.post("/{document_id}/comments")
async def create_document_comment(
    document_id: str,
    payload: CreateDocumentCommentRequest,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    comment = await _service.create_comment(main_db, document_id, payload)
    return success_response({"comment": comment.model_dump(mode="json")}, "Comment created")


@router.post("/{document_id}/tracked-changes")
async def get_document_tracked_changes(
    document_id: str,
    payload: TrackedChangesRequest,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    result = await _service.get_tracked_changes(main_db, document_id, payload)
    return success_response({"tracked_changes": result.model_dump(mode="json")})


@template_router.get("")
async def list_document_templates(
    limit: int = Query(default=100, ge=1, le=300),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    templates = await _service.list_templates(main_db, limit=limit)
    return success_response({"templates": [item.model_dump(mode="json") for item in templates]})


@template_router.post("")
async def create_document_template(
    payload: CreateDocumentTemplateRequest,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    template = await _service.create_template(main_db, payload)
    return success_response({"template": template.model_dump(mode="json")}, "Template created")


@template_router.get("/{template_id}")
async def get_document_template(
    template_id: str,
    main_db: AsyncSession = Depends(get_main_db_session),
):
    template = await _service.get_template(main_db, template_id)
    return success_response({"template": template.model_dump(mode="json")})
