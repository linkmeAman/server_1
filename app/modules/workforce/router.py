"""Explicit workforce routes."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_central_db_session, get_main_db_session
from app.core.response import success_response

from .dependencies import CallerContext, require_any_caller
from .services.workforce_service import WorkforceService

router = APIRouter(prefix="/api/workforce", tags=["workforce"])

service = WorkforceService()


def _today() -> str:
    return date.today().isoformat()


class AttendanceRecordUpdateRequest(BaseModel):
    contact_id: int | None = None
    date: str | None = None
    ip_address: str | None = None
    logout_ip_address: str | None = None
    mac_address: str | None = None
    login_bssid: str | None = None
    logout_bssid: str | None = None
    login_wifi_details: str | None = None
    logout_wifi_details: str | None = None
    login_details: str | None = None
    logout_details: str | None = None
    in_time: str | None = None
    out_time: str | None = None
    comment: str | None = None
    status: int | None = None
    regularised: int | None = None
    regularised_type_id: int | None = None
    invalid: int | None = None
    park: int | None = None


class AttendanceRecordCreateRequest(BaseModel):
    contact_id: int | None = None
    date: str | None = None
    ip_address: str | None = None
    logout_ip_address: str | None = None
    mac_address: str | None = None
    login_bssid: str | None = None
    logout_bssid: str | None = None
    login_wifi_details: str | None = None
    logout_wifi_details: str | None = None
    login_details: str | None = None
    logout_details: str | None = None
    in_time: str | None = None
    out_time: str | None = None
    comment: str | None = None
    status: int | None = None
    regularised: int | None = None
    regularised_type_id: int | None = None
    invalid: int | None = None
    park: int | None = None


class AttendanceRequestUpdateRequest(BaseModel):
    emp_id: int | None = None
    parent_id: int | None = None
    date: str | None = None
    action_date: str | None = None
    request_type: int | None = None
    no_of_days: str | None = None
    start_date: str | None = None
    in_time: str | None = None
    end_date: str | None = None
    out_time: str | None = None
    status: int | None = None
    request_comment: str | None = None
    parent_comment: str | None = None
    bid: int | None = None
    park: int | None = None


class AttendanceRequestCreateRequest(BaseModel):
    emp_id: int | None = None
    parent_id: int | None = None
    date: str | None = None
    action_date: str | None = None
    request_type: int | None = None
    no_of_days: str | None = None
    start_date: str | None = None
    in_time: str | None = None
    end_date: str | None = None
    out_time: str | None = None
    status: int | None = None
    request_comment: str | None = None
    parent_comment: str | None = None
    bid: int | None = None
    park: int | None = None


class AttendanceRequestBulkStatusRequest(BaseModel):
    request_ids: list[int]
    status: int


class PayrollRecordUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    contact_id: int | None = None
    from_date: str | None = None
    to_date: str | None = None
    working_days: int | None = None
    present_days: int | None = None
    absent_days: int | None = None
    wo_days: int | None = None
    par_day: int | None = None
    total_leaves: float | None = None
    paid_leave: float | None = None
    unpaid_leave: float | None = None
    leave_balance_before: float | None = None
    leave_balance_after: float | None = None
    tax_amount: int | None = None
    tax_comment: str | None = None
    advance_amount: int | None = None
    advance_comment: str | None = None
    fine_amount: int | None = None
    fine_comment: str | None = None
    base_amount: int | None = None
    incentive: int | None = None
    incentive_comment: str | None = None
    deduction: int | None = None
    deduction_comment: str | None = None
    addition: int | None = None
    addition_comment: str | None = None
    extra_amount: int | None = None
    extra_comment: str | None = None
    allowance: int | None = None
    pay_mode: int | None = None
    sub_total: int | None = None
    salary: int | None = None
    paid: int | None = None
    bid: int | None = None
    comments: str | None = None
    paid_holidays: int | None = None
    paid_holidays_dates: str | None = None
    leaves: int | None = None
    leaves_dates: str | None = None
    wfh: int | None = None
    wfh_dates: str | None = None
    half_day: int | None = None
    half_day_dates: str | None = None
    optional_holiday: int | None = None
    optional_holiday_dates: str | None = None
    punch_inout: int | None = None
    punch_inout_dates: str | None = None
    break_: int | None = Field(default=None, alias="break")
    break_dates: str | None = None
    supplementary: int | None = None
    supplementary_dates: str | None = None
    park: int | None = None
    response_data: dict | list | None = None
    response_data_app: dict | list | None = None
    pay_slip_data: dict | list | None = None
    incentive_html: str | None = None
    year_month: str | None = None
    created_at: str | None = None
    created_by: int | None = None
    modified_at: str | None = None
    modified_by: int | None = None


class PayrollRecordCreateRequest(PayrollRecordUpdateRequest):
    pass


@router.get("/meta")
async def get_workforce_meta(
    _: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    data = await service.get_meta(main_db, central_db)
    return success_response(data=data, message="Workforce meta fetched").model_dump(mode="json")


@router.get("/employees")
async def list_workforce_employees(
    q: str | None = Query(default=None),
    status: int | None = Query(default=None),
    department_id: int | None = Query(default=None),
    position_id: int | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    data = await service.list_employees(
        main_db,
        central_db,
        q=q,
        status=status,
        department_id=department_id,
        position_id=position_id,
        limit=limit,
        offset=offset,
    )
    return success_response(data=data, message="Employees fetched").model_dump(mode="json")


@router.get("/employees/{employee_id}")
async def get_workforce_employee(
    employee_id: int,
    _: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    data = await service.get_employee(main_db, central_db, employee_id)
    return success_response(data=data, message="Employee fetched").model_dump(mode="json")


@router.get("/employees/{employee_id}/attendance-summary")
async def get_workforce_employee_attendance_summary(
    employee_id: int,
    from_date: str = Query(default_factory=_today),
    to_date: str = Query(default_factory=_today),
    _: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    data = await service.get_employee_attendance_summary(
        main_db,
        central_db,
        employee_id=employee_id,
        from_date=from_date,
        to_date=to_date,
    )
    return success_response(data=data, message="Employee attendance summary fetched").model_dump(mode="json")


@router.get("/attendance/overview")
async def get_workforce_attendance_overview(
    from_date: str = Query(default_factory=_today),
    to_date: str = Query(default_factory=_today),
    department_id: int | None = Query(default=None),
    limit: int = Query(default=12, ge=1, le=100),
    _: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    data = await service.get_attendance_overview(
        main_db,
        central_db,
        from_date=from_date,
        to_date=to_date,
        department_id=department_id,
        limit=limit,
    )
    return success_response(data=data, message="Attendance overview fetched").model_dump(mode="json")


@router.get("/attendance/employees")
async def list_workforce_attendance_employees(
    _: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    data = await service.list_attendance_employee_index(main_db, central_db)
    return success_response(data=data, message="Attendance employee index fetched").model_dump(mode="json")


@router.get("/attendance/bssid-options")
async def list_workforce_attendance_bssid_options(
    _: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    data = await service.list_attendance_bssid_options(main_db)
    return success_response(data=data, message="Attendance BSSID options fetched").model_dump(mode="json")


@router.get("/attendance/records")
async def list_workforce_attendance_records(
    employee_id: int | None = Query(default=None),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    status: int | None = Query(default=None),
    regularised: int | None = Query(default=None),
    invalid: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    data = await service.list_attendance_records(
        main_db,
        employee_id=employee_id,
        from_date=from_date,
        to_date=to_date,
        status=status,
        regularised=regularised,
        invalid=invalid,
        limit=limit,
        offset=offset,
    )
    return success_response(data=data, message="Attendance records fetched").model_dump(mode="json")


@router.patch("/attendance/records/{record_id}")
async def update_workforce_attendance_record(
    record_id: int,
    body: AttendanceRecordUpdateRequest,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    data = await service.update_attendance_record(
        main_db,
        record_id=record_id,
        payload=body.model_dump(exclude_unset=True),
        modified_by=caller.user_id,
    )
    return success_response(data=data, message="Attendance record updated").model_dump(mode="json")


@router.post("/attendance/records")
async def create_workforce_attendance_record(
    body: AttendanceRecordCreateRequest,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    data = await service.create_attendance_record(
        main_db,
        payload=body.model_dump(exclude_unset=True),
        created_by=caller.user_id,
    )
    return success_response(data=data, message="Attendance record created").model_dump(mode="json")


@router.get("/attendance/requests")
async def list_workforce_attendance_requests(
    employee_id: int | None = Query(default=None),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    status: int | None = Query(default=None),
    request_type: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    data = await service.list_attendance_requests(
        main_db,
        employee_id=employee_id,
        from_date=from_date,
        to_date=to_date,
        status=status,
        request_type=request_type,
        limit=limit,
        offset=offset,
    )
    return success_response(data=data, message="Attendance requests fetched").model_dump(mode="json")


@router.patch("/attendance/requests/status")
async def bulk_update_workforce_attendance_request_status(
    body: AttendanceRequestBulkStatusRequest,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    data = await service.bulk_update_attendance_request_status(
        main_db,
        request_ids=body.request_ids,
        status=body.status,
        modified_by=caller.user_id,
    )
    return success_response(data=data, message="Attendance request statuses updated").model_dump(mode="json")


@router.patch("/attendance/requests/{request_id}")
async def update_workforce_attendance_request(
    request_id: int,
    body: AttendanceRequestUpdateRequest,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    data = await service.update_attendance_request(
        main_db,
        request_id=request_id,
        payload=body.model_dump(exclude_unset=True),
        modified_by=caller.user_id,
    )
    return success_response(data=data, message="Attendance request updated").model_dump(mode="json")


@router.post("/attendance/requests")
async def create_workforce_attendance_request(
    body: AttendanceRequestCreateRequest,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    data = await service.create_attendance_request(
        main_db,
        payload=body.model_dump(exclude_unset=True),
        created_by=caller.user_id,
    )
    return success_response(data=data, message="Attendance request created").model_dump(mode="json")


@router.get("/payroll/overview")
async def get_workforce_payroll_overview(
    from_date: str = Query(default_factory=_today),
    to_date: str = Query(default_factory=_today),
    employee_id: int | None = Query(default=None),
    limit: int = Query(default=12, ge=1, le=100),
    _: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    data = await service.get_payroll_overview(
        main_db,
        central_db,
        from_date=from_date,
        to_date=to_date,
        employee_id=employee_id,
        limit=limit,
    )
    return success_response(data=data, message="Payroll overview fetched").model_dump(mode="json")


@router.get("/payroll/records")
async def list_workforce_payroll_records(
    employee_id: int | None = Query(default=None),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    paid: int | None = Query(default=None),
    park: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    data = await service.list_payroll_records(
        main_db,
        employee_id=employee_id,
        from_date=from_date,
        to_date=to_date,
        paid=paid,
        park=park,
        limit=limit,
        offset=offset,
    )
    return success_response(data=data, message="Payroll records fetched").model_dump(mode="json")


@router.patch("/payroll/records/{record_id}")
async def update_workforce_payroll_record(
    record_id: int,
    body: PayrollRecordUpdateRequest,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    payload = body.model_dump(exclude_unset=True)
    if "break_" in payload:
        payload["break"] = payload.pop("break_")
    data = await service.update_payroll_record(
        main_db,
        record_id=record_id,
        payload=payload,
        modified_by=caller.user_id,
    )
    return success_response(data=data, message="Payroll record updated").model_dump(mode="json")


@router.post("/payroll/records")
async def create_workforce_payroll_record(
    body: PayrollRecordCreateRequest,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    payload = body.model_dump(exclude_unset=True)
    if "break_" in payload:
        payload["break"] = payload.pop("break_")
    data = await service.create_payroll_record(
        main_db,
        payload=payload,
        created_by=caller.user_id,
    )
    return success_response(data=data, message="Payroll record created").model_dump(mode="json")
