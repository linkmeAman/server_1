"""Report definition loading and persistence."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.reports.schemas.models import ReportAdminSaveRequest, ReportDefinition


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

    async def save_draft(
        self,
        central_db: AsyncSession,
        payload: ReportAdminSaveRequest,
        *,
        user_id: int,
    ) -> ReportDefinition:
        slug = self._normalize_slug(payload.slug)
        definition_data = dict(payload.definition)
        definition_data.update(
            {
                "slug": slug,
                "name": payload.name,
                "description": payload.description,
                "category": payload.category,
                "status": "draft",
            }
        )
        definition = ReportDefinition.model_validate(definition_data)
        definition_json = definition.model_dump_json()

        existing = await central_db.execute(
            text(
                """
                SELECT id
                FROM report_definitions
                WHERE slug = :slug
                LIMIT 1
                """
            ),
            {"slug": slug},
        )
        row = existing.fetchone()
        if row is None:
            result = await central_db.execute(
                text(
                    """
                    INSERT INTO report_definitions
                        (slug, name, description, category, kind, status,
                         prism_resource_code, source_legacy_report_id,
                         route_path, created_by_user_id, modified_by_user_id)
                    VALUES
                        (:slug, :name, :description, :category, :kind, 'draft',
                         :prism_resource_code, :source_legacy_report_id,
                         :route_path, :user_id, :user_id)
                    """
                ),
                {
                    "slug": definition.slug,
                    "name": definition.name,
                    "description": definition.description,
                    "category": definition.category,
                    "kind": definition.kind,
                    "prism_resource_code": definition.prism_resource_code,
                    "source_legacy_report_id": definition.legacy_report_id,
                    "route_path": definition.route_path,
                    "user_id": int(user_id),
                },
            )
            report_id = int(result.lastrowid)
            next_version = 1
        else:
            report_id = int(row._mapping["id"])
            latest = await central_db.execute(
                text(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                    FROM report_versions
                    WHERE report_id = :report_id
                    """
                ),
                {"report_id": report_id},
            )
            next_version = int(latest.fetchone()._mapping["next_version"])
            await central_db.execute(
                text(
                    """
                    UPDATE report_definitions
                    SET name = :name,
                        description = :description,
                        category = :category,
                        kind = :kind,
                        status = 'draft',
                        prism_resource_code = :prism_resource_code,
                        source_legacy_report_id = :source_legacy_report_id,
                        route_path = :route_path,
                        modified_by_user_id = :user_id
                    WHERE id = :report_id
                    """
                ),
                {
                    "report_id": report_id,
                    "name": definition.name,
                    "description": definition.description,
                    "category": definition.category,
                    "kind": definition.kind,
                    "prism_resource_code": definition.prism_resource_code,
                    "source_legacy_report_id": definition.legacy_report_id,
                    "route_path": definition.route_path,
                    "user_id": int(user_id),
                },
            )

        version_result = await central_db.execute(
            text(
                """
                INSERT INTO report_versions
                    (report_id, version, definition_json, status, created_by_user_id)
                VALUES
                    (:report_id, :version, :definition_json, 'draft', :user_id)
                """
            ),
            {
                "report_id": report_id,
                "version": next_version,
                "definition_json": definition_json,
                "user_id": int(user_id),
            },
        )
        version_id = int(version_result.lastrowid)
        await central_db.execute(
            text(
                """
                UPDATE report_definitions
                SET active_version_id = :version_id
                WHERE id = :report_id
                """
            ),
            {"version_id": version_id, "report_id": report_id},
        )
        await central_db.commit()
        return definition.model_copy(update={"version": next_version})

    async def publish(
        self,
        central_db: AsyncSession,
        slug: str,
        *,
        user_id: int,
    ) -> ReportDefinition | None:
        normalized = self._normalize_slug(slug)
        row_result = await central_db.execute(
            text(
                """
                SELECT d.id, d.active_version_id
                FROM report_definitions d
                WHERE d.slug = :slug
                LIMIT 1
                """
            ),
            {"slug": normalized},
        )
        row = row_result.fetchone()
        if row is None:
            return None

        report_id = int(row._mapping["id"])
        version_id = int(row._mapping["active_version_id"])
        await central_db.execute(
            text(
                """
                UPDATE report_definitions
                SET status = 'published',
                    modified_by_user_id = :user_id
                WHERE id = :report_id
                """
            ),
            {"user_id": int(user_id), "report_id": report_id},
        )
        await central_db.execute(
            text(
                """
                UPDATE report_versions
                SET status = 'published'
                WHERE id = :version_id
                """
            ),
            {"version_id": version_id},
        )
        await central_db.commit()
        return await self.get_definition(central_db, normalized, include_drafts=True)

    async def _load_db_definitions(
        self,
        central_db: AsyncSession,
        *,
        include_drafts: bool,
    ) -> list[ReportDefinition]:
        try:
            where = "" if include_drafts else "AND d.status = 'published'"
            result = await central_db.execute(
                text(
                    f"""
                    SELECT
                        d.slug,
                        d.status,
                        v.version,
                        v.definition_json
                    FROM report_definitions d
                    JOIN report_versions v ON v.id = d.active_version_id
                    WHERE d.status <> 'archived'
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
        ]

