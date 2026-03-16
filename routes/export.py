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


def _validate_identifier(name: str, field_name: str) -> str:
    if not _IDENTIFIER_RE.match(name or ""):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")
    return name


def _quoted(name: str) -> str:
    return f"`{name}`"


@router.get("/export/{table_name}")
def export_table_csv(
    table_name: str,
    search: str | None = Query(default=None),
    column: str | None = Query(default=None),
):
    safe_table = _validate_identifier(table_name, "table name")

    where_sql = ""
    params: list[object] = []

    if search:
        if not column:
            raise HTTPException(status_code=400, detail="column is required when search is provided")
        safe_column = _validate_identifier(column, "column name")
        where_sql = f" WHERE {_quoted(safe_column)} LIKE %s"
        params.append(f"%{search}%")

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
