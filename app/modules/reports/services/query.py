"""Safe structured report query execution."""

from __future__ import annotations

import re
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.prism_guard import CallerContext
from app.modules.reports.schemas.models import (
    ReportAction,
    ReportDefinition,
    ReportQueryFilter,
    ReportQueryRequest,
    ReportQueryResponse,
    ReportQuerySort,
)

from .permission import ReportPermissionService

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ReportQueryService:
    def __init__(self, permissions: ReportPermissionService | None = None) -> None:
        self.permissions = permissions or ReportPermissionService()

    async def run_query(
        self,
        main_db: AsyncSession,
        central_db: AsyncSession,
        caller: CallerContext,
        definition: ReportDefinition,
        request: ReportQueryRequest,
        *,
        request_id: str,
    ) -> ReportQueryResponse:
        if definition.kind != "table" or definition.source.type != "table":
            raise HTTPException(status_code=400, detail="This report is route-backed and cannot be queried generically")
        if not definition.source.table:
            raise HTTPException(status_code=400, detail="Report source table is not configured")

        started = time.time()
        status = "success"
        error_code: str | None = None
        rows: list[dict[str, Any]] = []
        total = 0
        try:
            rows, total, sort = await self._execute(main_db, caller, definition, request)
            actions = await self._allowed_actions(central_db, caller, definition)
            return ReportQueryResponse(
                slug=definition.slug,
                columns=[col for col in definition.columns if col.visible],
                rows=rows,
                total=total,
                page=request.page,
                page_size=request.page_size,
                sort=sort,
                actions=actions,
            )
        except HTTPException as exc:
            status = "error"
            error_code = str(exc.status_code)
            raise
        except Exception as exc:
            status = "error"
            error_code = exc.__class__.__name__
            raise HTTPException(status_code=500, detail="Report query failed") from exc
        finally:
            await self._log_run(
                central_db,
                caller=caller,
                definition=definition,
                request=request,
                request_id=request_id,
                row_count=len(rows),
                status=status,
                error_code=error_code,
                duration_ms=int((time.time() - started) * 1000),
            )

    async def _execute(
        self,
        main_db: AsyncSession,
        caller: CallerContext,
        definition: ReportDefinition,
        request: ReportQueryRequest,
    ) -> tuple[list[dict[str, Any]], int, list[ReportQuerySort]]:
        table = self._identifier(definition.source.table or "", "table")
        visible_columns = [column.key for column in definition.columns if column.visible]
        if not visible_columns:
            raise HTTPException(status_code=400, detail="Report has no visible columns")

        for column in visible_columns:
            self._identifier(column, "column")

        where_sql, params = self._build_where(definition, request, caller)
        sort = self._resolve_sort(definition, request)
        order_sql = ", ".join(
            f"`{self._identifier(item.column, 'sort column')}` {item.direction.upper()}"
            for item in sort
        )
        if not order_sql:
            order_sql = f"`{self._identifier(visible_columns[0], 'sort column')}` ASC"

        limit = int(request.page_size)
        offset = (int(request.page) - 1) * limit
        params.update({"limit": limit, "offset": offset})

        column_sql = ", ".join(f"`{column}`" for column in visible_columns)
        data_sql = (
            f"SELECT {column_sql} FROM `{table}` "
            f"{where_sql} ORDER BY {order_sql} LIMIT :limit OFFSET :offset"
        )
        count_sql = f"SELECT COUNT(*) AS total FROM `{table}` {where_sql}"

        count_result = await main_db.execute(text(count_sql), params)
        count_row = count_result.fetchone()
        total = int(count_row._mapping["total"]) if count_row else 0

        data_result = await main_db.execute(text(data_sql), params)
        rows = [self._serialize_row(dict(row._mapping)) for row in data_result.fetchall()]
        return rows, total, sort

    def _build_where(
        self,
        definition: ReportDefinition,
        request: ReportQueryRequest,
        caller: CallerContext,
    ) -> tuple[str, dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}

        filter_columns = {item.column for item in definition.filters}
        search_columns = set(definition.search_columns)
        sortable_columns = {item.key for item in definition.columns if item.sortable}
        visible_columns = {item.key for item in definition.columns}
        allowed_columns = visible_columns | filter_columns | search_columns | sortable_columns

        date_column = definition.date_range.column or definition.source.date_column
        if request.date_range and date_column:
            self._ensure_allowed_column(date_column, allowed_columns)
            if request.date_range.start:
                clauses.append(f"`{date_column}` >= :date_start")
                params["date_start"] = request.date_range.start
            if request.date_range.end:
                clauses.append(f"`{date_column}` <= :date_end")
                params["date_end"] = request.date_range.end

        if definition.branch_scope.mode == "token_branch":
            branch_column = definition.branch_scope.column or definition.source.branch_column
            if not branch_column:
                raise HTTPException(status_code=400, detail="Report branch scope is missing a column")
            branch_id = caller.token_claims.get("bid") or caller.token_claims.get("branch_id")
            if branch_id is None:
                raise HTTPException(status_code=400, detail="Current branch is not available for this report")
            self._ensure_allowed_column(branch_column, allowed_columns | {branch_column})
            clauses.append(f"`{branch_column}` = :branch_id")
            params["branch_id"] = branch_id

        for index, item in enumerate(request.filters):
            clause, clause_params = self._filter_clause(index, item, allowed_columns, definition)
            clauses.append(clause)
            params.update(clause_params)

        search_value = (request.search or "").strip()
        if search_value and definition.search_columns:
            search_parts: list[str] = []
            for index, column in enumerate(definition.search_columns):
                self._ensure_allowed_column(column, allowed_columns)
                param = f"search_{index}"
                search_parts.append(f"`{column}` LIKE :{param}")
                params[param] = f"%{search_value}%"
            clauses.append(f"({' OR '.join(search_parts)})")

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where_sql, params

    def _filter_clause(
        self,
        index: int,
        item: ReportQueryFilter,
        allowed_columns: set[str],
        definition: ReportDefinition,
    ) -> tuple[str, dict[str, Any]]:
        self._ensure_allowed_column(item.column, allowed_columns)
        configured = next((flt for flt in definition.filters if flt.column == item.column), None)
        if configured is None or item.operator not in configured.operators:
            raise HTTPException(status_code=400, detail=f"Unsupported filter for column '{item.column}'")

        column = self._identifier(item.column, "filter column")
        base = f"filter_{index}"
        op = item.operator
        value = item.value

        if op == "is_null":
            return f"`{column}` IS NULL", {}
        if op == "not_null":
            return f"`{column}` IS NOT NULL", {}
        if op == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise HTTPException(status_code=400, detail="between filter requires two values")
            return f"`{column}` BETWEEN :{base}_start AND :{base}_end", {
                f"{base}_start": value[0],
                f"{base}_end": value[1],
            }
        if op == "in":
            if not isinstance(value, list) or not value:
                raise HTTPException(status_code=400, detail="in filter requires values")
            params = {f"{base}_{idx}": entry for idx, entry in enumerate(value[:100])}
            placeholders = ", ".join(f":{key}" for key in params)
            return f"`{column}` IN ({placeholders})", params

        sql_op = {
            "eq": "=",
            "ne": "!=",
            "gte": ">=",
            "lte": "<=",
            "gt": ">",
            "lt": "<",
        }.get(op)
        if sql_op:
            return f"`{column}` {sql_op} :{base}", {base: value}
        if op == "contains":
            return f"`{column}` LIKE :{base}", {base: f"%{value}%"}
        if op == "starts_with":
            return f"`{column}` LIKE :{base}", {base: f"{value}%"}

        raise HTTPException(status_code=400, detail=f"Unsupported operator '{op}'")

    def _resolve_sort(
        self,
        definition: ReportDefinition,
        request: ReportQueryRequest,
    ) -> list[ReportQuerySort]:
        sortable = {item.key for item in definition.columns if item.sortable}
        requested = request.sort or definition.default_sort
        output: list[ReportQuerySort] = []
        for item in requested:
            if item.column not in sortable:
                raise HTTPException(status_code=400, detail=f"Column '{item.column}' cannot be sorted")
            output.append(ReportQuerySort(column=item.column, direction=item.direction))
        return output

    async def _allowed_actions(
        self,
        central_db: AsyncSession,
        caller: CallerContext,
        definition: ReportDefinition,
    ) -> list[ReportAction]:
        output: list[ReportAction] = []
        for action in definition.actions:
            if await self.permissions.action_allowed(
                caller,
                central_db,
                definition=definition,
                action_key=action.key,
                declared_permission=action.permission,
            ):
                output.append(action)
        return output

    async def _log_run(
        self,
        central_db: AsyncSession,
        *,
        caller: CallerContext,
        definition: ReportDefinition,
        request: ReportQueryRequest,
        request_id: str,
        row_count: int,
        status: str,
        error_code: str | None,
        duration_ms: int,
    ) -> None:
        try:
            await central_db.execute(
                text(
                    """
                    INSERT INTO report_run_logs
                        (request_id, report_slug, report_version, user_id,
                         employee_id, action, request_json, result_count,
                         status, error_code, duration_ms)
                    VALUES
                        (:request_id, :report_slug, :report_version, :user_id,
                         :employee_id, 'query', :request_json, :result_count,
                         :status, :error_code, :duration_ms)
                    """
                ),
                {
                    "request_id": request_id,
                    "report_slug": definition.slug,
                    "report_version": definition.version,
                    "user_id": int(caller.user_id),
                    "employee_id": caller.employee_id,
                    "request_json": request.model_dump_json(),
                    "result_count": int(row_count),
                    "status": status,
                    "error_code": error_code,
                    "duration_ms": int(duration_ms),
                },
            )
            await central_db.commit()
        except Exception:
            await central_db.rollback()

    def _ensure_allowed_column(self, column: str, allowed_columns: set[str]) -> None:
        column = self._identifier(column, "column")
        if column not in allowed_columns:
            raise HTTPException(status_code=400, detail=f"Column '{column}' is not allowed")

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        if not value or not IDENTIFIER_RE.match(value):
            raise HTTPException(status_code=400, detail=f"Invalid {label}")
        return value

    @staticmethod
    def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, (datetime, date)):
                output[key] = value.isoformat()
            elif isinstance(value, Decimal):
                output[key] = float(value)
            else:
                output[key] = value
        return output
