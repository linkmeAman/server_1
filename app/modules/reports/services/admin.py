"""Admin lifecycle orchestration for report drafts."""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.reports.schemas.models import (
    LegacyImportBatchResponse,
    LegacyImportIssue,
    LegacyImportItemResult,
    LegacyReportCandidate,
    ReportDefinition,
    ReportDraftUpsertRequest,
    ReportFieldError,
    ReportImportDraftResponse,
    ReportVersionSummary,
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

    def collect_draft_validation_issues(self, definition: ReportDefinition) -> list[ReportFieldError]:
        return self.validator.collect_draft_issues(definition)

    async def get_report(
        self,
        central_db: AsyncSession,
        slug: str,
    ) -> ReportDefinition | None:
        rows = await self._load_db_definitions(central_db, slug=self._normalize_slug(slug), status="all")
        return rows[0] if rows else None

    async def list_report_versions(
        self,
        central_db: AsyncSession,
        slug: str,
    ) -> list[ReportVersionSummary]:
        normalized = self._normalize_slug(slug)
        existing = await self._fetch_report_row(central_db, normalized)
        if existing is None:
            raise ReportApiException(
                404,
                error_code="ReportNotFound",
                message="Report not found",
            )

        result = await central_db.execute(
            text(
                """
                SELECT
                    d.slug,
                    d.active_version_id,
                    v.id,
                    v.version,
                    v.status,
                    v.definition_json,
                    v.created_by_user_id,
                    v.created_at
                FROM report_definitions d
                JOIN report_versions v ON v.report_id = d.id
                WHERE d.id = :report_id
                ORDER BY v.version DESC
                """
            ),
            {"report_id": int(existing["id"])},
        )

        versions: list[ReportVersionSummary] = []
        for row in result.fetchall():
            row_map = row._mapping
            definition = self._definition_from_snapshot(
                row_map["definition_json"],
                slug=str(row_map["slug"]),
                status=str(row_map["status"]),
                version=int(row_map["version"]),
            )
            versions.append(
                ReportVersionSummary(
                    id=int(row_map["id"]),
                    slug=str(row_map["slug"]),
                    version=int(row_map["version"]),
                    status=str(row_map["status"]),
                    created_at=self._datetime_to_iso(row_map.get("created_at")),
                    created_by_user_id=row_map.get("created_by_user_id"),
                    is_active=int(row_map["id"]) == int(existing["active_version_id"]),
                    is_published=str(row_map["status"]) == "published",
                    report=definition,
                )
            )
        return versions

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

    async def restore_report_version(
        self,
        central_db: AsyncSession,
        slug: str,
        version: int,
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
                message="Archived reports cannot be restored.",
            )

        result = await central_db.execute(
            text(
                """
                SELECT definition_json
                FROM report_versions
                WHERE report_id = :report_id
                  AND version = :version
                LIMIT 1
                """
            ),
            {"report_id": int(existing["id"]), "version": int(version)},
        )
        row = result.fetchone()
        if row is None:
            raise ReportApiException(
                404,
                error_code="ReportVersionNotFound",
                message="Report version not found",
            )

        definition = self._definition_from_snapshot(
            row._mapping["definition_json"],
            slug=normalized,
            status="draft",
            version=int(version),
        )
        payload = ReportDraftUpsertRequest.model_validate(definition.model_dump(mode="python"))
        return await self._save_report(
            central_db,
            payload,
            slug=normalized,
            user_id=user_id,
            report_id=int(existing["id"]),
            validate=False,
        )

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

        archived_rows = await self._load_db_definitions(
            central_db,
            slug=normalized,
            status="archived",
        )
        archived = archived_rows[0] if archived_rows else None
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
        batch = await self.import_legacy_reports(
            central_db,
            main_db,
            report_ids=[int(report_id)],
            user_id=user_id,
        )
        result = batch.results[0]
        if result.report is None:
            first_issue = result.issues[0] if result.issues else None
            raise ReportApiException(
                404 if first_issue and first_issue.code == "legacy_report_not_found" else 503,
                error_code="LegacyImportFailed",
                message=first_issue.message if first_issue else "Legacy report import failed.",
                data={"issues": [item.model_dump(mode="json") for item in result.issues]},
            )
        return ReportImportDraftResponse(
            report=result.report,
            warnings=[item.message for item in result.issues],
            imported_legacy_report_id=int(report_id),
        )

    async def list_legacy_reports(
        self,
        central_db: AsyncSession,
        main_db: AsyncSession,
    ) -> list[LegacyReportCandidate]:
        candidates = await self.legacy_imports.list_legacy_candidates(main_db)
        migration_map = await self._load_legacy_migration_map(central_db)
        rows: list[LegacyReportCandidate] = []
        for candidate in candidates:
            existing = migration_map.get(int(candidate.legacy_report_id))
            if existing is None:
                rows.append(candidate)
                continue
            rows.append(
                candidate.model_copy(
                    update={
                        "already_migrated": True,
                        "existing_report_slug": existing["slug"],
                        "existing_report_status": existing["status"],
                        "available_for_import": False,
                        "unavailable_reason": (
                            f"Already migrated to {existing['slug']} ({existing['status']})."
                        ),
                    }
                )
            )
        return rows

    async def import_legacy_reports(
        self,
        central_db: AsyncSession,
        main_db: AsyncSession,
        *,
        report_ids: list[int],
        user_id: int,
    ) -> LegacyImportBatchResponse:
        results: list[LegacyImportItemResult] = []
        for report_id in report_ids:
            results.append(
                await self._import_legacy_report_item(
                    central_db,
                    main_db,
                    report_id=int(report_id),
                    user_id=user_id,
                )
            )

        imported_count = sum(1 for item in results if item.status == "imported")
        imported_with_issues_count = sum(
            1 for item in results if item.status == "imported_with_issues"
        )
        failed_count = sum(1 for item in results if item.status == "failed")
        return LegacyImportBatchResponse(
            results=results,
            total_requested=len(report_ids),
            imported_count=imported_count,
            imported_with_issues_count=imported_with_issues_count,
            failed_count=failed_count,
        )

    async def _save_report(
        self,
        central_db: AsyncSession,
        payload: ReportDraftUpsertRequest,
        *,
        slug: str,
        user_id: int,
        report_id: int | None = None,
        validate: bool = True,
    ) -> ReportDefinition:
        definition = self._build_definition(payload, slug=slug)
        if validate:
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
            status_result = await central_db.execute(
                text(
                    """
                    SELECT status
                    FROM report_definitions
                    WHERE id = :report_id
                    LIMIT 1
                    """
                ),
                {"report_id": int(report_id)},
            )
            status_row = status_result.fetchone()
            current_status = str(status_row._mapping["status"]) if status_row else "draft"
            next_definition_status = (
                "published" if current_status == "published" else "draft"
            )
            await central_db.execute(
                text(
                    """
                    UPDATE report_definitions
                    SET slug = :slug,
                        name = :name,
                        description = :description,
                        category = :category,
                        kind = :kind,
                        status = :definition_status,
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
                    "definition_status": next_definition_status,
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
        return definition.model_copy(update={"version": next_version, "status": "draft"})

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
        if status == "draft":
            clauses.append("v.status = 'draft'")
            clauses.append("d.status <> 'archived'")
        elif status == "published":
            clauses.append("d.status = 'published'")
            clauses.append("v.status = 'published'")
        elif status == "archived":
            clauses.append("d.status = 'archived'")
        elif status != "all":
            clauses.append("d.status = :status")
            params["status"] = status
        else:
            clauses.append("d.status <> 'archived'")
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        result = await central_db.execute(
            text(
                f"""
                SELECT
                    d.id,
                    d.slug,
                    d.status AS definition_status,
                    d.active_version_id,
                    v.version,
                    v.status AS version_status,
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
                definitions.append(
                    self._definition_from_snapshot(
                        row_map["definition_json"],
                        slug=str(row_map["slug"]),
                        status=str(row_map["version_status"]),
                        version=int(row_map["version"]),
                    )
                )
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

    async def _import_legacy_report_item(
        self,
        central_db: AsyncSession,
        main_db: AsyncSession,
        *,
        report_id: int,
        user_id: int,
    ) -> LegacyImportItemResult:
        try:
            imported = await self.legacy_imports.build_draft_from_legacy(
                main_db,
                report_id=report_id,
            )
            payload = ReportDraftUpsertRequest.model_validate(imported["definition"])
            slug = self._normalize_slug(payload.slug)
            existing = await self._fetch_report_row(central_db, slug)
            if existing is None:
                report = await self._save_report(
                    central_db,
                    payload,
                    slug=slug,
                    user_id=user_id,
                    validate=False,
                )
            else:
                report = await self._save_report(
                    central_db,
                    payload,
                    slug=slug,
                    user_id=user_id,
                    report_id=int(existing["id"]),
                    validate=False,
                )
            issues = [
                LegacyImportIssue.model_validate(item)
                for item in imported.get("issues") or []
            ]
            return LegacyImportItemResult(
                legacy_report_id=int(report_id),
                name=str(imported.get("report_name") or report.name),
                status="imported_with_issues" if issues else "imported",
                report=report,
                issues=issues,
            )
        except HTTPException as exc:
            return LegacyImportItemResult(
                legacy_report_id=int(report_id),
                name=f"Legacy Report {report_id}",
                status="failed",
                issues=[
                    self._legacy_failure_issue(
                        "legacy_report_not_found" if exc.status_code == 404 else "legacy_report_unavailable",
                        str(exc.detail) or "Legacy report import failed.",
                    )
                ],
            )
        except Exception as exc:
            return LegacyImportItemResult(
                legacy_report_id=int(report_id),
                name=f"Legacy Report {report_id}",
                status="failed",
                issues=[
                    self._legacy_failure_issue(
                        "legacy_import_failed",
                        "The legacy report could not be imported. Try again, or inspect the technical detail below.",
                        technical_detail=str(exc),
                    )
                ],
            )

    async def _load_legacy_migration_map(
        self,
        central_db: AsyncSession,
    ) -> dict[int, dict[str, str]]:
        try:
            result = await central_db.execute(
                text(
                    """
                    SELECT slug, status, source_legacy_report_id
                    FROM report_definitions
                    WHERE source_legacy_report_id IS NOT NULL
                    """
                )
            )
        except Exception:
            return {}

        rows: dict[int, dict[str, str]] = {}
        for row in result.fetchall():
            row_map = row._mapping
            legacy_id = row_map.get("source_legacy_report_id")
            if legacy_id is None:
                continue
            rows[int(legacy_id)] = {
                "slug": str(row_map.get("slug") or ""),
                "status": str(row_map.get("status") or "draft"),
            }
        return rows

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
    def _definition_from_snapshot(
        definition_json: str,
        *,
        slug: str,
        status: str,
        version: int,
    ) -> ReportDefinition:
        payload = json.loads(definition_json)
        payload["slug"] = slug
        payload["status"] = status
        payload["version"] = version
        return ReportDefinition.model_validate(payload)

    @staticmethod
    def _datetime_to_iso(value: Any) -> str | None:
        if value is None:
            return None
        isoformat = getattr(value, "isoformat", None)
        if callable(isoformat):
            return str(isoformat())
        return str(value)

    @staticmethod
    def _normalize_slug(value: str) -> str:
        return value.strip().lower().replace("_", "-")

    @staticmethod
    def _legacy_failure_issue(
        code: str,
        message: str,
        *,
        technical_detail: str | None = None,
    ) -> LegacyImportIssue:
        return LegacyImportIssue(
            code=code,
            message=message,
            technical_detail=technical_detail,
        )
