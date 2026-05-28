"""Notification API routes."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.prism_guard import CallerContext, require_any_caller
from app.core.response import success_response
from app.modules.notifications.schemas.models import (
    NotificationPreferencePatch,
    NotificationPublishRequest,
    NotificationRulePatch,
)
from app.modules.notifications.services.publisher import (
    heartbeat_stream,
    notification_broker,
    publish_notification,
)
from app.modules.notifications.services.repository import (
    clear_all_notifications,
    clear_notification,
    get_notification_preferences,
    list_notification_rules,
    list_recent_notifications,
    mark_all_notifications_read,
    mark_notification_read,
    save_notification_event,
    update_notification_rule,
    update_notification_preferences,
)

router = APIRouter(prefix="/api/notifications/v1", tags=["notifications-v1"])
logger = logging.getLogger(__name__)
notification_broker.register_persistence_hook(save_notification_event)


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


@router.get("/preferences")
async def notification_preferences(
    request: Request,
    caller: CallerContext = Depends(require_any_caller),
):
    preferences = await get_notification_preferences(user_id=caller.user_id)
    response = JSONResponse(
        status_code=200,
        content=success_response(
            data=preferences.model_dump(mode="json"),
            message="Notification preferences retrieved",
        ).model_dump(mode="json"),
    )
    response.headers["X-Request-ID"] = _request_id(request)
    return response


@router.patch("/preferences")
async def patch_notification_preferences(
    payload: NotificationPreferencePatch,
    request: Request,
    caller: CallerContext = Depends(require_any_caller),
):
    preferences = await update_notification_preferences(
        user_id=caller.user_id,
        patch=payload,
    )
    response = JSONResponse(
        status_code=200,
        content=success_response(
            data=preferences.model_dump(mode="json"),
            message="Notification preferences updated",
        ).model_dump(mode="json"),
    )
    response.headers["X-Request-ID"] = _request_id(request)
    return response


@router.get("/rules")
async def notification_rules(
    request: Request,
    caller: CallerContext = Depends(require_any_caller),
):
    rules = await list_notification_rules(user_id=caller.user_id)
    response = JSONResponse(
        status_code=200,
        content=success_response(
            data={"results": [rule.model_dump(mode="json") for rule in rules]},
            message="Notification rules retrieved",
        ).model_dump(mode="json"),
    )
    response.headers["X-Request-ID"] = _request_id(request)
    return response


@router.patch("/rules")
async def patch_notification_rules(
    payload: NotificationRulePatch,
    request: Request,
    caller: CallerContext = Depends(require_any_caller),
):
    rule = await update_notification_rule(user_id=caller.user_id, patch=payload)
    response = JSONResponse(
        status_code=200,
        content=success_response(
            data=rule.model_dump(mode="json"),
            message="Notification rule updated",
        ).model_dump(mode="json"),
    )
    response.headers["X-Request-ID"] = _request_id(request)
    return response


@router.get("/recent")
async def recent_notifications(
    request: Request,
    severity: str | None = None,
    limit: int = 100,
    caller: CallerContext = Depends(require_any_caller),
):
    try:
        events = await list_recent_notifications(
            user_id=caller.user_id,
            min_severity=severity,
            limit=limit,
        )
    except (RuntimeError, SQLAlchemyError):
        logger.exception(
            "Notification DB recent lookup failed; falling back to memory user_id=%s",
            caller.user_id,
        )
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


@router.patch("/{event_id}/read")
async def read_notification(
    event_id: str,
    request: Request,
    caller: CallerContext = Depends(require_any_caller),
):
    found = await mark_notification_read(user_id=caller.user_id, event_id=event_id)
    if not found:
        raise HTTPException(status_code=404, detail="Notification not found")

    response = JSONResponse(
        status_code=200,
        content=success_response(
            data={"event_id": event_id, "read": True},
            message="Notification marked as read",
        ).model_dump(mode="json"),
    )
    response.headers["X-Request-ID"] = _request_id(request)
    return response


@router.patch("/read-all")
async def read_all_notifications(
    request: Request,
    caller: CallerContext = Depends(require_any_caller),
):
    count = await mark_all_notifications_read(user_id=caller.user_id)
    response = JSONResponse(
        status_code=200,
        content=success_response(
            data={"updated": count},
            message="Notifications marked as read",
        ).model_dump(mode="json"),
    )
    response.headers["X-Request-ID"] = _request_id(request)
    return response


@router.delete("/clear-all")
async def clear_all_visible_notifications(
    request: Request,
    caller: CallerContext = Depends(require_any_caller),
):
    count = await clear_all_notifications(user_id=caller.user_id)
    response = JSONResponse(
        status_code=200,
        content=success_response(
            data={"updated": count},
            message="Notifications cleared",
        ).model_dump(mode="json"),
    )
    response.headers["X-Request-ID"] = _request_id(request)
    return response


@router.delete("/{event_id}")
async def clear_visible_notification(
    event_id: str,
    request: Request,
    caller: CallerContext = Depends(require_any_caller),
):
    found = await clear_notification(user_id=caller.user_id, event_id=event_id)
    if not found:
        raise HTTPException(status_code=404, detail="Notification not found")

    response = JSONResponse(
        status_code=200,
        content=success_response(
            data={"event_id": event_id, "cleared": True},
            message="Notification cleared",
        ).model_dump(mode="json"),
    )
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
