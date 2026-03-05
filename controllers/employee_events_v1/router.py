"""Explicit Employee Events V1 routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from core.response import error_response, success_response

from .dependencies import EmployeeEventsError, require_app_access_claims
from .schemas.models import (
    ApproveEmployeeEventRequest,
    CheckConflictRequest,
    CreateEmployeeEventRequest,
    ParkEmployeeEventRequest,
    UpdateEmployeeEventRequest,
)
from .services.event_service import EmployeeEventsService

router = APIRouter(prefix="/api/employee-events/v1", tags=["employee-events-v1"])

employee_events_service = EmployeeEventsService()


def _error_response(exc: EmployeeEventsError) -> JSONResponse:
    payload = error_response(
        error=exc.code,
        message=exc.message,
        data=exc.data,
    ).model_dump(mode="json")
    return JSONResponse(status_code=exc.status_code, content=payload)


def _actor_user_id(claims: dict) -> str:
    return str(claims.get("sub") or "unknown")


@router.get("/employees/realtime-data")
async def get_realtime_employee_data(request: Request):
    try:
        require_app_access_claims(request.headers.get("Authorization"))
        data = employee_events_service.get_realtime_employee_data()
        return success_response(
            data=data,
            message="Realtime employee and branch data fetched successfully",
        ).model_dump(mode="json")
    except EmployeeEventsError as exc:
        return _error_response(exc)


@router.get("/events")
async def list_events(
    request: Request,
    from_date: Optional[str] = Query(default=None),
    to_date: Optional[str] = Query(default=None),
    contact_id: Optional[int] = Query(default=None, ge=1),
    status: Optional[int] = Query(default=None),
    park: Optional[int] = Query(default=None),
    include_parked: bool = Query(default=True),
):
    try:
        require_app_access_claims(request.headers.get("Authorization"))
        data = employee_events_service.list_events(
            from_date=from_date,
            to_date=to_date,
            contact_id=contact_id,
            status=status,
            park=park,
            include_parked=include_parked,
        )
        return success_response(
            data=data,
            message="Employee events fetched successfully",
        ).model_dump(mode="json")
    except EmployeeEventsError as exc:
        return _error_response(exc)


@router.post("/events/check-conflict")
async def check_conflict(payload: CheckConflictRequest, request: Request):
    try:
        require_app_access_claims(request.headers.get("Authorization"))
        data = employee_events_service.check_conflict(
            date=payload.date,
            start_time=payload.start_time,
            end_time=payload.end_time,
            contact_id=payload.contact_id,
            exclude_event_id=payload.exclude_event_id,
        )
        return success_response(
            data=data,
            message="Conflict check completed",
        ).model_dump(mode="json")
    except EmployeeEventsError as exc:
        return _error_response(exc)


@router.post("/events")
async def create_event(payload: CreateEmployeeEventRequest, request: Request):
    try:
        claims = require_app_access_claims(request.headers.get("Authorization"))
        data = employee_events_service.create_event(
            payload=payload.model_dump(mode="python"),
            actor_user_id=_actor_user_id(claims),
        )
        return success_response(
            data=data,
            message="Employee event created successfully",
        ).model_dump(mode="json")
    except EmployeeEventsError as exc:
        return _error_response(exc)


@router.put("/events/{event_id}")
async def update_event(event_id: int, payload: UpdateEmployeeEventRequest, request: Request):
    try:
        claims = require_app_access_claims(request.headers.get("Authorization"))
        data = await employee_events_service.update_event(
            event_id=event_id,
            payload=payload.model_dump(mode="python"),
            actor_user_id=_actor_user_id(claims),
        )
        return success_response(
            data=data,
            message="Employee event updated successfully",
        ).model_dump(mode="json")
    except EmployeeEventsError as exc:
        return _error_response(exc)


@router.patch("/events/{event_id}/park")
async def park_event(event_id: int, payload: ParkEmployeeEventRequest, request: Request):
    try:
        require_app_access_claims(request.headers.get("Authorization"))
        data = await employee_events_service.park_event(
            event_id=event_id,
            park_value=payload.park_value,
        )
        return success_response(
            data=data,
            message="Employee event park status updated",
        ).model_dump(mode="json")
    except EmployeeEventsError as exc:
        return _error_response(exc)


@router.post("/events/{event_id}/approve")
async def approve_event(event_id: int, payload: ApproveEmployeeEventRequest, request: Request):
    try:
        require_app_access_claims(request.headers.get("Authorization"))
        data = await employee_events_service.approve_event(
            event_id=event_id,
            requested_status=payload.status,
        )
        return success_response(
            data=data,
            message="Employee event approval processed",
        ).model_dump(mode="json")
    except EmployeeEventsError as exc:
        return _error_response(exc)
