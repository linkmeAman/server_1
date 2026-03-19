"""Ad-hoc read-only query endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from db.connection import db_cursor
from db.query_validator import MAX_ROWS, QueryValidationError, apply_row_limit, validate_query

router = APIRouter(prefix="/api", tags=["db-explorer"])


def _optional_db_name(db: str | None) -> str | None:
    if db is None:
        return None
    cleaned = db.strip()
    if not cleaned:
        return None
    return cleaned


class QueryRequest(BaseModel):
    query: str


@router.post("/query")
def run_select_query(payload: QueryRequest, db: str | None = Query(default=None)):
    try:
        validated = validate_query(payload.query)
        bounded = apply_row_limit(validated, max_rows=MAX_ROWS)
    except QueryValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with db_cursor(database=_optional_db_name(db)) as cursor:
        cursor.execute(bounded)
        rows = cursor.fetchall()

    return {
        "rows": rows,
        "row_count": len(rows),
        "max_rows": MAX_ROWS,
    }
