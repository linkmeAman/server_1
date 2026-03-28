"""Explicit SQL gateway route for structured single-table operations."""

from __future__ import annotations

import json
import logging
import threading
import time
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.response import error_response, success_response
from core.security import validate_token
from core.settings import get_settings
from core.sql_gateway import SQLGatewayError, execute_gateway_request, parse_gateway_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/query", tags=["query-gateway"])

_rate_lock = threading.Lock()
_rate_buckets = {}


def reset_query_gateway_rate_limiter() -> None:
    """Reset in-memory limiter for isolated tests."""
    with _rate_lock:
        _rate_buckets.clear()


def _request_id(request: Request) -> str:
    request_id = request.headers.get("X-Request-ID")
    if request_id:
        return request_id
    return str(uuid4())


def _error_response(code: str, message: str, status_code: int, request_id: str) -> JSONResponse:
    payload = error_response(
        error=code,
        message=message,
        data={"request_id": request_id},
    ).model_dump(mode="json")
    response = JSONResponse(content=payload, status_code=status_code)
    response.headers["X-Request-ID"] = request_id
    return response


def _success_response(data: dict, request_id: str) -> JSONResponse:
    payload = success_response(
        data={**data, "request_id": request_id},
        message="Query executed successfully",
    ).model_dump(mode="json")
    response = JSONResponse(content=payload, status_code=200)
    response.headers["X-Request-ID"] = request_id
    return response


def _authenticate_request(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise SQLGatewayError("SQLGW_UNAUTHORIZED", "Missing or invalid Authorization header", 401)

    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        raise SQLGatewayError("SQLGW_UNAUTHORIZED", "Missing access token", 401)

    try:
        claims = validate_token(token, expected_type="access")
        return claims
    except Exception as exc:
        raise SQLGatewayError("SQLGW_UNAUTHORIZED", "Invalid or expired access token", 401) from exc


def _is_rate_limited(key: str, limit_per_minute: int) -> bool:
    if limit_per_minute <= 0:
        return False

    now = time.time()
    cutoff = now - 60

    with _rate_lock:
        timestamps = [ts for ts in _rate_buckets.get(key, []) if ts > cutoff]
        if len(timestamps) >= limit_per_minute:
            _rate_buckets[key] = timestamps
            return True
        timestamps.append(now)
        _rate_buckets[key] = timestamps
        return False


@router.post("/gateway")
async def query_gateway(request: Request):
    request_id = _request_id(request)
    started = time.time()

    try:
        claims = _authenticate_request(request)
        settings = get_settings()

        client_ip = request.client.host if request.client else "unknown"
        subject = str(claims.get("sub", "unknown"))
        limiter_key = f"{subject}:{client_ip}"
        if _is_rate_limited(limiter_key, int(settings.SQL_GATEWAY_RATE_LIMIT_PER_MINUTE)):
            raise SQLGatewayError("SQLGW_RATE_LIMITED", "Rate limit exceeded", 429)

        raw_body = await request.body()
        if len(raw_body) > int(settings.SQL_GATEWAY_MAX_BODY_BYTES):
            raise SQLGatewayError(
                "SQLGW_COMPLEXITY_LIMIT_EXCEEDED",
                "Request body exceeds configured size limit",
                400,
            )

        if not raw_body:
            payload = {}
        else:
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except Exception as exc:
                raise SQLGatewayError(
                    "SQLGW_INVALID_OPERATOR_PAYLOAD",
                    "Request body must be valid JSON",
                    400,
                ) from exc

        request_model = parse_gateway_payload(payload)
        data = execute_gateway_request(request_model, actor_user_id=subject)

        duration_ms = int((time.time() - started) * 1000)
        logger.info(
            "SQLGW request_id=%s sub=%s op=%s table=%s duration_ms=%s status=success",
            request_id,
            subject,
            request_model.operation,
            request_model.table,
            duration_ms,
        )

        return _success_response(data, request_id)
    except SQLGatewayError as exc:
        logger.warning(
            "SQLGW request_id=%s status=error code=%s message=%s",
            request_id,
            exc.code,
            exc.message,
        )
        return _error_response(exc.code, exc.message, exc.status_code, request_id)
    except Exception as exc:
        logger.error("SQLGW request_id=%s unexpected_error=%s", request_id, str(exc), exc_info=True)
        return _error_response(
            "SQLGW_EXECUTION_FAILED",
            "An unexpected error occurred",
            500,
            request_id,
        )
