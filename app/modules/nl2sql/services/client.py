"""HTTP client for the external NL2SQL service."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.settings import get_settings
from app.modules.nl2sql.schemas.models import (
    ASK_RESPONSE_ADAPTER,
    GENERATE_SQL_RESPONSE_ADAPTER,
    Nl2SqlRequest,
)

logger = logging.getLogger(__name__)


class Nl2SqlClientError(HTTPException):
    """Stable HTTP exception used to normalize upstream failures."""

    def __init__(
        self,
        status_code: int,
        *,
        error_code: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.error_code = error_code
        self.response_data = data


class Nl2SqlClient:
    """Small wrapper around the external NL2SQL service."""

    async def ask(
        self,
        *,
        request_data: Nl2SqlRequest,
        actor_user_id: int | str,
        request_id: str,
        route_path: str,
    ) -> dict[str, Any]:
        return await self._post(
            upstream_path="/ask",
            request_data=request_data,
            actor_user_id=actor_user_id,
            request_id=request_id,
            route_path=route_path,
            response_adapter=ASK_RESPONSE_ADAPTER,
        )

    async def generate_sql(
        self,
        *,
        request_data: Nl2SqlRequest,
        actor_user_id: int | str,
        request_id: str,
        route_path: str,
    ) -> dict[str, Any]:
        return await self._post(
            upstream_path="/generate-sql",
            request_data=request_data,
            actor_user_id=actor_user_id,
            request_id=request_id,
            route_path=route_path,
            response_adapter=GENERATE_SQL_RESPONSE_ADAPTER,
        )

    async def _post(
        self,
        *,
        upstream_path: str,
        request_data: Nl2SqlRequest,
        actor_user_id: int | str,
        request_id: str,
        route_path: str,
        response_adapter,
    ) -> dict[str, Any]:
        settings = get_settings()
        base_url = str(getattr(settings, "NL2SQL_SERVICE_BASE_URL", "") or "").strip().rstrip("/")
        timeout_seconds = float(getattr(settings, "NL2SQL_TIMEOUT_SECONDS", 30))
        default_top_k = int(getattr(settings, "NL2SQL_DEFAULT_TOP_K", 5))

        if not base_url:
            raise Nl2SqlClientError(
                503,
                error_code="NL2SQL_NOT_CONFIGURED",
                message="NL2SQL service base URL is not configured",
                data={"request_id": request_id},
            )

        upstream_url = f"{base_url}{upstream_path}"
        upstream_payload = {
            "query": request_data.query,
            "top_k": request_data.top_k if request_data.top_k not in (None, 0) else default_top_k,
            "request_id": request_id,
        }

        started = time.perf_counter()

        logger.info(
            "NL2SQL → REQUEST  | request_id=%s user_id=%s route=%s upstream_url=%s "
            "query=%r query_len=%d top_k=%d timeout_s=%.0f",
            request_id,
            actor_user_id,
            route_path,
            upstream_url,
            request_data.query,
            len(request_data.query),
            upstream_payload["top_k"],
            timeout_seconds,
        )

        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    upstream_url,
                    json=upstream_payload,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "X-Request-ID": request_id,
                    },
                )
        except httpx.TimeoutException as exc:
            duration_ms = _duration_ms(started)
            logger.warning(
                "NL2SQL → TIMEOUT  | request_id=%s user_id=%s route=%s upstream_url=%s "
                "duration_ms=%d timeout_s=%.0f error=%s",
                request_id,
                actor_user_id,
                route_path,
                upstream_url,
                duration_ms,
                timeout_seconds,
                str(exc),
            )
            raise Nl2SqlClientError(
                502,
                error_code="NL2SQL_UPSTREAM_TIMEOUT",
                message=f"NL2SQL upstream timed out while calling {upstream_path}",
                data={"request_id": request_id, "upstream_path": upstream_path},
            ) from exc
        except httpx.RequestError as exc:
            duration_ms = _duration_ms(started)
            logger.warning(
                "NL2SQL → UNAVAILABLE | request_id=%s user_id=%s route=%s upstream_url=%s "
                "duration_ms=%d error_type=%s error=%s",
                request_id,
                actor_user_id,
                route_path,
                upstream_url,
                duration_ms,
                type(exc).__name__,
                str(exc),
            )
            raise Nl2SqlClientError(
                502,
                error_code="NL2SQL_UPSTREAM_UNAVAILABLE",
                message=f"Could not reach NL2SQL upstream while calling {upstream_path}",
                data={"request_id": request_id, "upstream_path": upstream_path},
            ) from exc

        duration_ms = _duration_ms(started)

        logger.info(
            "NL2SQL → HTTP     | request_id=%s user_id=%s route=%s upstream_url=%s "
            "http_status=%d duration_ms=%d content_length=%s",
            request_id,
            actor_user_id,
            route_path,
            upstream_url,
            response.status_code,
            duration_ms,
            response.headers.get("content-length", "unknown"),
        )

        if response.status_code != 200:
            message, details = _extract_error_message(response)
            raw_body = response.text[:2000] if response.text else ""
            logger.warning(
                "NL2SQL → ERROR    | request_id=%s user_id=%s route=%s upstream_url=%s "
                "http_status=%d duration_ms=%d message=%r body_preview=%r",
                request_id,
                actor_user_id,
                route_path,
                upstream_url,
                response.status_code,
                duration_ms,
                message,
                raw_body,
            )
            raise Nl2SqlClientError(
                response.status_code,
                error_code="NL2SQL_UPSTREAM_ERROR",
                message=message,
                data={
                    "request_id": request_id,
                    "upstream_path": upstream_path,
                    "upstream_status": response.status_code,
                    "details": details,
                },
            )

        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning(
                "NL2SQL → INVALID_JSON | request_id=%s user_id=%s route=%s upstream_url=%s "
                "http_status=%d duration_ms=%d body_preview=%r error=%s",
                request_id,
                actor_user_id,
                route_path,
                upstream_url,
                response.status_code,
                duration_ms,
                response.text[:500],
                str(exc),
            )
            raise Nl2SqlClientError(
                502,
                error_code="NL2SQL_INVALID_RESPONSE",
                message="NL2SQL upstream returned invalid JSON",
                data={"request_id": request_id, "upstream_path": upstream_path},
            ) from exc

        try:
            validated = response_adapter.validate_python(payload)
        except ValidationError as exc:
            logger.warning(
                "NL2SQL → INVALID_SCHEMA | request_id=%s user_id=%s route=%s upstream_url=%s "
                "http_status=%d duration_ms=%d validation_errors=%s",
                request_id,
                actor_user_id,
                route_path,
                upstream_url,
                response.status_code,
                duration_ms,
                exc.errors(),
            )
            raise Nl2SqlClientError(
                502,
                error_code="NL2SQL_INVALID_RESPONSE",
                message="NL2SQL upstream returned an unexpected response shape",
                data={
                    "request_id": request_id,
                    "upstream_path": upstream_path,
                    "errors": exc.errors(),
                },
            ) from exc

        normalized = validated.model_dump(mode="json")
        warning_codes = _warning_codes(normalized)

        logger.info(
            "NL2SQL → SUCCESS  | request_id=%s user_id=%s route=%s upstream_url=%s "
            "duration_ms=%d status=%s warnings=%s attempt_count=%s "
            "row_count=%s tables=%s sql_preview=%r",
            request_id,
            actor_user_id,
            route_path,
            upstream_url,
            duration_ms,
            normalized.get("status"),
            warning_codes,
            normalized.get("attempt_count"),
            normalized.get("row_count"),
            normalized.get("tables_used"),
            (normalized.get("sql") or "")[:120],
        )
        return normalized

def _duration_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _warning_codes(payload: dict[str, Any]) -> list[str]:
    warning_codes: list[str] = []
    for warning in payload.get("warnings", []):
        if isinstance(warning, dict):
            code = str(warning.get("code") or "").strip()
            if code:
                warning_codes.append(code)
    return warning_codes


def _extract_error_message(response: httpx.Response) -> tuple[str, dict[str, Any]]:
    default_message = f"NL2SQL upstream request failed ({response.status_code})"

    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return (text or default_message, {})

    if isinstance(payload, dict):
        message = (
            str(payload.get("message") or "").strip()
            or str(payload.get("detail") or "").strip()
            or str(payload.get("error") or "").strip()
            or default_message
        )
        details = {
            key: value
            for key, value in payload.items()
            if key in {"detail", "error", "message", "status", "warnings"}
        }
        return message, details

    return default_message, {}

