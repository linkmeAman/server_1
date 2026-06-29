from __future__ import annotations

from pydantic import BaseModel

from .models import DocumentExportJob, DocumentPreviewJob, DocumentSummary, DocumentVersionSummary


class DocumentListPayload(BaseModel):
    documents: list[DocumentSummary]


class DocumentVersionsPayload(BaseModel):
    versions: list[DocumentVersionSummary]


class DocumentPreviewPayload(BaseModel):
    preview: DocumentPreviewJob


class DocumentExportPayload(BaseModel):
    export: DocumentExportJob

