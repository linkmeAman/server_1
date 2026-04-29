"""Legacy report metadata import helpers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.reports.schemas.models import ReportCatalogItem, ReportDefinition


class LegacyReportImportService:
    """Builds draft report definitions from the old CRM metadata tables.

    The importer intentionally produces draft definitions. It does not publish
    unsafe legacy behavior such as raw SQL filters or PHP-evaluated button
    predicates without a human review step.
    """

    async def list_legacy_catalog_items(
        self,
        main_db: AsyncSession,
        *,
        exclude_report_ids: set[int] | None = None,
    ) -> list[ReportCatalogItem]:
        """Return active legacy report metadata for supreme-user discovery.

        These entries are intentionally marked unavailable. They make the old
        report inventory visible in the new catalog without executing legacy
        report SQL before each report is migrated and reviewed.
        """

        excluded = exclude_report_ids or set()
        try:
            result = await main_db.execute(
                text(
                    """
                    SELECT id, name, subtitle
                    FROM report
                    WHERE report = 1
                      AND park = 0
                    ORDER BY name ASC, id ASC
                    """
                )
            )
        except Exception:
            return []

        items: list[ReportCatalogItem] = []
        for row in result.fetchall():
            item = dict(row._mapping)
            report_id = int(item["id"])
            if report_id in excluded:
                continue
            slug = f"legacy-{report_id}"
            items.append(
                ReportCatalogItem(
                    slug=slug,
                    name=str(item.get("name") or f"Legacy Report {report_id}"),
                    description=str(item.get("subtitle") or "") or "Legacy report pending migration.",
                    category="Legacy Reports",
                    kind="table",
                    status="draft",
                    route_path=f"/reports/{slug}",
                    prism_resource_code=f"reports.legacy_{report_id}",
                    legacy_report_id=report_id,
                    source_label="legacy-pending",
                    available=False,
                    unavailable_reason=(
                        "This legacy report is visible to supreme users, "
                        "but it must be migrated before it can open in the new report renderer."
                    ),
                )
            )
        return items

    async def build_draft_from_legacy(
        self,
        main_db: AsyncSession,
        *,
        report_id: int,
    ) -> dict[str, Any]:
        report = await self._fetch_report(main_db, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Legacy report not found")

        columns = await self._fetch_columns(main_db, report_id)
        buttons = await self._fetch_buttons(main_db, report_id)
        warnings: list[str] = []
        if any(str(item.get("query") or "").strip() for item in buttons):
            warnings.append("Legacy row button predicates require manual conversion to the safe predicate DSL.")
        if int(report.get("dynamic_report") or 0) == 1:
            warnings.append("Dynamic report SQL requires review before publication.")

        visible_columns = [
            {
                "key": str(item["column_name"]),
                "label": str(item.get("header") or item["column_name"]),
                "visible": int(item.get("position") or 0) > 0,
                "sortable": int(item.get("position") or 0) > 0,
                "searchable": int(item.get("position") or 0) > 0,
                "exportable": int(item.get("export") or 0) == 1,
            }
            for item in columns
            if str(item.get("column_name") or "").strip()
        ]

        # Legacy reports often store filter/scope columns outside report_column.
        # Keep them hidden but declared so structured query validation can use them.
        declared_keys = {str(item.get("key") or "").strip() for item in visible_columns}
        date_column = str(report.get("date_filter_col") or "").strip()
        if date_column and date_column not in declared_keys:
            visible_columns.append(
                {
                    "key": date_column,
                    "label": date_column.replace("_", " ").title(),
                    "visible": False,
                    "sortable": False,
                    "searchable": False,
                    "exportable": False,
                }
            )
            declared_keys.add(date_column)

        if int(report.get("check_bid") or 0) == 1 and "bid" not in declared_keys:
            visible_columns.append(
                {
                    "key": "bid",
                    "label": "Bid",
                    "visible": False,
                    "sortable": False,
                    "searchable": False,
                    "exportable": False,
                }
            )
            declared_keys.add("bid")

        slug = f"legacy-{int(report_id)}"
        definition = ReportDefinition.model_validate(
            {
                "slug": slug,
                "name": str(report.get("name") or f"Legacy Report {report_id}"),
                "description": str(report.get("subtitle") or "") or None,
                "category": "Legacy Imports",
                "status": "draft",
                "kind": "table",
                "legacy_report_id": int(report_id),
                "prism_resource_code": f"reports.{slug.replace('-', '_')}",
                "legacy_view_action": "report:read",
                "source": {
                    "type": "table",
                    "table": str(report.get("table_name") or ""),
                    "date_column": str(report.get("date_filter_col") or "") or None,
                    "branch_column": "bid" if int(report.get("check_bid") or 0) == 1 else None,
                },
                "columns": visible_columns,
                "default_sort": [],
                "search_columns": [
                    item["key"]
                    for item in visible_columns
                    if item.get("visible")
                ],
                "date_range": {
                    "enabled": int(report.get("report_option") or 0) in {1, 3},
                    "default_days": 7,
                    "column": str(report.get("date_filter_col") or "") or None,
                },
                "branch_scope": {
                    "mode": "token_branch" if int(report.get("check_bid") or 0) == 1 else "all",
                    "column": "bid" if int(report.get("check_bid") or 0) == 1 else None,
                },
                "source_label": "legacy-import",
                "route_path": f"/reports/{slug}",
            }
        )

        return {
            "definition": definition.model_dump(mode="json"),
            "warnings": warnings,
        }

    async def _fetch_report(self, db: AsyncSession, report_id: int) -> dict[str, Any] | None:
        try:
            result = await db.execute(
                text(
                    """
                    SELECT id, name, subtitle, table_name, date_filter_col,
                           report_option, dynamic_report, check_bid
                    FROM report
                    WHERE id = :report_id
                    LIMIT 1
                    """
                ),
                {"report_id": int(report_id)},
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Legacy report tables are unavailable") from exc
        row = result.fetchone()
        return dict(row._mapping) if row else None

    async def _fetch_columns(self, db: AsyncSession, report_id: int) -> list[dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT column_name, header, position, export
                FROM report_column
                WHERE report_id = :report_id
                ORDER BY position ASC, id ASC
                """
            ),
            {"report_id": int(report_id)},
        )
        return [dict(row._mapping) for row in result.fetchall()]

    async def _fetch_buttons(self, db: AsyncSession, report_id: int) -> list[dict[str, Any]]:
        try:
            result = await db.execute(
                text(
                    """
                    SELECT button_tt, module_id, permission, query
                    FROM report_button
                    WHERE report_id = :report_id
                      AND hide <> 1
                    """
                ),
                {"report_id": int(report_id)},
            )
        except Exception:
            return []
        return [dict(row._mapping) for row in result.fetchall()]
