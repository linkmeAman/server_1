"""Report catalog service."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.prism_guard import CallerContext
from app.modules.reports.schemas.models import ReportCatalogItem

from .definition import ReportDefinitionService
from .legacy_import import LegacyReportImportService
from .permission import ReportPermissionService


class ReportCatalogService:
    def __init__(
        self,
        definitions: ReportDefinitionService | None = None,
        permissions: ReportPermissionService | None = None,
        legacy_imports: LegacyReportImportService | None = None,
    ) -> None:
        self.definitions = definitions or ReportDefinitionService()
        self.permissions = permissions or ReportPermissionService()
        self.legacy_imports = legacy_imports or LegacyReportImportService()

    async def list_visible_reports(
        self,
        central_db: AsyncSession,
        main_db: AsyncSession,
        caller: CallerContext,
    ) -> list[ReportCatalogItem]:
        items: list[ReportCatalogItem] = []
        for definition in await self.definitions.list_definitions(central_db):
            if not await self.permissions.can_view(caller, central_db, definition):
                continue
            items.append(
                ReportCatalogItem(
                    slug=definition.slug,
                    name=definition.name,
                    description=definition.description,
                    category=definition.category,
                    kind=definition.kind,
                    status=definition.status,
                    route_path=definition.route_path or f"/reports/{definition.slug}",
                    prism_resource_code=definition.prism_resource_code,
                    legacy_report_id=definition.legacy_report_id,
                    source_label=definition.source_label,
                )
            )
        if caller.is_super:
            migrated_report_ids = {
                int(item.legacy_report_id)
                for item in items
                if item.legacy_report_id is not None
            }
            items.extend(
                await self.legacy_imports.list_legacy_catalog_items(
                    main_db,
                    exclude_report_ids=migrated_report_ids,
                )
            )
        return items
