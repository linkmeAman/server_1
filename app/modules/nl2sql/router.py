"""App-facing wrapper routes for the external NL2SQL service."""

from __future__ import annotations

import json
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.prism_guard import CallerContext
from app.core.response import error_response, success_response
from app.modules.nl2sql.dependencies import require_nl2sql_access
from app.modules.nl2sql.schemas.models import Nl2SqlRequest
from app.modules.nl2sql.services.client import Nl2SqlClient

router = APIRouter(prefix="/api/nl2sql/v1", tags=["nl2sql-v1"])

nl2sql_client = Nl2SqlClient()


def _resolve_request_id(request: Request, payload: Nl2SqlRequest | None = None) -> str:
    if payload and payload.request_id:
        return payload.request_id

    request_id = request.headers.get("X-Request-ID")
    if request_id:
        normalized = request_id.strip()
        if normalized:
            return normalized

    return str(uuid4())


def _success_response(data: dict, message: str, request_id: str) -> JSONResponse:
    payload = success_response(data=data, message=message).model_dump(mode="json")
    response = JSONResponse(status_code=200, content=payload)
    response.headers["X-Request-ID"] = request_id
    return response


def _error_response(
    *,
    request_id: str,
    status_code: int,
    error_code: str,
    message: str,
    data: dict | list | None = None,
) -> JSONResponse:
    response_data = {"request_id": request_id}
    if data is not None:
        response_data["details"] = data

    payload = error_response(
        error=error_code,
        message=message,
        data=response_data,
    ).model_dump(mode="json")
    response = JSONResponse(status_code=status_code, content=payload)
    response.headers["X-Request-ID"] = request_id
    return response


async def _parse_request_body(request: Request) -> tuple[Nl2SqlRequest | None, JSONResponse | None]:
    request_id = _resolve_request_id(request)

    try:
        body = await request.json()
    except Exception:
        return None, _error_response(
            request_id=request_id,
            status_code=400,
            error_code="BadRequest",
            message="Request body must be valid JSON",
        )

    if not isinstance(body, dict):
        return None, _error_response(
            request_id=request_id,
            status_code=400,
            error_code="BadRequest",
            message="Request body must be a JSON object",
        )

    try:
        payload = Nl2SqlRequest.model_validate(body)
    except ValidationError as exc:
        serialized_errors = json.loads(exc.json())
        return None, _error_response(
            request_id=request_id,
            status_code=422,
            error_code="ValidationError",
            message="Request validation failed",
            data=serialized_errors,
        )

    return payload, None


@router.post("/ask")
async def ask(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = await _parse_request_body(request)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request, payload)
    result = await nl2sql_client.ask(
        request_data=payload,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL ask completed", request_id)


@router.post("/generate-sql")
async def generate_sql(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = await _parse_request_body(request)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request, payload)
    result = await nl2sql_client.generate_sql(
        request_data=payload,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL SQL preview completed", request_id)
