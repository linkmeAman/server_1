"""Explicit Employee Events V1 routes."""

from __future__ import annotations

from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from core.response import error_response, success_response

from .dependencies import EmployeeEventsError, require_app_access_claims
from .schemas.models import (
    ApproveEmployeeEventRequest,
    CheckConflictRequest,
    CreateEmployeeEventRequest,
    DemoEventsBatchQueryRequest,
    EmployeeLeaveCalendarBatchQueryRequest,
    EmployeeWorkshiftCalendarBatchQueryRequest,
    ParkEmployeeEventRequest,
    UpdateEmployeeEventRequest,
)
from .services.event_service import EmployeeEventsService

router = APIRouter(prefix="/api/employee-events/v1", tags=["employee-events-v1"])

employee_events_service = EmployeeEventsService()


def _error_response(exc: EmployeeEventsError, request: Request) -> JSONResponse:
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    payload = error_response(
        error=exc.code,
        message=exc.message,
        data={
            "request_id": request_id,
            "details": exc.data or {},
        },
    ).model_dump(mode="json")
    response = JSONResponse(status_code=exc.status_code, content=payload)
    response.headers["X-Request-ID"] = request_id
    return response


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
        return _error_response(exc, request)


@router.get("/calendar/events")
async def get_trainer_calendar_events(
    request: Request,
    contact_id: int = Query(...),
    from_date: Optional[str] = Query(default=None),
    to_date: Optional[str] = Query(default=None),
):
    try:
        require_app_access_claims(request.headers.get("Authorization"))
        data = employee_events_service.get_trainer_calendar_events(
            contact_id=contact_id,
            from_date=from_date,
            to_date=to_date,
        )
        return success_response(
            data=data,
            message="Calendar events fetched successfully",
        ).model_dump(mode="json")
    except EmployeeEventsError as exc:
        return _error_response(exc, request)


@router.get("/teacher/{contact_id}/availability")
async def get_teacher_daily_availability(
    request: Request,
    contact_id: int = Path(..., description="Teacher's contact ID"),
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
):
    """
    Get teacher's daily availability including shift times, busy blocks, and free slots.
    
    Returns workshift configuration, all busy events (batches + employee events + leave),
    and computed free time slots within the shift.
    """
    try:
        require_app_access_claims(request.headers.get("Authorization"))
        data = employee_events_service.get_teacher_daily_availability(
            contact_id=contact_id,
            date_str=date,
        )
        return success_response(
            data=data,
            message="Teacher daily availability retrieved successfully",
        ).model_dump(mode="json")
    except EmployeeEventsError as exc:
        return _error_response(exc, request)


@router.post("/employees/workshift-calendar/query")
async def post_employee_workshift_calendar_query(request: Request):
    try:
        require_app_access_claims(request.headers.get("Authorization"))
        try:
            body = await request.json()
        except Exception as exc:
            raise EmployeeEventsError(
                code="EMP_EVENT_INVALID_WORKSHIFT_QUERY",
                message="Request body must be valid JSON",
                status_code=400,
                data={"reason": "invalid_json"},
            ) from exc

        if not isinstance(body, dict):
            raise EmployeeEventsError(
                code="EMP_EVENT_INVALID_WORKSHIFT_QUERY",
                message="Request body must be a JSON object",
                status_code=400,
                data={"reason": "invalid_body_type"},
            )

        try:
            payload = EmployeeWorkshiftCalendarBatchQueryRequest.model_validate(body)
        except ValidationError as exc:
            raise EmployeeEventsError(
                code="EMP_EVENT_INVALID_WORKSHIFT_QUERY",
                message="Invalid workshift calendar query",
                status_code=400,
                data={"errors": exc.errors()},
            ) from exc

        data = employee_events_service.get_employee_workshift_calendar_batch(
            employee_ids=payload.employee_ids,
            from_date=payload.from_date,
            to_date=payload.to_date,
        )
        return success_response(
            data=data,
            message="Employee workshift calendar fetched successfully",
        ).model_dump(mode="json")
    except EmployeeEventsError as exc:
        return _error_response(exc, request)


