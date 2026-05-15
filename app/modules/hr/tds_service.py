"""TDS certificate service — business logic layer."""

from __future__ import annotations

import io
import logging
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.shared.s3_service import (
    extract_pdfs_from_zip,
    generate_presigned_get_url,
    upload_fileobj,
    upload_pdfs_to_s3,
)

from .tds_repository import TDSRepository, parse_tds_filename

logger = logging.getLogger(__name__)

_ALLOWED_MIME_TYPES = {"application/pdf", "application/zip", "application/x-zip-compressed"}
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB


class TDSService:
    def __init__(self) -> None:
        self.repo = TDSRepository()

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    async def upload_tds_files(
        self,
        db: AsyncSession,
        *,
        file: UploadFile,
        uploaded_by: int | None,
    ) -> dict[str, Any]:
        """Handle single PDF or ZIP upload — idempotent.

        Files whose ``original_filename`` already exists in ``tds_document``
        are skipped entirely (no S3 upload, no DB row).  This makes re-uploading
        the same ZIP safe: only genuinely new PDFs are processed.

        Steps:
        1. Read and validate the file.
        2. Extract the PDF file-list in-memory (no S3 yet).
        3. Query ``tds_document`` for already-existing filenames → build skip list.
        4. If **all** files are duplicates, return early without creating a batch.
        5. Create a batch record, upload only the new PDFs to S3, insert DB rows.
        6. Return summary including ``skipped_files``.
        """
        settings = get_settings()
        content_type = (file.content_type or "").lower()
        filename = file.filename or "upload"

        raw = await file.read()
        if len(raw) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum allowed size is {_MAX_UPLOAD_BYTES // (1024*1024)} MB.",
            )

        is_zip = filename.lower().endswith(".zip") or content_type in {
            "application/zip",
            "application/x-zip-compressed",
        }
        is_pdf = filename.lower().endswith(".pdf") or content_type == "application/pdf"

        if not is_zip and not is_pdf:
            raise HTTPException(
                status_code=422,
                detail="Only PDF or ZIP files are accepted.",
            )

        upload_type = "zip" if is_zip else "single"

        # ----------------------------------------------------------------
        # Step 1 — Extract file list in-memory (no S3 yet)
        # ----------------------------------------------------------------
        if is_zip:
            try:
                pdfs = extract_pdfs_from_zip(raw)
            except Exception as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if not pdfs:
                raise HTTPException(
                    status_code=422, detail="ZIP archive contains no PDF files."
                )
            candidate_names = [p.filename for p in pdfs]
        else:
            pdfs = None
            candidate_names = [filename]

        # ----------------------------------------------------------------
        # Step 2 — Idempotency: skip PDFs already present in tds_document
        # ----------------------------------------------------------------
        existing_names = await self.repo.find_existing_filenames(db, candidate_names)
        skipped = sorted(existing_names)

        new_pdfs = (
            [p for p in pdfs if p.filename not in existing_names] if is_zip else None
        )
        all_skipped = (is_zip and not new_pdfs) or (is_pdf and filename in existing_names)

        if all_skipped:
            return {
                "batch_id": None,
                "upload_type": upload_type,
                "total_files": 0,
                "skipped_files": len(skipped),
                "skipped": skipped,
                "message": "All files already exist in the system. Nothing was uploaded.",
            }

        # ----------------------------------------------------------------
        # Step 3 — Create batch record (only for genuinely new files)
        # ----------------------------------------------------------------
        batch_id = await self.repo.create_batch(
            db,
            upload_type=upload_type,
            original_filename=filename,
            uploaded_by=uploaded_by,
        )
        folder = f"{settings.S3_TDS_FOLDER}/batch_{batch_id}"

        # ----------------------------------------------------------------
        # Step 4 — Upload only new files to S3
        # ----------------------------------------------------------------
        try:
            if is_zip:
                uploaded_records = upload_pdfs_to_s3(new_pdfs, folder)  # type: ignore[arg-type]
            else:
                s3_key = f"{folder}/{filename}"
                upload_fileobj(io.BytesIO(raw), s3_key, "application/pdf")
                uploaded_records = [{"original_filename": filename, "s3_key": s3_key}]
        except Exception as exc:
            logger.exception("S3 upload failed for batch %s", batch_id)
            raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc

        # ----------------------------------------------------------------
        # Step 5 — Persist document rows and update batch status
        # ----------------------------------------------------------------
        await self.repo.bulk_insert_documents(db, batch_id, uploaded_records)
        await self.repo.update_batch_file_count(
            db, batch_id, len(uploaded_records), status="uploaded"
        )

        return {
            "batch_id": batch_id,
            "upload_type": upload_type,
            "total_files": len(uploaded_records),
            "skipped_files": len(skipped),
            "skipped": skipped,
            "files": [
                {
                    "original_filename": r["original_filename"],
                    "s3_key": r["s3_key"],
                }
                for r in uploaded_records
            ],
        }

    # ------------------------------------------------------------------
    # Mapping suggestions
    # ------------------------------------------------------------------

    async def get_mapping_suggestions(
        self,
        db: AsyncSession,
        batch_id: int,
        employees: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Auto-map uploaded documents to employees by fuzzy name match.

        Returns mapping items split into ``auto_mapped`` (green) and
        ``failed`` (red) groups.
        """
        docs = await self.repo.list_documents_for_batch(db, batch_id)
        if not docs:
            raise HTTPException(
                status_code=404, detail=f"No documents found for batch {batch_id}."
            )

        # Build employee lookup: normalised_name → employee
        emp_index: dict[str, dict[str, Any]] = {}
        for emp in employees:
            full = (
                f"{emp.get('fname', '')} {emp.get('mname', '') or ''} {emp.get('lname', '') or ''}"
            )
            norm = _normalise_name(full)
            if norm:
                emp_index[norm] = emp

        auto_mapped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for doc in docs:
            parsed_name = doc.get("parsed_name") or ""
            norm_parsed = _normalise_name(parsed_name)
            matched_emp = _find_best_match(norm_parsed, emp_index) if norm_parsed else None

            item: dict[str, Any] = {
                "doc_id": doc["id"],
                "original_filename": doc["original_filename"],
                "s3_key": doc["s3_key"],
                "parsed_name": parsed_name,
                "quarter": doc.get("quarter"),
                "fiscal_year": doc.get("fiscal_year"),
                "employee_id": matched_emp["employee_id"] if matched_emp else None,
                "employee_name": matched_emp.get("full_name") if matched_emp else None,
            }

            if matched_emp:
                item["mapping_status"] = "auto_mapped"
                auto_mapped.append(item)
            else:
                item["mapping_status"] = "failed"
                failed.append(item)

        return {
            "batch_id": batch_id,
            "auto_mapped": auto_mapped,
            "failed": failed,
            "total": len(docs),
        }

    async def save_mapping(
        self,
        db: AsyncSession,
        batch_id: int,
        mappings: list[dict[str, Any]],
    ) -> None:
        """Persist confirmed mappings and mark batch as fully mapped."""
        await self.repo.apply_mapping(db, mappings)
        await self.repo.mark_batch_mapped(db, batch_id)

    # ------------------------------------------------------------------
    # View / signed URL
    # ------------------------------------------------------------------

    def get_signed_url(self, s3_key: str, expiry: int | None = None) -> str:
        return generate_presigned_get_url(s3_key, expiry)

    # ------------------------------------------------------------------
    # Employee TDS view
    # ------------------------------------------------------------------

    async def list_employees(
        self,
        db: AsyncSession,
        *,
        q: str | None,
        status: int | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        rows = await self.repo.list_employees_with_tds(
            db, q=q, status=status, limit=limit, offset=offset
        )
        total = await self.repo.count_employees(db, q=q, status=status)
        return {
            "employees": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def get_employee_tds_years(
        self, db: AsyncSession, employee_id: int
    ) -> list[str]:
        return await self.repo.get_tds_years_for_employee(db, employee_id)

    async def get_employee_tds_docs(
        self, db: AsyncSession, employee_id: int, fiscal_year: str
    ) -> list[dict[str, Any]]:
        docs = await self.repo.get_tds_docs_for_employee_year(
            db, employee_id, fiscal_year
        )
        # Attach short-lived signed URLs
        for doc in docs:
            try:
                doc["signed_url"] = generate_presigned_get_url(doc["s3_key"])
            except Exception:
                doc["signed_url"] = None
        return docs


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalise_name(name: str) -> str:
    """Upper-case, collapse whitespace, strip punctuation for loose comparison."""
    import re

    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", "", name.upper())).strip()


def _find_best_match(
    norm_parsed: str,
    emp_index: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Exact match first; then try token-subset match."""
    if norm_parsed in emp_index:
        return emp_index[norm_parsed]

    parsed_tokens = set(norm_parsed.split())
    best: dict[str, Any] | None = None
    best_score = 0

    for norm_emp, emp in emp_index.items():
        emp_tokens = set(norm_emp.split())
        # Jaccard-ish score
        inter = len(parsed_tokens & emp_tokens)
        union = len(parsed_tokens | emp_tokens)
        if union == 0:
            continue
        score = inter / union
        if score > best_score and score >= 0.6:
            best_score = score
            best = emp

    return best
