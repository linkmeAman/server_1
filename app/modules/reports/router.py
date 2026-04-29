"""Generic report platform routes."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_central_db_session, get_main_db_session
from app.core.prism_guard import CallerContext, require_any_caller
from app.core.response import success_response
from app.modules.reports.schemas.models import (
    ReportAdminStatus,
    ReportDraftUpsertRequest,
    ReportQueryRequest,
)
from app.modules.reports.services import (
    ReportAdminService,
    ReportCatalogService,
    ReportDefinitionService,
    ReportPermissionService,
    ReportQueryService,
)

router = APIRouter(prefix="/api/reports", tags=["reports"])

definition_service = ReportDefinitionService()
permission_service = ReportPermissionService()
admin_service = ReportAdminService(definition_service)
catalog_service = ReportCatalogService(definition_service, permission_service)
query_service = ReportQueryService(permission_service)


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
    drafts = await admin_service.list_reports(central_db, status="draft")
    return success_response(
        data={"drafts": [item.model_dump(mode="json") for item in drafts]},
        message="Draft reports fetched",
    ).model_dump(mode="json")


@router.post("/admin/reports")
async def create_report_draft(
    payload: ReportDraftUpsertRequest,
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await permission_service.require_manage(caller, central_db)
    definition = await admin_service.create_report(
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
    imported = await admin_service.import_legacy_report(
        central_db,
        main_db,
        report_id=int(report_id),
        user_id=int(caller.user_id),
    )
    return success_response(
        data=imported.model_dump(mode="json"),
        message="Legacy report imported as draft",
    ).model_dump(mode="json")


@router.get("/admin/reports")
async def list_admin_reports(
    status: ReportAdminStatus = "all",
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await permission_service.require_manage(caller, central_db)
    reports = await admin_service.list_reports(central_db, status=status)
    return success_response(
        data={"reports": [item.model_dump(mode="json") for item in reports]},
        message="Admin reports fetched",
    ).model_dump(mode="json")


@router.get("/admin/reports/{slug}")
async def get_admin_report(
    slug: str,
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await permission_service.require_manage(caller, central_db)
    report = await admin_service.get_report(central_db, slug)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return success_response(
        data={"report": report.model_dump(mode="json")},
        message="Admin report fetched",
    ).model_dump(mode="json")


@router.put("/admin/reports/{slug}")
async def update_report_draft(
    slug: str,
    payload: ReportDraftUpsertRequest,
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await permission_service.require_manage(caller, central_db)
    saved = await admin_service.update_report(
        central_db,
        slug,
        payload,
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
    definition = await admin_service.publish_report(
        central_db,
        slug,
        user_id=int(caller.user_id),
    )
    return success_response(
        data={"report": definition.model_dump(mode="json")},
        message="Report published",
    ).model_dump(mode="json")


@router.post("/admin/reports/{slug}/archive")
async def archive_report(
    slug: str,
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await permission_service.require_manage(caller, central_db)
    definition = await admin_service.archive_report(
        central_db,
        slug,
        user_id=int(caller.user_id),
    )
    return success_response(
        data={"report": definition.model_dump(mode="json")},
        message="Report archived",
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
