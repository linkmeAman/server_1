"""Explicit workforce routes."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_central_db_session, get_main_db_session
from app.core.response import success_response

from .dependencies import CallerContext, require_any_caller
from .services.workforce_service import WorkforceService

router = APIRouter(prefix="/api/workforce", tags=["workforce"])

service = WorkforceService()


def _today() -> str:
    return date.today().isoformat()


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
