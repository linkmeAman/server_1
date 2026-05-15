"""Generic report platform routes."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_central_db_session, get_main_db_session
from app.core.prism_guard import CallerContext, require_any_caller
from app.core.response import success_response
from app.modules.reports.schemas.models import (
    LegacyImportBatchRequest,
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
from db.connection import db_cursor
from routes.db_explorer_security import filter_database_list, normalize_database_name, validate_identifier

router = APIRouter(prefix="/api/reports", tags=["reports"])

definition_service = ReportDefinitionService()
permission_service = ReportPermissionService()
admin_service = ReportAdminService(definition_service)
catalog_service = ReportCatalogService(definition_service, permission_service)
query_service = ReportQueryService(permission_service)


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID") or str(uuid4())


@router.get("", include_in_schema=False)
@router.get("/catalog")
async def list_report_catalog(
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
        data={
            "report": definition.model_dump(mode="json"),
            "validation_issues": [
                item.model_dump(mode="json")
                for item in admin_service.collect_draft_validation_issues(definition)
            ],
        },
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


@router.get("/admin/legacy/reports")
async def list_admin_legacy_reports(
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    await permission_service.require_manage(caller, central_db)
    reports = await admin_service.list_legacy_reports(central_db, main_db)
    return success_response(
        data={"reports": [item.model_dump(mode="json") for item in reports]},
        message="Legacy reports fetched",
    ).model_dump(mode="json")


@router.post("/admin/legacy/import")
async def import_legacy_report_batch(
    payload: LegacyImportBatchRequest,
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    await permission_service.require_manage(caller, central_db)
    imported = await admin_service.import_legacy_reports(
        central_db,
        main_db,
        report_ids=[int(item) for item in payload.report_ids],
        user_id=int(caller.user_id),
    )
    return success_response(
        data=imported.model_dump(mode="json"),
        message="Legacy reports imported",
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


def _infer_column_type(data_type: str) -> str:
    normalized = (data_type or "").lower()
    if "bool" in normalized or normalized.startswith("bit"):
        return "boolean"
    if "timestamp" in normalized or "datetime" in normalized:
        return "datetime"
    if "date" in normalized:
        return "date"
    if any(token in normalized for token in ("decimal", "numeric", "money")):
        return "currency"
    if "bigint" in normalized or "int" in normalized:
        return "integer"
    if any(token in normalized for token in ("float", "double", "real")):
        return "number"
    return "text"


@router.get("/admin/discovery/databases")
async def list_report_discovery_databases(
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await permission_service.require_manage(caller, central_db)
    with db_cursor() as cursor:
        cursor.execute("SHOW DATABASES")
        rows = cursor.fetchall()

    databases: list[str] = []
    for row in rows:
        if not row:
            continue
        value = str(next(iter(row.values()), "") or "").strip()
        if value:
            databases.append(value)

    return success_response(
        data={"databases": sorted(filter_database_list(databases))},
        message="Report source databases fetched",
    ).model_dump(mode="json")


@router.get("/admin/discovery/tables")
async def list_report_discovery_tables(
    db: str = Query(..., min_length=1),
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await permission_service.require_manage(caller, central_db)
    selected_db = normalize_database_name(db)

    with db_cursor(database=selected_db) as cursor:
        cursor.execute("SHOW TABLES")
        rows = cursor.fetchall()

    tables: list[str] = []
    for row in rows:
        if not row:
            continue
        name = str(next(iter(row.values()), "") or "").strip()
        if name:
            tables.append(validate_identifier(name, "table name"))

    return success_response(
        data={"tables": sorted(set(tables))},
        message="Report source tables fetched",
    ).model_dump(mode="json")


@router.get("/admin/discovery/columns")
async def list_report_discovery_columns(
    db: str = Query(..., min_length=1),
    table: str = Query(..., min_length=1),
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await permission_service.require_manage(caller, central_db)
    selected_db = normalize_database_name(db)
    safe_table = validate_identifier(table, "table name")

    with db_cursor(database=selected_db) as cursor:
        cursor.execute(f"DESCRIBE `{safe_table}`")
        schema_rows = cursor.fetchall()

    columns: list[dict[str, object]] = []
    for row in schema_rows:
        raw_name = str(row.get("Field", "") or "").strip()
        if not raw_name:
            continue
        name = validate_identifier(raw_name, "column name")
        data_type = str(row.get("Type", "text") or "text")
        key_flag = str(row.get("Key", "") or "")
        null_flag = str(row.get("Null", "YES") or "YES")
        columns.append(
            {
                "name": name,
                "data_type": data_type,
                "report_type": _infer_column_type(data_type),
                "is_primary_key": key_flag.upper() == "PRI",
                "is_nullable": null_flag.upper() == "YES",
            }
        )

    columns.sort(key=lambda item: str(item["name"]))
    return success_response(
        data={"columns": columns},
        message="Report source columns fetched",
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
        data={
            "report": saved.model_dump(mode="json"),
            "validation_issues": [
                item.model_dump(mode="json")
                for item in admin_service.collect_draft_validation_issues(saved)
            ],
        },
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
