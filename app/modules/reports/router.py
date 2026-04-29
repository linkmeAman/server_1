"""Generic report platform routes."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_central_db_session, get_main_db_session
from app.core.prism_guard import CallerContext, require_any_caller
from app.core.response import success_response
from app.modules.reports.schemas.models import ReportAdminSaveRequest, ReportQueryRequest
from app.modules.reports.services import (
    LegacyReportImportService,
    ReportCatalogService,
    ReportDefinitionService,
    ReportPermissionService,
    ReportQueryService,
)

router = APIRouter(prefix="/api/reports", tags=["reports"])

definition_service = ReportDefinitionService()
permission_service = ReportPermissionService()
catalog_service = ReportCatalogService(definition_service, permission_service)
query_service = ReportQueryService(permission_service)
legacy_import_service = LegacyReportImportService()


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID") or str(uuid4())


@router.get("")
async def list_reports(
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    reports = await catalog_service.list_visible_reports(central_db, main_db, caller)
    await central_db.commit()
    return success_response(
        data={"reports": [item.model_dump(mode="json") for item in reports]},
        message="Reports fetched",
    ).model_dump(mode="json")


@router.get("/admin/drafts")
async def list_report_drafts(
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await permission_service.require_manage(caller, central_db)
    all_definitions = await definition_service.list_definitions(central_db, include_drafts=True)
    drafts = [item for item in all_definitions if item.status == "draft"]
    return success_response(
        data={"drafts": [item.model_dump(mode="json") for item in drafts]},
        message="Draft reports fetched",
    ).model_dump(mode="json")


@router.post("/admin/reports")
async def create_report_draft(
    payload: ReportAdminSaveRequest,
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await permission_service.require_manage(caller, central_db)
    definition = await definition_service.save_draft(
        central_db,
        payload,
        user_id=int(caller.user_id),
    )
    return success_response(
        data={"report": definition.model_dump(mode="json")},
        message="Report draft saved",
    ).model_dump(mode="json")


@router.post("/admin/legacy/{report_id}/import")
async def import_legacy_report_draft(
    report_id: int,
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    await permission_service.require_manage(caller, central_db)
    imported = await legacy_import_service.build_draft_from_legacy(
        main_db,
        report_id=report_id,
    )
    definition_payload = imported["definition"]
    saved = await definition_service.save_draft(
        central_db,
        ReportAdminSaveRequest(
            slug=str(definition_payload.get("slug") or f"legacy-{report_id}"),
            name=str(definition_payload.get("name") or f"Legacy Report {report_id}"),
            description=definition_payload.get("description"),
            category=str(definition_payload.get("category") or "Legacy Imports"),
            definition=definition_payload,
        ),
        user_id=int(caller.user_id),
    )
    return success_response(
        data={
            "report": saved.model_dump(mode="json"),
            "warnings": imported.get("warnings") or [],
            "imported_legacy_report_id": int(report_id),
        },
        message="Legacy report imported as draft",
    ).model_dump(mode="json")


@router.put("/admin/reports/{slug}")
async def update_report_draft(
    slug: str,
    payload: ReportAdminSaveRequest,
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await permission_service.require_manage(caller, central_db)
    saved = await definition_service.save_draft(
        central_db,
        payload.model_copy(update={"slug": slug}),
        user_id=int(caller.user_id),
    )
    return success_response(
        data={"report": saved.model_dump(mode="json")},
        message="Report draft updated",
    ).model_dump(mode="json")


@router.post("/admin/reports/{slug}/publish")
async def publish_report(
    slug: str,
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await permission_service.require_manage(caller, central_db)
    definition = await definition_service.publish(
        central_db,
        slug,
        user_id=int(caller.user_id),
    )
    if definition is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return success_response(
        data={"report": definition.model_dump(mode="json")},
        message="Report published",
    ).model_dump(mode="json")


@router.post("/{slug}/query")
async def query_report(
    slug: str,
    payload: ReportQueryRequest,
    request: Request,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    definition = await definition_service.get_definition(central_db, slug)
    if definition is None:
        raise HTTPException(status_code=404, detail="Report not found")
    await permission_service.require_view(caller, central_db, definition)
    response = await query_service.run_query(
        main_db,
        central_db,
        caller,
        definition,
        payload,
        request_id=_request_id(request),
    )
    return success_response(
        data=response.model_dump(mode="json"),
        message="Report queried",
    ).model_dump(mode="json")


@router.get("/{slug}")
async def get_report(
    slug: str,
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    definition = await definition_service.get_definition(central_db, slug)
    if definition is None:
        raise HTTPException(status_code=404, detail="Report not found")
    await permission_service.require_view(caller, central_db, definition)
    await central_db.commit()
    return success_response(
        data={"report": definition.model_dump(mode="json")},
        message="Report fetched",
    ).model_dump(mode="json")