@router.post("/employees/leave-calendar/query")
async def post_employee_leave_calendar_query(request: Request):
    try:
        require_app_access_claims(request.headers.get("Authorization"))
        try:
            body = await request.json()
        except Exception as exc:
            raise EmployeeEventsError(
                code="EMP_EVENT_INVALID_LEAVE_QUERY",
                message="Request body must be valid JSON",
                status_code=400,
                data={"reason": "invalid_json"},
            ) from exc

        if not isinstance(body, dict):
            raise EmployeeEventsError(
                code="EMP_EVENT_INVALID_LEAVE_QUERY",
                message="Request body must be a JSON object",
                status_code=400,
                data={"reason": "invalid_body_type"},
            )

        try:
            payload = EmployeeLeaveCalendarBatchQueryRequest.model_validate(body)
        except ValidationError as exc:
            raise EmployeeEventsError(
                code="EMP_EVENT_INVALID_LEAVE_QUERY",
                message="Invalid leave calendar query",
                status_code=400,
                data={"errors": exc.errors()},
            ) from exc

        try:
            data = employee_events_service.get_employee_leave_calendar_batch(
                employee_ids=payload.employee_ids,
                from_date=payload.from_date,
                to_date=payload.to_date,
                statuses=payload.statuses,
                request_types=payload.request_types,
                department_ids=payload.department_ids,
            )
        except EmployeeEventsError as exc:
            if exc.status_code == 400 and exc.code != "EMP_EVENT_INVALID_LEAVE_QUERY":
                raise EmployeeEventsError(
                    code="EMP_EVENT_INVALID_LEAVE_QUERY",
                    message=exc.message,
                    status_code=400,
                    data=exc.data,
                ) from exc
            raise

        return success_response(
            data=data,
            message="Employee leave calendar fetched successfully",
        ).model_dump(mode="json")
    except EmployeeEventsError as exc:
        return _error_response(exc, request)


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
        return _error_response(exc, request)


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
        return _error_response(exc, request)


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
        return _error_response(exc, request)


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
        return _error_response(exc, request)


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
        return _error_response(exc, request)


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
        return _error_response(exc, request)


@router.post("/demo/query")
async def post_demo_events_query(request: Request):
    try:
        require_app_access_claims(request.headers.get("Authorization"))
        try:
            body = await request.json()
        except Exception as exc:
            raise EmployeeEventsError(
                code="EMP_EVENT_INVALID_DEMO_QUERY",
                message="Request body must be valid JSON",
                status_code=400,
                data={"reason": "invalid_json"},
            ) from exc

        if not isinstance(body, dict):
            raise EmployeeEventsError(
                code="EMP_EVENT_INVALID_DEMO_QUERY",
                message="Request body must be a JSON object",
                status_code=400,
                data={"reason": "invalid_body_type"},
            )

        try:
            payload = DemoEventsBatchQueryRequest.model_validate(body)
        except ValidationError as exc:
            raise EmployeeEventsError(
                code="EMP_EVENT_INVALID_DEMO_QUERY",
                message="Invalid demo events query",
                status_code=400,
                data={"errors": exc.errors()},
            ) from exc

        try:
            data = employee_events_service.get_demo_events_batch(
                employee_ids=payload.employee_ids,
                from_date=payload.from_date,
                to_date=payload.to_date,
                statuses=payload.statuses,
                types=payload.types,
                venue_ids=payload.venue_ids,
                batch_ids=payload.batch_ids,
            )
        except EmployeeEventsError as exc:
            if exc.status_code == 400 and exc.code != "EMP_EVENT_INVALID_DEMO_QUERY":
                raise EmployeeEventsError(
                    code="EMP_EVENT_INVALID_DEMO_QUERY",
                    message=exc.message,
                    status_code=400,
                    data=exc.data,
                ) from exc
            raise

        return success_response(
            data=data,
            message="Demo events fetched successfully",
        ).model_dump(mode="json")
    except EmployeeEventsError as exc:
        return _error_response(exc, request)
