"""HR module — TDS certificate API router."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_main_db_session
from app.core.prism_guard import CallerContext, require_any_caller
from app.core.response import success_response

from .tds_service import TDSService

router = APIRouter(prefix="/api/hr/tds", tags=["hr-tds"])
_service = TDSService()

DEFAULT_LIMIT = 50


# ---------------------------------------------------------------------------
# Employee list (View tab)
# ---------------------------------------------------------------------------


@router.get("/employees")
async def list_employees(
    q: str | None = Query(default=None),
    status: int | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_main_db_session),
    caller: CallerContext = Depends(require_any_caller),
):
    data = await _service.list_employees(
        db, q=q, status=status, limit=limit, offset=offset
    )
    return success_response(data)


@router.get("/employees/{employee_id}/years")
async def get_tds_years(
    employee_id: int,
    db: AsyncSession = Depends(get_main_db_session),
    caller: CallerContext = Depends(require_any_caller),
):
    years = await _service.get_employee_tds_years(db, employee_id)
    return success_response({"employee_id": employee_id, "fiscal_years": years})


@router.get("/employees/{employee_id}/documents")
async def get_tds_documents(
    employee_id: int,
    fiscal_year: str = Query(...),
    db: AsyncSession = Depends(get_main_db_session),
    caller: CallerContext = Depends(require_any_caller),
):
    docs = await _service.get_employee_tds_docs(db, employee_id, fiscal_year)
    return success_response({"employee_id": employee_id, "fiscal_year": fiscal_year, "documents": docs})


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@router.post("/upload")
async def upload_tds(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_main_db_session),
    caller: CallerContext = Depends(require_any_caller),
):
    result = await _service.upload_tds_files(db, file=file, uploaded_by=caller.user_id)
    return success_response(result)


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


@router.get("/batches/{batch_id}/mapping")
async def get_mapping_suggestions(
    batch_id: int,
    db: AsyncSession = Depends(get_main_db_session),
    caller: CallerContext = Depends(require_any_caller),
):
    # We need the full employee list for matching — load all (no pagination)
    employees_data = await _service.list_employees(
        db, q=None, status=1, limit=2000, offset=0
    )
    suggestions = await _service.get_mapping_suggestions(
        db, batch_id, employees_data["employees"]
    )
    return success_response(suggestions)


class MappingItem:
    doc_id: int
    employee_id: int | None
    mapping_status: str


from pydantic import BaseModel  # noqa: E402


class MappingItemPayload(BaseModel):
    doc_id: int
    employee_id: int | None = None
    mapping_status: str = "manual_mapped"


class SaveMappingPayload(BaseModel):
    mappings: list[MappingItemPayload]


@router.post("/batches/{batch_id}/mapping")
async def save_mapping(
    batch_id: int,
    payload: SaveMappingPayload,
    db: AsyncSession = Depends(get_main_db_session),
    caller: CallerContext = Depends(require_any_caller),
):
    await _service.save_mapping(
        db,
        batch_id,
        [m.model_dump() for m in payload.mappings],
    )
    return success_response({"batch_id": batch_id, "saved": len(payload.mappings)})


# ---------------------------------------------------------------------------
# Signed URL (view PDF in browser)
# ---------------------------------------------------------------------------


@router.get("/signed-url")
async def get_signed_url(
    key: str = Query(..., description="S3 object key"),
    expiry: int = Query(default=3600, ge=60, le=86400),
    caller: CallerContext = Depends(require_any_caller),
):
    url = _service.get_signed_url(key, expiry)
    return success_response({"signed_url": url, "expires_in": expiry})
