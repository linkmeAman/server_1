"""Table browsing endpoints for read-only database explorer."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from db.connection import db_cursor, serialize_db_rows
from db.query_validator import MAX_ROWS
from routes.db_explorer_permissions import require_db_explorer_access
from routes.db_explorer_security import (
    filter_database_list,
    normalize_database_name,
    validate_identifier,
)

router = APIRouter(
    prefix="/api",
    tags=["db-explorer"],
    dependencies=[Depends(require_db_explorer_access)],
)
DEFAULT_LIMIT = 50
_ALLOWED_FILTER_OPERATORS = {
    "=", ">", ">=", "<", "<=", "!=", 
    "like", "like %...%", "not like", "not like %...%", 
    "in (...)", "not in (...)", "between", "not between", 
    "is null", "is not null"
}


def _validate_identifier(name: str, field_name: str) -> str:
    return validate_identifier(name, field_name)


def _quoted(name: str) -> str:
    return f"`{name}`"


def _optional_db_name(db: str | None) -> str | None:
    return normalize_database_name(db)


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


@router.get("/tables")
def list_tables(db: str | None = Query(default=None)):
    selected_db = _optional_db_name(db)

    with db_cursor(database=selected_db) as cursor:
        cursor.execute("SHOW TABLES")
        rows = cursor.fetchall()

    tables = []
    for row in rows:
        if not row:
            continue
        tables.append(next(iter(row.values())))

    return {"tables": sorted(tables)}


@router.get("/databases")
def list_databases():
    with db_cursor() as cursor:
        cursor.execute("SHOW DATABASES")
        rows = cursor.fetchall()

    databases: list[str] = []
    for row in rows:
        if not row:
            continue
        value = str(next(iter(row.values()), "") or "").strip()
        if not value:
            continue
        databases.append(value)

    visible_databases = filter_database_list(databases)
    return {"databases": sorted(visible_databases)}


@router.get("/schema/{table_name}")
def describe_table(table_name: str, db: str | None = Query(default=None)):
    safe_table = _validate_identifier(table_name, "table name")
    selected_db = _optional_db_name(db)

    with db_cursor(database=selected_db) as cursor:
        cursor.execute(f"DESCRIBE {_quoted(safe_table)}")
        schema_rows = cursor.fetchall()

        view_query = None
        is_view = False
        try:
            cursor.execute(f"SHOW CREATE VIEW {_quoted(safe_table)}")
            view_result = cursor.fetchone()
            if view_result and "Create View" in view_result:
                view_query = view_result["Create View"]
                is_view = True
        except Exception:
            pass

    return {"table": safe_table, "schema": schema_rows, "is_view": is_view, "view_query": view_query}


@router.get("/table/{table_name}")
def table_rows(
    table_name: str,
    db: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_ROWS),
    offset: int = Query(default=0, ge=0),
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

    data_sql = (
        f"SELECT * FROM {_quoted(safe_table)}"
        f"{where_sql}{order_sql} LIMIT %s OFFSET %s"
    )
    data_params = [*params, limit, offset]

    count_sql = f"SELECT COUNT(*) AS total FROM {_quoted(safe_table)}{where_sql}"

    with db_cursor(database=selected_db) as cursor:
        cursor.execute(count_sql, params)
        total_row = cursor.fetchone() or {"total": 0}

        cursor.execute(data_sql, data_params)
        rows = cursor.fetchall()

    return {
        "table": safe_table,
        "rows": serialize_db_rows(rows),
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": int(total_row.get("total", 0)),
        },
    }
