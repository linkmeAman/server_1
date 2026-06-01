"""App-facing wrapper routes for the external NL2SQL service."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from app.core.prism_guard import CallerContext
from app.core.response import error_response, success_response
from app.modules.nl2sql.dependencies import require_nl2sql_access
from app.modules.nl2sql.schemas.models import (
    Nl2SqlBenchmarkCaseCreateRequest,
    Nl2SqlBenchmarkCasesQuery,
    Nl2SqlConfirmTeachRequest,
    Nl2SqlGovernanceValidateRequest,
    Nl2SqlHealthLlmQuery,
    Nl2SqlIngestGroupsRequest,
    Nl2SqlIngestKnowledgeRequest,
    Nl2SqlInstructionsQuery,
    Nl2SqlPatternFeedbackRequest,
    Nl2SqlRequest,
    Nl2SqlTeachRequest,
    Nl2SqlTeachPendingQuery,
    Nl2SqlTelemetryRecentQuery,
    Nl2SqlTelemetrySummaryQuery,
)
from app.modules.nl2sql.services.client import Nl2SqlClient
from app.modules.notifications.services.publisher import publish_notification

router = APIRouter(prefix="/api/nl2sql/v1", tags=["nl2sql-v1"])

nl2sql_client = Nl2SqlClient()

_NL2SQL_EVENT_MESSAGES = {
    "SCHEMA_RETRIEVAL_STARTED": "Schema retrieval started",
    "SCHEMA_RETRIEVAL_SUCCESS": "Schema retrieval completed",
    "SQL_GENERATION_STARTED": "SQL generation started",
    "SQL_GENERATION_FAILED": "SQL generation failed",
    "QUERY_TIMEOUT": "Query execution timed out",
    "QUERY_COMPLETED": "Query completed",
}


def _resolve_request_id(request: Request, payload: object | None = None) -> str:
    payload_request_id = getattr(payload, "request_id", None) if payload is not None else None
    if payload_request_id:
        return str(payload_request_id)

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


def _severity_for_nl2sql_event(event_type: str, status: str | None = None) -> str:
    normalized_status = (status or "").lower()
    if event_type == "QUERY_TIMEOUT":
        return "error"
    if event_type.endswith("_FAILED") or normalized_status in {"error", "failed"}:
        return "error"
    if event_type.endswith("_SUCCESS") or event_type == "QUERY_COMPLETED":
        return "success"
    if normalized_status in {"warning", "warn"}:
        return "warning"
    return "info"


def _event_type_from_trace(trace_event: dict) -> str | None:
    stage = str(trace_event.get("stage") or "").upper()
    status = str(trace_event.get("status") or "").upper()
    message = str(trace_event.get("message") or "").lower()

    if "TIMEOUT" in stage or "timed out" in message or "timeout" in message:
        return "QUERY_TIMEOUT"
    if "SCHEMA" in stage and status in {"STARTED", "START", "RUNNING"}:
        return "SCHEMA_RETRIEVAL_STARTED"
    if "SCHEMA" in stage and status in {"OK", "SUCCESS", "COMPLETED"}:
        return "SCHEMA_RETRIEVAL_SUCCESS"
    if "SQL" in stage and status in {"STARTED", "START", "RUNNING"}:
        return "SQL_GENERATION_STARTED"
    if "SQL" in stage and status in {"FAILED", "ERROR"}:
        return "SQL_GENERATION_FAILED"
    if stage in {"QUERY", "QUERY_EXECUTION", "EXECUTION"} and status in {"OK", "SUCCESS", "COMPLETED"}:
        return "QUERY_COMPLETED"
    if status in {"FAILED", "ERROR"}:
        return f"{stage or 'NL2SQL'}_FAILED"
    return None


async def _publish_nl2sql_event(
    *,
    request_id: str,
    event_type: str,
    user_id: int | str,
    message: str | None = None,
    metadata: dict | None = None,
    status: str | None = None,
) -> None:
    await publish_notification(
        request_id=request_id,
        event_type=event_type,
        severity=_severity_for_nl2sql_event(event_type, status),
        source="nl2sql",
        message=message or _NL2SQL_EVENT_MESSAGES.get(event_type, event_type.replace("_", " ").title()),
        metadata=metadata or {},
        user_id=user_id,
        group_key=f"nl2sql:{request_id}",
        dedupe_key=f"nl2sql:{request_id}:{event_type}",
    )


async def _notification_stream_wrapper(
    stream: AsyncIterator[bytes],
    *,
    request_id: str,
    user_id: int | str,
) -> AsyncIterator[bytes]:
    buffer = ""
    try:
        async for chunk in stream:
            decoded = chunk.decode("utf-8", errors="replace")
            buffer += decoded
            lines = buffer.splitlines(keepends=True)
            buffer = ""
            if lines and not lines[-1].endswith(("\n", "\r")):
                buffer = lines.pop()

            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                except ValueError:
                    continue
                if event.get("event") == "trace":
                    event_type = _event_type_from_trace(event)
                    if event_type:
                        await _publish_nl2sql_event(
                            request_id=request_id,
                            event_type=event_type,
                            user_id=user_id,
                            message=str(event.get("message") or ""),
                            metadata={"trace": event},
                            status=str(event.get("status") or ""),
                        )
                elif event.get("event") == "final":
                    await _publish_nl2sql_event(
                        request_id=request_id,
                        event_type="QUERY_COMPLETED",
                        user_id=user_id,
                        metadata={"stream_event": event.get("event")},
                    )
            yield chunk
    except Exception as exc:
        await _publish_nl2sql_event(
            request_id=request_id,
            event_type="SQL_GENERATION_FAILED",
            user_id=user_id,
            message="NL2SQL stream failed",
            metadata={"error": str(exc), "error_type": type(exc).__name__},
            status="failed",
        )
        raise


async def _parse_request_body(request: Request, model_cls) -> tuple[object | None, JSONResponse | None]:
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
        payload = model_cls.model_validate(body)
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


def _parse_query_params(request: Request, model_cls) -> tuple[object | None, JSONResponse | None]:
    request_id = _resolve_request_id(request)
    try:
        payload = model_cls.model_validate(dict(request.query_params))
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
    payload, error = await _parse_request_body(request, Nl2SqlRequest)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request, payload)
    await _publish_nl2sql_event(
        request_id=request_id,
        event_type="SQL_GENERATION_STARTED",
        user_id=caller.user_id,
        metadata={"query": payload.query, "endpoint": "ask"},
    )
    try:
        result = await nl2sql_client.ask(
            request_data=payload,
            actor_user_id=caller.user_id,
            request_id=request_id,
            route_path=request.url.path,
        )
    except Exception as exc:
        await _publish_nl2sql_event(
            request_id=request_id,
            event_type="SQL_GENERATION_FAILED",
            user_id=caller.user_id,
            message="NL2SQL ask failed",
            metadata={"error": str(exc), "error_type": type(exc).__name__},
            status="failed",
        )
        raise
    await _publish_nl2sql_event(
        request_id=request_id,
        event_type="QUERY_COMPLETED",
        user_id=caller.user_id,
        metadata={
            "status": result.get("status"),
            "row_count": result.get("row_count"),
            "tables_used": result.get("tables_used", []),
            "endpoint": "ask",
        },
    )
    return _success_response(result, "NL2SQL ask completed", request_id)


@router.post("/ask/stream")
async def ask_stream(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = await _parse_request_body(request, Nl2SqlRequest)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request, payload)
    await _publish_nl2sql_event(
        request_id=request_id,
        event_type="SQL_GENERATION_STARTED",
        user_id=caller.user_id,
        metadata={"query": payload.query, "endpoint": "ask_stream"},
    )
    stream = nl2sql_client.ask_stream(
        request_data=payload,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    notification_stream = _notification_stream_wrapper(
        stream,
        request_id=request_id,
        user_id=caller.user_id,
    )
    return StreamingResponse(
        notification_stream,
        media_type="application/x-ndjson",
        headers={
            "X-Request-ID": request_id,
            "Cache-Control": "no-store",
        },
    )


@router.post("/generate-sql")
async def generate_sql(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = await _parse_request_body(request, Nl2SqlRequest)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request, payload)
    await _publish_nl2sql_event(
        request_id=request_id,
        event_type="SQL_GENERATION_STARTED",
        user_id=caller.user_id,
        metadata={"query": payload.query, "endpoint": "generate_sql"},
    )
    try:
        result = await nl2sql_client.generate_sql(
            request_data=payload,
            actor_user_id=caller.user_id,
            request_id=request_id,
            route_path=request.url.path,
        )
    except Exception as exc:
        await _publish_nl2sql_event(
            request_id=request_id,
            event_type="SQL_GENERATION_FAILED",
            user_id=caller.user_id,
            message="NL2SQL SQL preview failed",
            metadata={"error": str(exc), "error_type": type(exc).__name__},
            status="failed",
        )
        raise
    await _publish_nl2sql_event(
        request_id=request_id,
        event_type="QUERY_COMPLETED",
        user_id=caller.user_id,
        metadata={
            "status": result.get("status"),
            "tables_used": result.get("tables_used", []),
            "endpoint": "generate_sql",
        },
    )
    return _success_response(result, "NL2SQL SQL preview completed", request_id)


@router.post("/teach")
async def teach(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = await _parse_request_body(request, Nl2SqlTeachRequest)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request, payload)
    result = await nl2sql_client.teach(
        request_data=payload,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL teach completed", request_id)


@router.post("/teach/confirm")
async def teach_confirm(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = await _parse_request_body(request, Nl2SqlConfirmTeachRequest)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request, payload)
    result = await nl2sql_client.teach_confirm(
        request_data=payload,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL teach confirmation completed", request_id)


@router.get("/health")
async def health(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    request_id = _resolve_request_id(request)
    result = await nl2sql_client.health(
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL health retrieved", request_id)


@router.get("/health/config")
async def health_config(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    request_id = _resolve_request_id(request)
    result = await nl2sql_client.health_config(
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL config health retrieved", request_id)


@router.get("/health/runtime")
async def health_runtime(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    request_id = _resolve_request_id(request)
    result = await nl2sql_client.health_runtime(
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL runtime health retrieved", request_id)


@router.get("/health/llm")
async def health_llm(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = _parse_query_params(request, Nl2SqlHealthLlmQuery)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request)
    result = await nl2sql_client.health_llm(
        role=payload.role,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL LLM health retrieved", request_id)


@router.get("/health/vector")
async def health_vector(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    request_id = _resolve_request_id(request)
    result = await nl2sql_client.health_vector(
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL vector health retrieved", request_id)


@router.get("/metrics/llm")
async def metrics_llm(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    request_id = _resolve_request_id(request)
    result = await nl2sql_client.metrics_llm(
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL LLM metrics retrieved", request_id)


@router.get("/metrics/teach")
async def metrics_teach(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    request_id = _resolve_request_id(request)
    result = await nl2sql_client.metrics_teach(
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL teach metrics retrieved", request_id)


@router.get("/instructions")
async def list_instructions(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = _parse_query_params(request, Nl2SqlInstructionsQuery)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request)
    result = await nl2sql_client.list_instructions(
        instruction_type=payload.instruction_type,
        active_only=payload.active_only,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL instructions retrieved", request_id)


@router.delete("/instructions/{instruction_id}")
async def delete_instruction(
    instruction_id: int,
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    request_id = _resolve_request_id(request)
    result = await nl2sql_client.delete_instruction(
        instruction_id=instruction_id,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL instruction deactivated", request_id)


@router.post("/ingest/groups")
async def ingest_groups(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = await _parse_request_body(request, Nl2SqlIngestGroupsRequest)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request)
    result = await nl2sql_client.ingest_groups(
        request_data=payload,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL group ingest completed", request_id)


@router.get("/ingest/groups/status")
async def ingest_groups_status(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    request_id = _resolve_request_id(request)
    result = await nl2sql_client.ingest_groups_status(
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL group ingest status retrieved", request_id)


@router.post("/ingest/knowledge")
async def ingest_knowledge(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = await _parse_request_body(request, Nl2SqlIngestKnowledgeRequest)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request)
    result = await nl2sql_client.ingest_knowledge(
        request_data=payload,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL knowledge ingest completed", request_id)


@router.post("/ingest/patterns")
async def ingest_patterns(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    request_id = _resolve_request_id(request)
    result = await nl2sql_client.ingest_patterns(
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL pattern ingest completed", request_id)


@router.post("/ingest/instructions")
async def ingest_instructions(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    request_id = _resolve_request_id(request)
    result = await nl2sql_client.ingest_instructions(
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL instruction ingest completed", request_id)


@router.post("/query")
async def query(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = await _parse_request_body(request, Nl2SqlRequest)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request, payload)
    result = await nl2sql_client.query(
        request_data=payload,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL retrieval completed", request_id)


@router.post("/query/groups")
async def query_groups(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = await _parse_request_body(request, Nl2SqlRequest)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request, payload)
    result = await nl2sql_client.query_groups(
        request_data=payload,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL grouped retrieval completed", request_id)


@router.get("/telemetry/recent")
async def telemetry_recent(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = _parse_query_params(request, Nl2SqlTelemetryRecentQuery)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request)
    result = await nl2sql_client.telemetry_recent(
        limit=payload.limit,
        endpoint=payload.endpoint,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL recent telemetry retrieved", request_id)


@router.get("/telemetry/summary")
async def telemetry_summary(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = _parse_query_params(request, Nl2SqlTelemetrySummaryQuery)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request)
    result = await nl2sql_client.telemetry_summary(
        endpoint=payload.endpoint,
        since_minutes=payload.since_minutes,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL telemetry summary retrieved", request_id)


@router.get("/failures")
async def list_failures(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    request_id = _resolve_request_id(request)
    limit = int(request.query_params.get("limit", 50))
    endpoint = request.query_params.get("endpoint")
    result = await nl2sql_client.list_failures(
        limit=limit,
        endpoint=endpoint,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response({"results": result, "total": len(result)}, "NL2SQL failure log retrieved", request_id)


@router.get("/teach/pending")
async def list_pending_teach_confirmations(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = _parse_query_params(request, Nl2SqlTeachPendingQuery)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request)
    result = await nl2sql_client.list_pending_teach_confirmations(
        limit=payload.limit,
        include_expired=payload.include_expired,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL pending teach confirmations retrieved", request_id)


@router.post("/teach/pending/cleanup")
async def cleanup_pending_teach_confirmations(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    request_id = _resolve_request_id(request)
    result = await nl2sql_client.cleanup_pending_teach_confirmations(
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL pending teach confirmations cleaned", request_id)


@router.get("/cache/stats")
async def cache_stats(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    request_id = _resolve_request_id(request)
    result = await nl2sql_client.cache_stats(
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL cache stats retrieved", request_id)


@router.post("/cache/clear")
async def cache_clear(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    request_id = _resolve_request_id(request)
    result = await nl2sql_client.cache_clear(
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL caches cleared", request_id)


@router.get("/governance/rules")
async def governance_rules(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    request_id = _resolve_request_id(request)
    result = await nl2sql_client.governance_rules(
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL governance rules retrieved", request_id)


@router.post("/governance/validate")
async def governance_validate(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = await _parse_request_body(request, Nl2SqlGovernanceValidateRequest)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request)
    result = await nl2sql_client.governance_validate(
        request_data=payload,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL governance validation completed", request_id)


@router.post("/benchmark/cases")
async def benchmark_add_case(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = await _parse_request_body(request, Nl2SqlBenchmarkCaseCreateRequest)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request)
    result = await nl2sql_client.benchmark_add_case(
        request_data=payload,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL benchmark case stored", request_id)


@router.get("/benchmark/cases")
async def benchmark_list_cases(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = _parse_query_params(request, Nl2SqlBenchmarkCasesQuery)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request)
    result = await nl2sql_client.benchmark_list_cases(
        limit=payload.limit,
        active_only=payload.active_only,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL benchmark cases retrieved", request_id)


@router.post("/patterns/feedback")
async def pattern_feedback(
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    payload, error = await _parse_request_body(request, Nl2SqlPatternFeedbackRequest)
    if error is not None or payload is None:
        return error

    request_id = _resolve_request_id(request)
    result = await nl2sql_client.pattern_feedback(
        request_data=payload,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(result, "NL2SQL pattern feedback applied", request_id)


@router.get("/telemetry/trace/{trace_request_id}")
async def list_trace_events(
    trace_request_id: str,
    request: Request,
    caller: CallerContext = Depends(require_nl2sql_access),
):
    request_id = _resolve_request_id(request)
    limit = int(request.query_params.get("limit", 500))
    result = await nl2sql_client.list_trace_events(
        trace_request_id=trace_request_id,
        limit=limit,
        actor_user_id=caller.user_id,
        request_id=request_id,
        route_path=request.url.path,
    )
    return _success_response(
        {"request_id": trace_request_id, "results": result, "total": len(result)},
        "NL2SQL trace events retrieved",
        request_id,
    )
