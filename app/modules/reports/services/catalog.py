"""Report catalog service."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.prism_guard import CallerContext
from app.modules.reports.schemas.models import ReportCatalogItem

from .definition import ReportDefinitionService
from .permission import ReportPermissionService


class ReportCatalogService:
    def __init__(
        self,
        definitions: ReportDefinitionService | None = None,
        permissions: ReportPermissionService | None = None,
    ) -> None:
        self.definitions = definitions or ReportDefinitionService()
        self.permissions = permissions or ReportPermissionService()

    async def list_visible_reports(
        self,
        central_db: AsyncSession,
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
        return items

