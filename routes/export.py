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
_ALLOWED_FILTER_OPERATORS = {
    "=", ">", ">=", "<", "<=", "!=", 
    "like", "like %...%", "not like", "not like %...%", 
    "in (...)", "not in (...)", "between", "not between", 
    "is null", "is not null"
}


def _validate_identifier(name: str, field_name: str) -> str:
    if not _IDENTIFIER_RE.match(name or ""):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")
    return name


def _quoted(name: str) -> str:
    return f"`{name}`"


def _optional_db_name(db: str | None) -> str | None:
    if db is None:
        return None
    cleaned = db.strip()
    if not cleaned:
        return None
    return _validate_identifier(cleaned, "database name")


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
            operator = str(filter_operators[index] or "").strip().lower()
            value = str(filter_values[index] or "").strip()
            
            if not value and operator not in ("is null", "is not null"):
                continue

            safe_column = _validate_identifier(raw_column, "column name")
            if operator not in _ALLOWED_FILTER_OPERATORS:
                raise HTTPException(status_code=400, detail=f"Unsupported filter operator: {operator}")

            if operator == "like %...%":
                clauses.append(f"{_quoted(safe_column)} LIKE %s")
                params.append(f"%{value}%")
            elif operator == "not like %...%":
                clauses.append(f"{_quoted(safe_column)} NOT LIKE %s")
                params.append(f"%{value}%")
            elif operator in ("like", "not like"):
                clauses.append(f"{_quoted(safe_column)} {operator.upper()} %s")
                params.append(value)
            elif operator == "is null":
                clauses.append(f"{_quoted(safe_column)} IS NULL")
            elif operator == "is not null":
                clauses.append(f"{_quoted(safe_column)} IS NOT NULL")
            elif operator in ("in (...)", "not in (...)"):
                op_sql = "IN" if operator == "in (...)" else "NOT IN"
                values = [v.strip() for v in value.split(",")]
                placeholders = ", ".join(["%s"] * len(values))
                clauses.append(f"{_quoted(safe_column)} {op_sql} ({placeholders})")
                params.extend(values)
            elif operator in ("between", "not between"):
                op_sql = "BETWEEN" if operator == "between" else "NOT BETWEEN"
                parts = value.split(" AND ") if " AND " in value else value.split(" and ")
                if len(parts) == 2:
                    clauses.append(f"{_quoted(safe_column)} {op_sql} %s AND %s")
                    params.extend([parts[0].strip(), parts[1].strip()])
                else:
                    clauses.append(f"{_quoted(safe_column)} = %s") # fallback if invalid between syntax
                    params.append(value)
            else:
                clauses.append(f"{_quoted(safe_column)} {operator.upper()} %s")
                params.append(value)

    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, params


@router.get("/export/{table_name}")
def export_table_csv(
    table_name: str,
    db: str | None = Query(default=None),
    search: str | None = Query(default=None),
    column: str | None = Query(default=None),
    filter_column: list[str] = Query(default=[]),
    filter_value: list[str] = Query(default=[]),
    filter_operator: list[str] = Query(default=[]),
    sort_column: str | None = Query(default=None),
    sort_direction: str | None = Query(default="asc"),
):
    safe_table = _validate_identifier(table_name, "table name")
    selected_db = _optional_db_name(db)

    where_sql, params = _build_filter_clause(
        search=search,
        column=column,
        filter_columns=filter_column,
        filter_values=filter_value,
        filter_operators=filter_operator,
    )

    order_sql = ""
    if sort_column:
        safe_sort_column = _validate_identifier(sort_column, "sort column")
        safe_sort_dir = "ASC" if sort_direction and sort_direction.lower() == "asc" else "DESC"
        order_sql = f" ORDER BY {_quoted(safe_sort_column)} {safe_sort_dir}"

    sql = f"SELECT * FROM {_quoted(safe_table)}{where_sql}{order_sql} LIMIT %s"
    params.append(MAX_ROWS)

    with db_cursor(database=selected_db) as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    frame = pd.DataFrame(rows)
    csv_buffer = io.StringIO()
    frame.to_csv(csv_buffer, index=False)
    csv_buffer.seek(0)

    filename = f"{safe_table}.csv"
    headers = {"Content-Disposition": f"attachment; filename={filename}"}
    return StreamingResponse(csv_buffer, media_type="text/csv", headers=headers)
