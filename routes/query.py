"""Ad-hoc read-only query endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError

from app.core.prism_guard import require_any_caller
from db.connection import db_cursor
from db.query_validator import MAX_ROWS, QueryValidationError, apply_row_limit, validate_query
from routes.db_explorer_security import normalize_database_name
from routes.query_transport_crypto import build_response, parse_request_payload

router = APIRouter(
    prefix="/api",
    tags=["db-explorer"],
    dependencies=[Depends(require_any_caller)],
)


def _optional_db_name(db: str | None) -> str | None:
    return normalize_database_name(db)


class QueryRequest(BaseModel):
    query: str


@router.post("/query")
async def run_select_query(request: Request, db: str | None = Query(default=None)):
    payload_data = await parse_request_payload(request)
    try:
        payload = QueryRequest.model_validate(payload_data)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="Invalid query request payload") from exc

    try:
        validated = validate_query(payload.query)
        bounded = apply_row_limit(validated, max_rows=MAX_ROWS)
    except QueryValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with db_cursor(database=_optional_db_name(db)) as cursor:
        cursor.execute(bounded)
        rows = cursor.fetchall()

    return build_response({
        "rows": rows,
        "row_count": len(rows),
        "max_rows": MAX_ROWS,
    }, request)
