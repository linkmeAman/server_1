"""Report definition loading and persistence."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.reports.schemas.models import ReportDefinition


class ReportDefinitionService:
    """Loads certified code reports and DB-backed custom report definitions."""

    def __init__(self) -> None:
        self._certified = self._build_certified_definitions()

    async def list_definitions(
        self,
        central_db: AsyncSession,
        *,
        include_drafts: bool = False,
    ) -> list[ReportDefinition]:
        definitions = {item.slug: item for item in self._certified}
        for item in await self._load_db_definitions(central_db, include_drafts=include_drafts):
            definitions[item.slug] = item
        rows = list(definitions.values())
        if not include_drafts:
            rows = [item for item in rows if item.status == "published"]
        return sorted(rows, key=lambda item: (item.category.lower(), item.name.lower()))

    async def get_definition(
        self,
        central_db: AsyncSession,
        slug: str,
        *,
        include_drafts: bool = False,
    ) -> ReportDefinition | None:
        normalized = self._normalize_slug(slug)
        for item in await self.list_definitions(central_db, include_drafts=include_drafts):
            if item.slug == normalized:
                return item
        return None

    async def _load_db_definitions(
        self,
        central_db: AsyncSession,
        *,
        include_drafts: bool,
    ) -> list[ReportDefinition]:
        try:
            if include_drafts:
                version_join = "JOIN report_versions v ON v.id = d.active_version_id"
                where = "WHERE d.status <> 'archived'"
            else:
                version_join = """
                    JOIN (
                        SELECT report_id, MAX(version) AS version
                        FROM report_versions
                        WHERE status = 'published'
                        GROUP BY report_id
                    ) latest ON latest.report_id = d.id
                    JOIN report_versions v
                      ON v.report_id = latest.report_id
                     AND v.version = latest.version
                     AND v.status = 'published'
                """
                where = "WHERE d.status = 'published'"
            result = await central_db.execute(
                text(
                    f"""
                    SELECT
                        d.slug,
                        v.status,
                        v.version,
                        v.definition_json
                    FROM report_definitions d
                    {version_join}
                    {where}
                    """
                )
            )
        except Exception:
            return []

        definitions: list[ReportDefinition] = []
        for row in result.fetchall():
            row_map = row._mapping
            try:
                payload: dict[str, Any] = json.loads(row_map["definition_json"])
                payload["slug"] = str(row_map["slug"])
                payload["status"] = str(row_map["status"])
                payload["version"] = int(row_map["version"])
                definitions.append(ReportDefinition.model_validate(payload))
            except Exception:
                continue
        return definitions

    def has_certified_slug(self, slug: str) -> bool:
        normalized = self._normalize_slug(slug)
        return any(item.slug == normalized for item in self._certified)

    @staticmethod
    def _normalize_slug(value: str) -> str:
        return value.strip().lower().replace("_", "-")

    @staticmethod
    def _build_certified_definitions() -> list[ReportDefinition]:
        return [
            ReportDefinition.model_validate(
                {
                    "slug": "inquiry-411",
                    "name": "Inquiry Report",
                    "description": "Generic report pilot migrated from legacy report 411.",
                    "category": "Reports",
                    "status": "published",
                    "kind": "table",
                    "legacy_report_id": 411,
                    "prism_resource_code": "reports.inquiry_411",
                    "legacy_view_action": "report:read",
                    "source": {
                        "type": "table",
                        "table": "inquiry_structured_report_view",
                        "id_column": "contact_id",
                        "date_column": "doi_created",
                    },
                    "columns": [
                        {"key": "contact_id", "label": "Contact ID", "type": "number", "visible": True, "sortable": True},
                        {"key": "fullname", "label": "Name", "searchable": True, "exportable": True},
                        {"key": "mobile", "label": "Mobile", "searchable": True, "exportable": True},
                        {"key": "email", "label": "Email", "searchable": True, "exportable": True},
                        {"key": "primary_source", "label": "Primary Source", "searchable": True, "exportable": True},
                        {"key": "gclid", "label": "GCLID", "searchable": True, "exportable": True},
                        {"key": "doi_created", "label": "Created", "type": "datetime", "sortable": True, "exportable": True},
                    ],
                    "filters": [
                        {
                            "key": "primary_source",
                            "label": "Primary Source",
                            "column": "primary_source",
                            "operators": ["eq", "contains", "in"],
                            "type": "text",
                        }
                    ],
                    "default_sort": [{"column": "doi_created", "direction": "desc"}],
                    "search_columns": ["fullname", "mobile", "email", "primary_source", "gclid"],
                    "date_range": {"enabled": True, "default_days": 30, "column": "doi_created"},
                    "branch_scope": {"mode": "all"},
                    "source_label": "legacy-pilot",
                    "route_path": "/reports/inquiry-411",
                }
            ),
            ReportDefinition.model_validate(
                {
                    "slug": "top-summary",
                    "name": "Top Summary",
                    "description": "High-level summary of key lead metrics.",
                    "category": "Performance Marketing",
                    "status": "published",
                    "kind": "route",
                    "prism_resource_code": "reports.top_summary",
                    "legacy_view_action": "top-summary:read",
                    "source": {"type": "route", "route_path": "/reports/top-summary"},
                    "route_path": "/reports/top-summary",
                    "source_label": "certified-route",
                }
            ),
            ReportDefinition.model_validate(
                {
                    "slug": "source-breakdown",
                    "name": "Source Breakdown",
                    "description": "Lead source performance across the funnel.",
                    "category": "Performance Marketing",
                    "status": "published",
                    "kind": "route",
                    "prism_resource_code": "reports.source_breakdown",
                    "legacy_view_action": "report:read",
                    "source": {"type": "route", "route_path": "/reports/source-breakdown"},
                    "route_path": "/reports/source-breakdown",
                    "source_label": "certified-route",
                }
            ),
            ReportDefinition.model_validate(
                {
                    "slug": "center-performance",
                    "name": "Center Performance",
                    "description": "Compare city or center performance across the funnel.",
                    "category": "Performance Marketing",
                    "status": "published",
                    "kind": "route",
                    "prism_resource_code": "reports.center_performance",
                    "legacy_view_action": "report:read",
                    "source": {"type": "route", "route_path": "/reports/center-performance"},
                    "route_path": "/reports/center-performance",
                    "source_label": "certified-route",
                }
            ),
            ReportDefinition.model_validate(
                {
                    "slug": "funnel-tracking",
                    "name": "Funnel Stage Tracking",
                    "description": "Track lead movement across funnel stages and identify drop-offs.",
                    "category": "Performance Marketing",
                    "status": "published",
                    "kind": "route",
                    "prism_resource_code": "reports.funnel_stage_tracking",
                    "legacy_view_action": "report:read",
                    "source": {"type": "route", "route_path": "/reports/funnel-tracking"},
                    "route_path": "/reports/funnel-tracking",
                    "source_label": "certified-route",
                }
            ),
            ReportDefinition.model_validate(
                {
                    "slug": "campaign-performance",
                    "name": "Campaign Performance",
                    "description": "Campaign-level performance across Meta and Google.",
                    "category": "Performance Marketing",
                    "status": "published",
                    "kind": "route",
                    "prism_resource_code": "reports.campaign_performance",
                    "legacy_view_action": "report:read",
                    "source": {"type": "route", "route_path": "/reports/campaign-performance"},
                    "route_path": "/reports/campaign-performance",
                    "source_label": "certified-route",
                }
            ),
            ReportDefinition.model_validate(
                {
                    "slug": "heard-from-performance",
                    "name": "Heard-from Performance",
                    "description": "Performance by primary heard-from channel and creative.",
                    "category": "Performance Marketing",
                    "status": "published",
                    "kind": "route",
                    "prism_resource_code": "reports.heard_from_performance",
                    "legacy_view_action": "report:read",
                    "source": {"type": "route", "route_path": "/reports/heard-from-performance"},
                    "route_path": "/reports/heard-from-performance",
                    "source_label": "certified-route",
                }
            ),
            ReportDefinition.model_validate(
                {
                    "slug": "event-calendar",
                    "name": "Event Calendar",
                    "description": "Review and approve scheduled business events for your teams.",
                    "category": "Operations",
                    "status": "published",
                    "kind": "route",
                    "prism_resource_code": "reports.event_calendar",
                    "legacy_view_action": "event-calendar:read",
                    "source": {"type": "route", "route_path": "/reports/event-calendar"},
                    "route_path": "/reports/event-calendar",
                    "source_label": "certified-route",
                }
            ),
        ]
