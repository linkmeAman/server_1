"""Workforce service."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.employee_events_v1.services.event_repository import EmployeeEventsRepository

from ..schemas.models import FUTURE_SCOPE_ATTENDANCE, FUTURE_SCOPE_EMPLOYEE
from .workforce_repository import WorkforceRepository


class WorkforceService:
    STATUS_OPTIONS = [
        {"value": 1, "label": "Active"},
        {"value": 0, "label": "Inactive"},
    ]

    def __init__(self) -> None:
        self.repo = WorkforceRepository()
        self.employee_events_repo = EmployeeEventsRepository()

    async def get_meta(self, db: AsyncSession) -> dict[str, Any]:
        departments = await self.repo.list_departments(db)
        positions = await self.repo.list_positions(db)
        return {
            "filter_options": {
                "departments": departments,
                "positions": positions,
                "statuses": self.STATUS_OPTIONS,
            },
            "future_scope": sorted(set(FUTURE_SCOPE_EMPLOYEE + FUTURE_SCOPE_ATTENDANCE)),
        }

    async def list_employees(
        self,
        db: AsyncSession,
        *,
        q: str | None,
        status: int | None,
        department_id: int | None,
        position_id: int | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        rows = await self.repo.list_employees(
            db,
            q=q,
            status=status,
            department_id=department_id,
            position_id=position_id,
            limit=limit,
            offset=offset,
        )
        total = await self.repo.count_employees(
            db,
            q=q,
            status=status,
            department_id=department_id,
            position_id=position_id,
        )
        employees = [self._serialize_employee_row(row) for row in rows]
        return {
            "employees": employees,
            "total": total,
            "limit": limit,
            "offset": offset,
            "summary": {
                "total": total,
                "active_count": sum(1 for item in employees if item["status"] == 1),
                "inactive_count": sum(1 for item in employees if item["status"] != 1),
                "attendance_ready_count": sum(1 for item in employees if item["attendance_ready"]),
                "user_account_count": sum(1 for item in employees if self._truthy_int(item["user_account"])),
            },
            "filter_options": {
                "departments": await self.repo.list_departments(db),
                "positions": await self.repo.list_positions(db),
                "statuses": self.STATUS_OPTIONS,
            },
            "future_scope": FUTURE_SCOPE_EMPLOYEE,
        }

    async def get_employee(self, db: AsyncSession, employee_id: int) -> dict[str, Any]:
        row = await self.repo.get_employee(db, employee_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Employee not found")

        return {
            "employee": self._serialize_employee_row(row),
            "future_scope": FUTURE_SCOPE_EMPLOYEE,
        }

    async def get_employee_attendance_summary(
        self,
        db: AsyncSession,
        *,
        employee_id: int,
        from_date: str,
        to_date: str,
    ) -> dict[str, Any]:
        row = await self.repo.get_employee(db, employee_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Employee not found")

        employee = self._serialize_employee_row(row)
        scheduled_events = await self.repo.list_scheduled_events(
            db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
        )
        leave_requests = await self.repo.list_leave_requests(
            db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
        )
        approved_leave_count = sum(1 for item in leave_requests if self._as_int(item.get("status")) == 1)

        return {
            "employee": employee,
            "from_date": from_date,
            "to_date": to_date,
            "attendance_ready": employee["attendance_ready"],
            "workshift": {
                "workshift_id": employee["workshift_id"],
                "workshift_hours": employee["workshift_hours"],
                "workshift_in_time": employee["workshift_in_time"],
                "workshift_out_time": employee["workshift_out_time"],
                "week_off_code": employee["week_off_code"],
            },
            "metrics": {
                "scheduled_event_count": len(scheduled_events),
                "approved_leave_count": approved_leave_count,
            },
            "scheduled_events": [
                {
                    "id": self._as_int(item.get("id")),
                    "category": self._as_text(item.get("category")),
                    "description": self._as_text(item.get("description")),
                    "date": self._as_date_text(item.get("date")),
                    "start_time": self._as_time_text(item.get("start_time")),
                    "end_time": self._as_time_text(item.get("end_time")),
                    "status": self._as_int(item.get("status")),
                }
                for item in scheduled_events
            ],
            "leave_requests": [
                {
                    "leave_request_id": item.get("leave_request_id"),
                    "start_date": self._as_date_text(item.get("start_date")),
                    "end_date": self._as_date_text(item.get("end_date")),
                    "status": self._as_int(item.get("status")),
                    "request_type": self._as_int(item.get("request_type")),
                }
                for item in leave_requests
            ],
            "future_scope": FUTURE_SCOPE_ATTENDANCE,
        }

    async def get_attendance_overview(
        self,
        db: AsyncSession,
        *,
        from_date: str,
        to_date: str,
        department_id: int | None,
        limit: int,
    ) -> dict[str, Any]:
        rows = await self.repo.list_employees(
            db,
            status=1,
            department_id=department_id,
            position_id=None,
            q=None,
            limit=limit,
            offset=0,
        )
        employees = [self._serialize_employee_row(row) for row in rows]

        approved_leave_count = 0
        employees_on_leave: set[int] = set()
        for employee in employees:
            leave_requests = await self.repo.list_leave_requests(
                db,
                employee_id=int(employee["employee_id"]),
                from_date=from_date,
                to_date=to_date,
            )
            approved = [item for item in leave_requests if self._as_int(item.get("status")) == 1]
            approved_leave_count += len(approved)
            if approved:
                employees_on_leave.add(int(employee["employee_id"]))

        return {
            "from_date": from_date,
            "to_date": to_date,
            "department_id": department_id,
            "summary": {
                "active_employee_count": len(employees),
                "attendance_ready_count": sum(1 for item in employees if item["attendance_ready"]),
                "scheduled_event_count": await self.repo.count_scheduled_events(
                    db,
                    from_date=from_date,
                    to_date=to_date,
                    department_id=department_id,
                ),
                "approved_leave_count": approved_leave_count,
                "employees_on_leave_count": len(employees_on_leave),
            },
            "employees": employees,
            "filter_options": {
                "departments": await self.repo.list_departments(db),
            },
            "future_scope": FUTURE_SCOPE_ATTENDANCE,
        }

    def _serialize_employee_row(self, row: dict[str, Any]) -> dict[str, Any]:
        workshift_id = self._as_int(row.get("workshift_id"))
        workshift_in_time = self._as_time_text(row.get("workshift_in_time"))
        workshift_out_time = self._as_time_text(row.get("workshift_out_time"))
        attendance_ready = bool(
            (workshift_id is not None and workshift_id > 0)
            or (workshift_in_time and workshift_out_time)
        )

        return {
            "employee_id": self._as_int(row.get("employee_id")),
            "contact_id": self._as_int(row.get("contact_id")),
            "ecode": row.get("ecode"),
            "department_id": self._as_int(row.get("department_id")),
            "department": self._as_text(row.get("department")),
            "position_id": self._as_int(row.get("position_id")),
            "position": self._as_text(row.get("position")),
            "status": self._as_int(row.get("status")),
            "user_account": self._as_int(row.get("user_account")),
            "is_admin": self._as_int(row.get("is_admin")),
            "employee_type": self._as_int(row.get("employee_type")),
            "doj": self._as_date_text(row.get("doj")),
            "doe": self._as_date_text(row.get("doe")),
            "exit_date": self._as_date_text(row.get("exit_date")),
            "workshift_id": workshift_id,
            "workshift_hours": self._as_text(row.get("workshift_hours")),
            "workshift_in_time": workshift_in_time,
            "workshift_out_time": workshift_out_time,
            "week_off_code": self._as_int(row.get("week_off_code")),
            "salary_type": self._as_int(row.get("salary_type")),
            "salary": row.get("salary"),
            "allowance": row.get("allowance"),
            "fname": self._as_text(row.get("fname")),
            "mname": self._as_text(row.get("mname")),
            "lname": self._as_text(row.get("lname")),
            "email": self._as_text(row.get("email")),
            "mobile": self._as_text(row.get("mobile")),
            "country_code": self._as_text(row.get("country_code")),
            "bid": self._as_int(row.get("bid")),
            "full_name": self._normalize_full_name(
                row.get("full_name"),
                row.get("fname"),
                row.get("mname"),
                row.get("lname"),
            ),
            "attendance_ready": attendance_ready,
            "grade": self._as_int(row.get("grade")),
            "interviewer": self._as_int(row.get("interviewer")),
            "notice_start_date": self._as_date_text(row.get("notice_start_date")),
            "on_notice": self._as_int(row.get("on_notice")),
            "personal_email": self._as_text(row.get("personal_email")),
            "gender": self._as_text(row.get("gender")),
            "address": self._as_text(row.get("address")),
            "city": self._as_text(row.get("city")),
            "state": self._as_text(row.get("state")),
            "country": self._as_text(row.get("country")),
            "pincode": self._as_text(row.get("pincode")),
            "auto_assign_inq": self._as_int(row.get("auto_assign_inq")),
            "associate": self._as_int(row.get("associate")),
            "qualifier": self._as_int(row.get("qualifier")),
        }

    @staticmethod
    def _normalize_full_name(*parts: Any) -> str | None:
        cleaned = [str(part).strip() for part in parts if str(part or "").strip()]
        if not cleaned:
            return None
        return cleaned[0] if len(parts) == 1 and cleaned else " ".join(cleaned)

    @staticmethod
    def _as_int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_text(value: Any) -> str | None:
        if value is None:
            return None
        text_value = str(value).strip()
        return text_value or None

    @staticmethod
    def _as_date_text(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, date):
            return value.isoformat()
        text_value = str(value).strip()
        return text_value or None

    @staticmethod
    def _as_time_text(value: Any) -> str | None:
        if value is None:
            return None
        text_value = str(value).strip()
        return text_value or None

    @staticmethod
    def _truthy_int(value: Any) -> bool:
        try:
            return int(value or 0) > 0
        except (TypeError, ValueError):
            return False
