"""Explicit Google Calendar V1 routes."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.core.response import error_response, success_response

from .dependencies import GoogleCalendarError, require_app_access_claims
from .schemas.models import CreateCalendarEventRequest, DeleteMode, UpdateCalendarEventRequest
from .services.event_service import GoogleCalendarEventService

router = APIRouter(prefix="/api/google-calendar/v1", tags=["google-calendar-v1"])

# Stateless service singleton for route handlers.
event_service = GoogleCalendarEventService()


def _error_response(exc: GoogleCalendarError) -> JSONResponse:
    payload = error_response(
        error=exc.code,
        message=exc.message,
        data=exc.data,
    ).model_dump(mode="json")
    return JSONResponse(status_code=exc.status_code, content=payload)


@router.post("/events")
async def create_calendar_event(payload: CreateCalendarEventRequest, request: Request):
    try:
        require_app_access_claims(request.headers.get("Authorization"))

        data = await event_service.create_event(
            event=payload.event,
            actor_name=payload.actor_name,
            actor_email=payload.actor_email,
        )
        return success_response(
            data=data,
            message="Calendar event created successfully",
        ).model_dump(mode="json")
    except GoogleCalendarError as exc:
        return _error_response(exc)


@router.put("/events/{event_id}")
async def update_calendar_event(
    event_id: str,
    payload: UpdateCalendarEventRequest,
    request: Request,
):
    try:
        require_app_access_claims(request.headers.get("Authorization"))

        data = await event_service.update_event(
            event_id=event_id,
            event=payload.event,
            actor_name=payload.actor_name,
            log_row_id=payload.log_row_id,
        )
        return success_response(
            data=data,
            message="Calendar event updated successfully",
        ).model_dump(mode="json")
    except GoogleCalendarError as exc:
        return _error_response(exc)


@router.delete("/events/{event_id}")
async def delete_calendar_event(
    event_id: str,
    request: Request,
    delete_mode: DeleteMode = Query(default=DeleteMode.full),
):
    try:
        require_app_access_claims(request.headers.get("Authorization"))

        data = await event_service.delete_event(
            event_id=event_id,
            delete_mode=delete_mode.value,
        )
        return success_response(
            data=data,
            message="Calendar event deleted successfully",
        ).model_dump(mode="json")
    except GoogleCalendarError as exc:
        return _error_response(exc)
