"""Admin lifecycle orchestration for report drafts."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.reports.schemas.models import (
    ReportDefinition,
    ReportDraftUpsertRequest,
    ReportImportDraftResponse,
)

from .definition import ReportDefinitionService
from .errors import ReportApiException
from .legacy_import import LegacyReportImportService
from .validator import ReportDefinitionValidator


class ReportAdminService:
    """Manage create, update, publish, archive, and import flows for DB-backed reports."""

    def __init__(
        self,
        definitions: ReportDefinitionService | None = None,
        validator: ReportDefinitionValidator | None = None,
        legacy_imports: LegacyReportImportService | None = None,
    ) -> None:
        self.definitions = definitions or ReportDefinitionService()
        self.validator = validator or ReportDefinitionValidator()
        self.legacy_imports = legacy_imports or LegacyReportImportService()

    async def list_reports(
        self,
        central_db: AsyncSession,
        *,
        status: str = "all",
    ) -> list[ReportDefinition]:
        return await self._load_db_definitions(central_db, status=status)

    async def get_report(
        self,
        central_db: AsyncSession,
        slug: str,
    ) -> ReportDefinition | None:
        rows = await self._load_db_definitions(central_db, slug=self._normalize_slug(slug), status="all")
        return rows[0] if rows else None

    async def create_report(
        self,
        central_db: AsyncSession,
        payload: ReportDraftUpsertRequest,
        *,
        user_id: int,
    ) -> ReportDefinition:
        slug = self._normalize_slug(payload.slug)
        if self.definitions.has_certified_slug(slug):
            raise ReportApiException(
                409,
                error_code="ReportSlugConflict",
                message="This slug is reserved by a certified report.",
            )
        existing = await self._fetch_report_row(central_db, slug)
        if existing is not None:
            raise ReportApiException(
                409,
                error_code="ReportSlugConflict",
                message="A report with this slug already exists.",
            )
        return await self._save_report(central_db, payload, slug=slug, user_id=user_id)

    async def update_report(
        self,
        central_db: AsyncSession,
        slug: str,
        payload: ReportDraftUpsertRequest,
        *,
        user_id: int,
    ) -> ReportDefinition:
        normalized = self._normalize_slug(slug)
        existing = await self._fetch_report_row(central_db, normalized)
        if existing is None:
            raise ReportApiException(
                404,
                error_code="ReportNotFound",
                message="Report not found",
            )
        if str(existing["status"]) == "archived":
            raise ReportApiException(
                409,
                error_code="ReportArchived",
                message="Archived reports cannot be updated.",
            )
        return await self._save_report(
            central_db,
            payload,
            slug=normalized,
            user_id=user_id,
            report_id=int(existing["id"]),
        )

    async def publish_report(
        self,
        central_db: AsyncSession,
        slug: str,
        *,
        user_id: int,
    ) -> ReportDefinition:
        normalized = self._normalize_slug(slug)
        existing = await self._fetch_report_row(central_db, normalized)
        if existing is None:
            raise ReportApiException(
                404,
                error_code="ReportNotFound",
                message="Report not found",
            )
        if str(existing["status"]) == "archived":
            raise ReportApiException(
                409,
                error_code="ReportArchived",
                message="Archived reports cannot be published.",
            )

        definition = await self.get_report(central_db, normalized)
        if definition is None:
            raise ReportApiException(
                404,
                error_code="ReportNotFound",
                message="Report not found",
            )
        self.validator.validate_publish(definition)

        await central_db.execute(
            text(
                """
                UPDATE report_definitions
                SET status = 'published',
                    modified_by_user_id = :user_id
                WHERE id = :report_id
                """
            ),
            {"user_id": int(user_id), "report_id": int(existing["id"])},
        )
        await central_db.execute(
            text(
                """
                UPDATE report_versions
                SET status = 'published'
                WHERE id = :version_id
                """
            ),
            {"version_id": int(existing["active_version_id"])},
        )
        await central_db.commit()
        published = await self.get_report(central_db, normalized)
        if published is None:
            raise ReportApiException(
                500,
                error_code="ReportStateError",
                message="Report was published but could not be reloaded.",
            )
        return published

    async def archive_report(
        self,
        central_db: AsyncSession,
        slug: str,
        *,
        user_id: int,
    ) -> ReportDefinition:
        normalized = self._normalize_slug(slug)
        existing = await self._fetch_report_row(central_db, normalized)
        if existing is None:
            raise ReportApiException(
                404,
                error_code="ReportNotFound",
                message="Report not found",
            )

        await central_db.execute(
            text(
                """
                UPDATE report_definitions
                SET status = 'archived',
                    modified_by_user_id = :user_id
                WHERE id = :report_id
                """
            ),
            {"user_id": int(user_id), "report_id": int(existing["id"])},
        )
        if existing["active_version_id"] is not None:
            await central_db.execute(
                text(
                    """
                    UPDATE report_versions
                    SET status = 'archived'
                    WHERE id = :version_id
                    """
                ),
                {"version_id": int(existing["active_version_id"])},
            )
        await central_db.commit()

        archived = await self.get_report(central_db, normalized)
        if archived is None:
            raise ReportApiException(
                500,
                error_code="ReportStateError",
                message="Report was archived but could not be reloaded.",
            )
        return archived

    async def import_legacy_report(
        self,
        central_db: AsyncSession,
        main_db: AsyncSession,
        *,
        report_id: int,
        user_id: int,
    ) -> ReportImportDraftResponse:
        imported = await self.legacy_imports.build_draft_from_legacy(main_db, report_id=report_id)
        payload = ReportDraftUpsertRequest.model_validate(imported["definition"])
        slug = self._normalize_slug(payload.slug)
        existing = await self._fetch_report_row(central_db, slug)
        if existing is None:
            report = await self._save_report(central_db, payload, slug=slug, user_id=user_id)
        else:
            report = await self._save_report(
                central_db,
                payload,
                slug=slug,
                user_id=user_id,
                report_id=int(existing["id"]),
            )
        return ReportImportDraftResponse(
            report=report,
            warnings=[str(item) for item in imported.get("warnings") or []],
            imported_legacy_report_id=int(report_id),
        )

    async def _save_report(
        self,
        central_db: AsyncSession,
        payload: ReportDraftUpsertRequest,
        *,
        slug: str,
        user_id: int,
        report_id: int | None = None,
    ) -> ReportDefinition:
        definition = self._build_definition(payload, slug=slug)
        self.validator.validate_draft(definition)
        definition_json = definition.model_dump_json()

        if report_id is None:
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
            latest = await central_db.execute(
                text(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                    FROM report_versions
                    WHERE report_id = :report_id
                    """
                ),
                {"report_id": int(report_id)},
            )
            next_version = int(latest.fetchone()._mapping["next_version"])
            await central_db.execute(
                text(
                    """
                    UPDATE report_definitions
                    SET slug = :slug,
                        name = :name,
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
                    "report_id": int(report_id),
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
                "report_id": int(report_id),
                "version": int(next_version),
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
            {"version_id": version_id, "report_id": int(report_id)},
        )
        await central_db.commit()
        return definition.model_copy(update={"version": next_version})

    async def _load_db_definitions(
        self,
        central_db: AsyncSession,
        *,
        status: str = "all",
        slug: str | None = None,
    ) -> list[ReportDefinition]:
        clauses = []
        params: dict[str, Any] = {}
        if slug:
            clauses.append("d.slug = :slug")
            params["slug"] = slug
        if status != "all":
            clauses.append("d.status = :status")
            params["status"] = status
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        result = await central_db.execute(
            text(
                f"""
                SELECT
                    d.id,
                    d.slug,
                    d.status,
                    d.active_version_id,
                    v.version,
                    v.definition_json
                FROM report_definitions d
                JOIN report_versions v ON v.id = d.active_version_id
                {where_sql}
                ORDER BY d.category ASC, d.name ASC
                """
            ),
            params,
        )

        definitions: list[ReportDefinition] = []
        for row in result.fetchall():
            row_map = row._mapping
            try:
                payload = json.loads(row_map["definition_json"])
                payload["slug"] = str(row_map["slug"])
                payload["status"] = str(row_map["status"])
                payload["version"] = int(row_map["version"])
                definitions.append(ReportDefinition.model_validate(payload))
            except Exception:
                continue
        return definitions

    async def _fetch_report_row(
        self,
        central_db: AsyncSession,
        slug: str,
    ) -> dict[str, Any] | None:
        result = await central_db.execute(
            text(
                """
                SELECT id, slug, status, active_version_id
                FROM report_definitions
                WHERE slug = :slug
                LIMIT 1
                """
            ),
            {"slug": slug},
        )
        row = result.fetchone()
        return dict(row._mapping) if row else None

    def _build_definition(
        self,
        payload: ReportDraftUpsertRequest,
        *,
        slug: str,
    ) -> ReportDefinition:
        route_path = (payload.route_path or "").strip() or None
        source_payload = payload.source.model_dump(mode="python")
        if payload.kind == "table":
            route_path = route_path or f"/reports/{slug}"
            source_payload["type"] = "table"
            source_payload["route_path"] = None
        else:
            route_path = route_path or payload.source.route_path
            source_payload["type"] = "route"
            source_payload["route_path"] = route_path
            source_payload["table"] = None

        data = payload.model_dump(mode="python")
        data.update(
            {
                "slug": slug,
                "status": "draft",
                "route_path": route_path,
                "source": source_payload,
            }
        )
        return ReportDefinition.model_validate(data)

    @staticmethod
    def _normalize_slug(value: str) -> str:
        return value.strip().lower().replace("_", "-")
