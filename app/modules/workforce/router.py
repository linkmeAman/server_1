"""Explicit workforce routes."""

from __future__ import annotations

import mimetypes
import os
import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_central_db_session, get_main_db_session
from app.core.prism_pdp import PDPRequest, evaluate
from app.core.response import success_response

from .dependencies import CallerContext, require_any_caller
from .positions_router import router as positions_router
from .services.workforce_service import WorkforceService

router = APIRouter(prefix="/api/workforce", tags=["workforce"])
router.include_router(positions_router)

service = WorkforceService()


def _today() -> str:
    return date.today().isoformat()


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}(:\d{2})?$")
_MOBILE_RE = re.compile(r"^\+?\d[\d\s\-]{5,14}\d$")


def _validate_date(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    if not _DATE_RE.match(value.strip()):
        raise HTTPException(status_code=422, detail=f"{field} must be YYYY-MM-DD")
    return value.strip()


class EmployeeCreateRequest(BaseModel):
    """Payload for creating a new employee (contact + employee row)."""
    # Contact
    fname: str = Field(..., min_length=1, max_length=100)
    mname: str | None = Field(default=None, max_length=100)
    lname: str | None = Field(default=None, max_length=100)
    mobile: str = Field(..., min_length=6, max_length=20)
    country_code: str | None = Field(default="+91", max_length=10)
    email: str | None = Field(default=None, max_length=255)
    personal_email: str | None = Field(default=None, max_length=255)
    gender: str | None = Field(default=None)
    dob: str | None = Field(default=None)
    address: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    pincode: str | None = Field(default=None, max_length=20)
    bid: int | None = Field(default=None)
    # Employee
    ecode: str | None = Field(default=None, max_length=50)
    department_id: int = Field(...)
    position_id: int = Field(...)
    doj: str = Field(...)
    doe: str | None = Field(default=None)
    workshift_id: int | None = Field(default=None)
    workshift_in_time: str | None = Field(default=None, max_length=10)
    workshift_out_time: str | None = Field(default=None, max_length=10)
    salary_type: int | None = Field(default=1)
    salary: float | None = Field(default=None, ge=0)
    allowance: float | None = Field(default=None, ge=0)
    employee_type: int | None = Field(default=0)
    status: int | None = Field(default=1)
    grade: int | None = Field(default=None)
    # Contact additions
    mobile2: str | None = Field(default=None, max_length=20)
    country_code_2: str | None = Field(default="+91", max_length=10)
    phone_no: str | None = Field(default=None, max_length=15)
    # Emergency contact
    ename: str | None = Field(default=None, max_length=100)
    emobile: str | None = Field(default=None, max_length=20)
    ecountry_code: str | None = Field(default="+91", max_length=10)
    relation: str | None = Field(default=None, max_length=50)
    # Official additions
    exit_date: str | None = Field(default=None)
    # Toggles (0 or 1)
    user_account: int | None = Field(default=0)
    is_admin: int | None = Field(default=0)
    calculate_salary: int | None = Field(default=0)
    is_parent: int | None = Field(default=0)
    demo_owner: int | None = Field(default=0)
    cash_collector: int | None = Field(default=0)
    auto_assign_inq: int | None = Field(default=0)
    qualifier: int | None = Field(default=0)
    # Parent positions
    parent_position_ids: list[int] | None = Field(default=None)
    # Financial
    tds_type: int | None = Field(default=0)
    tds_percent: float | None = Field(default=None, ge=0, le=100)
    rate_multiplier: float | None = Field(default=0.0, ge=0)
    incentive_new: float | None = Field(default=None, ge=0)
    incentive_renew: float | None = Field(default=None, ge=0)
    p_incentive_c: float | None = Field(default=None, ge=0)
    p_incentive_sc: float | None = Field(default=None, ge=0)
    trainer_incentive: float | None = Field(default=None, ge=0)
    mt_incentive: float | None = Field(default=None, ge=0)

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        cleaned = v.strip()
        if not _MOBILE_RE.match(cleaned):
            raise ValueError("mobile must be a valid phone number")
        return cleaned

    @field_validator("doj")
    @classmethod
    def validate_doj(cls, v: str) -> str:
        if not _DATE_RE.match(v.strip()):
            raise ValueError("doj must be YYYY-MM-DD")
        return v.strip()

    @field_validator("doe", "dob", "exit_date")
    @classmethod
    def validate_optional_dates(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _DATE_RE.match(v.strip()):
            raise ValueError("date must be YYYY-MM-DD")
        return v.strip()

    @field_validator("gender")
    @classmethod
    def validate_gender(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in {"M", "F", "O", "Male", "Female", "Other"}:
            raise ValueError("gender must be M, F, or O")
        # Normalise to single-char representation stored in DB
        _map = {"Male": "M", "Female": "F", "Other": "O"}
        return _map.get(v, v)

    @field_validator("employee_type")
    @classmethod
    def validate_employee_type(cls, v: int | None) -> int | None:
        if v is not None and v not in {0, 1}:
            raise ValueError("employee_type must be 0 (Regular) or 1 (Franchisee)")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: int | None) -> int | None:
        if v is not None and v not in {0, 1}:
            raise ValueError("status must be 0 (Inactive) or 1 (Active)")
        return v


class EmployeeUpdateRequest(BaseModel):
    """Payload for updating an existing employee (partial update)."""
    # Contact
    fname: str | None = Field(default=None, min_length=1, max_length=100)
    mname: str | None = Field(default=None, max_length=100)
    lname: str | None = Field(default=None, max_length=100)
    mobile: str | None = Field(default=None, min_length=6, max_length=20)
    country_code: str | None = Field(default=None, max_length=10)
    email: str | None = Field(default=None, max_length=255)
    personal_email: str | None = Field(default=None, max_length=255)
    gender: str | None = Field(default=None)
    dob: str | None = Field(default=None)
    address: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    pincode: str | None = Field(default=None, max_length=20)
    # Employee
    ecode: str | None = Field(default=None, max_length=50)
    department_id: int | None = Field(default=None)
    position_id: int | None = Field(default=None)
    doj: str | None = Field(default=None)
    doe: str | None = Field(default=None)
    workshift_id: int | None = Field(default=None)
    workshift_in_time: str | None = Field(default=None, max_length=10)
    workshift_out_time: str | None = Field(default=None, max_length=10)
    salary_type: int | None = Field(default=None)
    salary: float | None = Field(default=None, ge=0)
    allowance: float | None = Field(default=None, ge=0)
    employee_type: int | None = Field(default=None)
    status: int | None = Field(default=None)
    grade: int | None = Field(default=None)
    # Contact additions
    mobile2: str | None = Field(default=None, max_length=20)
    country_code_2: str | None = Field(default=None, max_length=10)
    phone_no: str | None = Field(default=None, max_length=15)
    # Emergency contact
    ename: str | None = Field(default=None, max_length=100)
    emobile: str | None = Field(default=None, max_length=20)
    ecountry_code: str | None = Field(default=None, max_length=10)
    relation: str | None = Field(default=None, max_length=50)
    # Official additions
    exit_date: str | None = Field(default=None)
    # Toggles (0 or 1)
    user_account: int | None = Field(default=None)
    is_admin: int | None = Field(default=None)
    calculate_salary: int | None = Field(default=None)
    is_parent: int | None = Field(default=None)
    demo_owner: int | None = Field(default=None)
    cash_collector: int | None = Field(default=None)
    auto_assign_inq: int | None = Field(default=None)
    qualifier: int | None = Field(default=None)
    # Parent positions
    parent_position_ids: list[int] | None = Field(default=None)
    # Financial
    tds_type: int | None = Field(default=None)
    tds_percent: float | None = Field(default=None, ge=0, le=100)
    rate_multiplier: float | None = Field(default=None, ge=0)
    incentive_new: float | None = Field(default=None, ge=0)
    incentive_renew: float | None = Field(default=None, ge=0)
    p_incentive_c: float | None = Field(default=None, ge=0)
    p_incentive_sc: float | None = Field(default=None, ge=0)
    trainer_incentive: float | None = Field(default=None, ge=0)
    mt_incentive: float | None = Field(default=None, ge=0)

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not _MOBILE_RE.match(cleaned):
            raise ValueError("mobile must be a valid phone number")
        return cleaned

    @field_validator("doj", "doe", "dob", "exit_date")
    @classmethod
    def validate_dates(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _DATE_RE.match(v.strip()):
            raise ValueError("date must be YYYY-MM-DD")
        return v.strip()

    @field_validator("gender")
    @classmethod
    def validate_gender(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in {"M", "F", "O", "Male", "Female", "Other"}:
            raise ValueError("gender must be M, F, or O")
        _map = {"Male": "M", "Female": "F", "Other": "O"}
        return _map.get(v, v)

    @field_validator("employee_type")
    @classmethod
    def validate_employee_type(cls, v: int | None) -> int | None:
        if v is not None and v not in {0, 1}:
            raise ValueError("employee_type must be 0 or 1")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: int | None) -> int | None:
        if v is not None and v not in {0, 1}:
            raise ValueError("status must be 0 or 1")
        return v


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


class AttendanceLeaveBucketUpdateRequest(BaseModel):
    contact_id: int | None = None
    emp_id: int | None = None
    emp_code: int | None = None
    doi: str | None = None
    doc: str | None = None
    doe: str | None = None
    earned: float | None = None
    category: int | None = None
    day_code: int | None = None
    consumed: int | None = None
    expired: int | None = None
    park: int | None = None


class AttendanceLeaveBucketCreateRequest(AttendanceLeaveBucketUpdateRequest):
    pass


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
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    # Super-administrators always have full read access.
    # For everyone else, verify an explicit employee:read grant in PRISM.
    if not caller.is_super:
        pdp_result = await evaluate(
            PDPRequest(user_id=caller.user_id, action="employee:read", resource_type="employee"),
            central_db,
        )
        if pdp_result.decision != "Allow":
            raise HTTPException(status_code=403, detail="PRISM: Not authorized to list employees")

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


@router.get("/employees/form-meta")
async def get_employee_form_meta(
    _: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    """Return dropdown options needed to render the employee create/edit form."""
    data = await service.get_employee_form_meta(main_db, central_db)
    return success_response(data=data, message="Employee form meta fetched").model_dump(mode="json")


# Filename validation — allow only safe chars (alphanumeric, hyphens, underscores, dots)
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


@router.get("/documents/contact/{filename}")
async def serve_contact_document(
    filename: str,
    _: CallerContext = Depends(require_any_caller),
):
    """Serve an uploaded contact/employee document image by filename.

    Files are read from ``CONTACT_DOCUMENT_PATH`` (configured via env var).
    Only filenames matching ``[A-Za-z0-9_.\\-]+`` are accepted to prevent
    any path-traversal attacks.
    """
    if not _SAFE_FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    from app.core.settings import get_settings  # local import to avoid circular deps

    settings = get_settings()
    base_dir = os.path.realpath(settings.CONTACT_DOCUMENT_PATH)
    target = os.path.realpath(os.path.join(base_dir, filename))

    # Guard against path traversal
    if not target.startswith(base_dir + os.sep) and target != base_dir:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="Document not found")

    media_type, _ = mimetypes.guess_type(filename)
    return FileResponse(target, media_type=media_type or "application/octet-stream")


@router.get("/employees/{employee_id}")
async def get_workforce_employee(
    employee_id: int,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    # Super-administrators always have full read access.
    if not caller.is_super:
        pdp_result = await evaluate(
            PDPRequest(
                user_id=caller.user_id,
                action="employee:read",
                resource_type="employee",
                resource_id=str(employee_id),
            ),
            central_db,
        )
        if pdp_result.decision != "Allow":
            raise HTTPException(status_code=403, detail="PRISM: Not authorized to view this employee")

    data = await service.get_employee(main_db, central_db, employee_id)
    return success_response(data=data, message="Employee fetched").model_dump(mode="json")


@router.post("/employees")
async def create_workforce_employee(
    body: EmployeeCreateRequest,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    """Create a new employee (contact + employee row). Requires employee:create permission."""
    if not caller.is_super:
        pdp_result = await evaluate(
            PDPRequest(user_id=caller.user_id, action="employee:create", resource_type="employee"),
            central_db,
        )
        if pdp_result.decision != "Allow":
            raise HTTPException(status_code=403, detail="PRISM: Not authorized to create employees")

    data = await service.create_employee(
        main_db, central_db, body.model_dump(exclude_unset=False), caller.user_id
    )
    return success_response(data=data, message="Employee created").model_dump(mode="json")


@router.put("/employees/{employee_id}")
async def update_workforce_employee(
    employee_id: int,
    body: EmployeeUpdateRequest,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    """Update an existing employee. Requires employee:update permission."""
    if not caller.is_super:
        pdp_result = await evaluate(
            PDPRequest(
                user_id=caller.user_id,
                action="employee:update",
                resource_type="employee",
                resource_id=str(employee_id),
            ),
            central_db,
        )
        if pdp_result.decision != "Allow":
            raise HTTPException(status_code=403, detail="PRISM: Not authorized to update employees")

    data = await service.update_employee(
        main_db, central_db, employee_id,
        body.model_dump(exclude_unset=True),
        caller.user_id,
    )
    return success_response(data=data, message="Employee updated").model_dump(mode="json")


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


@router.get("/attendance/leaves")
async def list_workforce_attendance_leaves(
    employee_id: int | None = Query(default=None),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    category: int | None = Query(default=None),
    expired: int | None = Query(default=None),
    park: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    data = await service.list_attendance_leaves(
        main_db,
        employee_id=employee_id,
        from_date=from_date,
        to_date=to_date,
        category=category,
        expired=expired,
        park=park,
        limit=limit,
        offset=offset,
    )
    return success_response(data=data, message="Attendance leaves fetched").model_dump(mode="json")


@router.patch("/attendance/leaves/{leave_id}")
async def update_workforce_attendance_leave(
    leave_id: int,
    body: AttendanceLeaveBucketUpdateRequest,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    data = await service.update_attendance_leave(
        main_db,
        leave_id=leave_id,
        payload=body.model_dump(exclude_unset=True),
        modified_by=caller.user_id,
    )
    return success_response(data=data, message="Attendance leave updated").model_dump(mode="json")


@router.post("/attendance/leaves")
async def create_workforce_attendance_leave(
    body: AttendanceLeaveBucketCreateRequest,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    data = await service.create_attendance_leave(
        main_db,
        payload=body.model_dump(exclude_unset=True),
        created_by=caller.user_id,
    )
    return success_response(data=data, message="Attendance leave created").model_dump(mode="json")


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
    paid_nonzero: bool = Query(default=False),
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
        paid_nonzero=paid_nonzero,
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


@router.delete("/payroll/records/{record_id}")
async def delete_workforce_payroll_record(
    record_id: int,
    _: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    await service.delete_payroll_record(
        main_db,
        record_id=record_id,
    )
    return success_response(data={"record_id": record_id}, message="Payroll record deleted").model_dump(mode="json")


@router.get("/payroll/salary-track")
async def get_workforce_salary_track(
    employee_id: int | None = Query(default=None),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    data = await service.salary_track(
        main_db,
        central_db,
        employee_id=employee_id,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )
    return success_response(data=data, message="Salary track fetched").model_dump(mode="json")


@router.get("/payroll/salary-excel")
async def get_workforce_salary_excel(
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    search: str | None = Query(default=None),
    dept: str | None = Query(default=None),
    paid_status: str | None = Query(default=None, pattern="^(paid|unpaid|all)?$"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
):
    from_date = _validate_date(from_date, "from_date")
    to_date = _validate_date(to_date, "to_date")
    data = await service.salary_excel(
        main_db,
        from_date=from_date,
        to_date=to_date,
        search=search,
        dept=dept if dept else None,
        paid_status=paid_status if paid_status and paid_status != "all" else None,
        limit=limit,
        offset=offset,
    )
    return success_response(data=data, message="Salary excel fetched").model_dump(mode="json")
