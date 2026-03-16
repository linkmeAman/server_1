"""CSV export endpoint for read-only table data."""

from __future__ import annotations

import io
import re

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from db.connection import db_cursor
from db.query_validator import MAX_ROWS

router = APIRouter(prefix="/api", tags=["db-explorer"])
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_FILTER_OPERATORS = {"contains", "equals", "starts_with", "ends_with"}


def _validate_identifier(name: str, field_name: str) -> str:
    if not _IDENTIFIER_RE.match(name or ""):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")
    return name


def _quoted(name: str) -> str:
    return f"`{name}`"


def _build_filter_clause(
    *,
    search: str | None,
    column: str | None,
    filter_columns: list[str],
    filter_values: list[str],
    filter_operators: list[str],
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []

    if search:
        if not column:
            raise HTTPException(status_code=400, detail="column is required when search is provided")
        safe_column = _validate_identifier(column, "column name")
        clauses.append(f"{_quoted(safe_column)} LIKE %s")
        params.append(f"%{search}%")

    if filter_columns or filter_values or filter_operators:
        if not (len(filter_columns) == len(filter_values) == len(filter_operators)):
            raise HTTPException(status_code=400, detail="Invalid multi-filter payload")

        for index, raw_column in enumerate(filter_columns):
            value = str(filter_values[index] or "").strip()
            operator = str(filter_operators[index] or "").strip().lower()
            if not value:
                continue

            safe_column = _validate_identifier(raw_column, "column name")
            if operator not in _ALLOWED_FILTER_OPERATORS:
                raise HTTPException(status_code=400, detail=f"Unsupported filter operator: {operator}")

            if operator == "contains":
                clauses.append(f"{_quoted(safe_column)} LIKE %s")
                params.append(f"%{value}%")
            elif operator == "starts_with":
                clauses.append(f"{_quoted(safe_column)} LIKE %s")
                params.append(f"{value}%")
            elif operator == "ends_with":
                clauses.append(f"{_quoted(safe_column)} LIKE %s")
                params.append(f"%{value}")
            else:
                clauses.append(f"{_quoted(safe_column)} = %s")
                params.append(value)

    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, params


@router.get("/export/{table_name}")
def export_table_csv(
    table_name: str,
    search: str | None = Query(default=None),
    column: str | None = Query(default=None),
    filter_column: list[str] = Query(default=[]),
    filter_value: list[str] = Query(default=[]),
    filter_operator: list[str] = Query(default=[]),
):
    safe_table = _validate_identifier(table_name, "table name")

    where_sql, params = _build_filter_clause(
        search=search,
        column=column,
        filter_columns=filter_column,
        filter_values=filter_value,
        filter_operators=filter_operator,
    )

    sql = f"SELECT * FROM {_quoted(safe_table)}{where_sql} LIMIT %s"
    params.append(MAX_ROWS)

    with db_cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    frame = pd.DataFrame(rows)
    csv_buffer = io.StringIO()
    frame.to_csv(csv_buffer, index=False)
    csv_buffer.seek(0)

    filename = f"{safe_table}.csv"
    headers = {"Content-Disposition": f"attachment; filename={filename}"}
    return StreamingResponse(csv_buffer, media_type="text/csv", headers=headers)
