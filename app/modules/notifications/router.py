"""Notification API routes."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.prism_guard import CallerContext, require_any_caller
from app.core.response import success_response
from app.modules.notifications.schemas.models import NotificationPublishRequest
from app.modules.notifications.services.publisher import (
    heartbeat_stream,
    notification_broker,
    publish_notification,
)

router = APIRouter(prefix="/api/notifications/v1", tags=["notifications-v1"])
logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID") or str(uuid4())


async def _logged_stream(
    stream: AsyncIterator[bytes],
    *,
    user_id: int | str,
    request_id: str,
) -> AsyncIterator[bytes]:
    logger.info("NOTIFICATION_STREAM_OPEN user_id=%s request_id=%s", user_id, request_id)
    try:
        async for chunk in stream:
            yield chunk
    finally:
        logger.info("NOTIFICATION_STREAM_CLOSED user_id=%s request_id=%s", user_id, request_id)


@router.get("/stream")
async def stream_notifications(
    request: Request,
    caller: CallerContext = Depends(require_any_caller),
):
    request_id = _request_id(request)
    last_event_id = request.headers.get("Last-Event-ID")
    events = notification_broker.subscribe(
        user_id=caller.user_id,
        last_event_id=last_event_id,
    )
    return StreamingResponse(
        _logged_stream(
            heartbeat_stream(events),
            user_id=caller.user_id,
            request_id=request_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Request-ID": request_id,
        },
    )


@router.get("/recent")
async def recent_notifications(
    request: Request,
    severity: str | None = None,
    limit: int = 100,
    caller: CallerContext = Depends(require_any_caller),
):
    events = await notification_broker.recent(
        user_id=caller.user_id,
        min_severity=severity,
        limit=limit,
    )
    payload = success_response(
        data={"results": [event.model_dump(mode="json") for event in events], "total": len(events)},
        message="Notifications retrieved",
    ).model_dump(mode="json")
    response = JSONResponse(status_code=200, content=payload)
    response.headers["X-Request-ID"] = _request_id(request)
    return response


@router.post("/debug")
async def publish_debug_notification(
    payload: NotificationPublishRequest,
    request: Request,
    caller: CallerContext = Depends(require_any_caller),
):
    event = await publish_notification(
        request_id=payload.request_id or _request_id(request),
        event_type=payload.event_type,
        severity=payload.severity,
        source=payload.source,
        message=payload.message,
        metadata=payload.metadata,
        user_id=caller.user_id,
        group_key=payload.group_key,
        dedupe_key=payload.dedupe_key,
    )
    response = JSONResponse(
        status_code=200,
        content=success_response(
            data=event.model_dump(mode="json"),
            message="Notification published",
        ).model_dump(mode="json"),
    )
    response.headers["X-Request-ID"] = event.request_id
    return response
