"""Shared helpers for auth v2 handlers/services."""

from __future__ import annotations

import asyncio
import hashlib
import random
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse

from core.response import error_response, success_response
from core.settings import get_settings

from app.modules.auth.constants import (
    HEADER_RATE_LIMIT_LIMIT,
    HEADER_RATE_LIMIT_REMAINING,
    HEADER_RATE_LIMIT_RESET,
    HEADER_REQUEST_ID,
    HEADER_RETRY_AFTER,
)


class AuthError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def request_id(request: Request) -> str:
    return request.headers.get(HEADER_REQUEST_ID) or str(uuid4())


def random_jti() -> str:
    return secrets.token_urlsafe(24)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def refresh_token_hash(token: str) -> str:
    settings = get_settings()
    pepper = settings.AUTH_V2_REFRESH_HASH_PEPPER
    return sha256_hex(f"{pepper}{token}")


def normalize_mobile(country_code: str, mobile: str) -> str:
    return f"{country_code.strip()}:{mobile.strip()}"


def client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def user_agent(request: Request) -> str:
    return request.headers.get("User-Agent", "")


async def apply_timing_floor(started_at: datetime) -> None:
    settings = get_settings()
    floor_ms = int(settings.AUTH_V2_TIMING_FLOOR_MS)
    jitter_min = int(settings.AUTH_V2_TIMING_JITTER_MIN_MS)
    jitter_max = int(settings.AUTH_V2_TIMING_JITTER_MAX_MS)
    jitter = random.randint(jitter_min, jitter_max) if jitter_max >= jitter_min else jitter_min
    elapsed_ms = (utcnow() - started_at).total_seconds() * 1000.0
    wait_ms = max(0, floor_ms + jitter - int(elapsed_ms))
    if wait_ms > 0:
        await asyncio.sleep(wait_ms / 1000.0)


def error_json_response(
    code: str,
    message: str,
    status_code: int,
    request_id_value: str,
    details: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    payload = error_response(
        error=code,
        message=message,
        data={"request_id": request_id_value, "details": details or {}},
    ).model_dump(mode="json")
    response = JSONResponse(content=payload, status_code=status_code)
    response.headers[HEADER_REQUEST_ID] = request_id_value
    return response


def success_json_response(
    data: Dict[str, Any],
    request_id_value: Optional[str] = None,
    status_code: int = 200,
    message: str = "Success",
) -> JSONResponse:
    payload = success_response(data=data, message=message).model_dump(mode="json")
    response = JSONResponse(content=payload, status_code=status_code)
    if request_id_value:
        response.headers[HEADER_REQUEST_ID] = request_id_value
    return response


def attach_rate_limit_headers(
    response: JSONResponse,
    *,
    limit: int,
    remaining: int,
    reset_epoch_seconds: int,
    retry_after_seconds: int,
) -> None:
    response.headers[HEADER_RATE_LIMIT_LIMIT] = str(limit)
    response.headers[HEADER_RATE_LIMIT_REMAINING] = str(max(0, remaining))
    response.headers[HEADER_RATE_LIMIT_RESET] = str(reset_epoch_seconds)
    response.headers[HEADER_RETRY_AFTER] = str(max(0, retry_after_seconds))

