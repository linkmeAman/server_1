"""Workforce service."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas.models import (
    FUTURE_SCOPE_ATTENDANCE,
    FUTURE_SCOPE_EMPLOYEE,
    FUTURE_SCOPE_PAYROLL,
)
from .workforce_repository import WorkforceRepository


class WorkforceService:
    STATUS_OPTIONS = [
        {"value": 1, "label": "Active"},
        {"value": 0, "label": "Inactive"},
    ]

    def __init__(self) -> None:
        self.repo = WorkforceRepository()

    async def get_meta(self, main_db: AsyncSession, central_db: AsyncSession) -> dict[str, Any]:
        del main_db
        departments = await self.repo.list_departments(central_db)
        positions = await self.repo.list_positions(central_db)
        return {
            "filter_options": {
                "departments": departments,
                "positions": positions,
                "statuses": self.STATUS_OPTIONS,
            },
            "future_scope": sorted(set(FUTURE_SCOPE_EMPLOYEE + FUTURE_SCOPE_ATTENDANCE + FUTURE_SCOPE_PAYROLL)),
        }

    async def list_employees(
        self,
        main_db: AsyncSession,
        central_db: AsyncSession,
        *,
        q: str | None,
        status: int | None,
        department_id: int | None,
        position_id: int | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        rows = await self.repo.list_employees(
            main_db,
            q=q,
            status=status,
            department_id=department_id,
            position_id=position_id,
            limit=limit,
            offset=offset,
        )
        total = await self.repo.count_employees(
            main_db,
            q=q,
            status=status,
            department_id=department_id,
            position_id=position_id,
        )
        departments = await self.repo.list_departments(central_db)
        positions = await self.repo.list_positions(central_db)
        department_map = self._map_lookup_by_id(departments)
        position_map = self._map_lookup_by_id(positions)

        employees = [
            self._serialize_employee_row(
                row,
                department_map=department_map,
                position_map=position_map,
            )
            for row in rows
        ]
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
                "departments": departments,
                "positions": positions,
                "statuses": self.STATUS_OPTIONS,
            },
            "future_scope": FUTURE_SCOPE_EMPLOYEE,
        }

    async def list_attendance_employee_index(
        self,
        main_db: AsyncSession,
        central_db: AsyncSession,
    ) -> dict[str, Any]:
        rows = await self.repo.list_employees(
            main_db,
            q=None,
            status=None,
            department_id=None,
            position_id=None,
            limit=None,
            offset=None,
        )
        departments = await self.repo.list_departments(central_db)
        positions = await self.repo.list_positions(central_db)
        department_map = self._map_lookup_by_id(departments)
        position_map = self._map_lookup_by_id(positions)

        employees = [
            self._serialize_employee_row(
                row,
                department_map=department_map,
                position_map=position_map,
            )
            for row in rows
        ]
        return {
            "employee_ids": [
                int(employee["employee_id"])
                for employee in employees
                if employee.get("employee_id") is not None
            ],
            "employees": employees,
            "total": len(employees),
            "filter_options": {
                "departments": departments,
                "positions": positions,
                "statuses": self.STATUS_OPTIONS,
            },
        }

    async def list_attendance_bssid_options(self, main_db: AsyncSession) -> dict[str, Any]:
        rows = await self.repo.list_valid_bssid_options(main_db)
        options = [
            {
                "id": self._as_int(row.get("id")),
                "bssid": self._as_text(row.get("bssid")),
                "bssid_name": self._as_text(row.get("bssid_name")),
                "venue_name": self._as_text(row.get("venue_name")),
                "wifi_name": self._as_text(row.get("wifi_name")),
            }
            for row in rows
            if self._as_text(row.get("bssid"))
        ]
        return {"options": options}

    async def get_employee(
        self,
        main_db: AsyncSession,
        central_db: AsyncSession,
        employee_id: int,
    ) -> dict[str, Any]:
        row = await self.repo.get_employee(main_db, employee_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Employee not found")

        departments = await self.repo.list_departments(central_db)
        positions = await self.repo.list_positions(central_db)
        department_map = self._map_lookup_by_id(departments)
        position_map = self._map_lookup_by_id(positions)

        parent_ids = await self.repo.get_employee_parent_ids(main_db, employee_id)
        serialized = self._serialize_employee_row(
            row,
            department_map=department_map,
            position_map=position_map,
        )
        serialized["parent_position_ids"] = parent_ids

        return {
            "employee": serialized,
            "future_scope": FUTURE_SCOPE_EMPLOYEE,
        }

    async def get_employee_attendance_summary(
        self,
        main_db: AsyncSession,
        central_db: AsyncSession,
        *,
        employee_id: int,
        from_date: str,
        to_date: str,
    ) -> dict[str, Any]:
        row = await self.repo.get_employee(main_db, employee_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Employee not found")

        departments = await self.repo.list_departments(central_db)
        positions = await self.repo.list_positions(central_db)
        department_map = self._map_lookup_by_id(departments)
        position_map = self._map_lookup_by_id(positions)

        employee = self._serialize_employee_row(
            row,
            department_map=department_map,
            position_map=position_map,
        )
        attendance_record_count = await self.repo.count_attendance_records(
            main_db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            status=None,
            regularised=None,
            invalid=None,
        )
        correction_requests = await self.repo.list_correction_requests(
            main_db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
        )
        correction_request_count = len(correction_requests)
        approved_request_count = sum(1 for item in correction_requests if self._as_int(item.get("status")) == 1)

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
                "attendance_record_count": attendance_record_count,
                "correction_request_count": correction_request_count,
                "approved_request_count": approved_request_count,
            },
            "correction_requests": [
                {
                    "request_id": self._as_int(item.get("request_id")),
                    "start_date": self._as_date_text(item.get("start_date")),
                    "end_date": self._as_date_text(item.get("end_date")),
                    "status": self._as_int(item.get("status")),
                    "request_type": self._as_int(item.get("request_type")),
                }
                for item in correction_requests
            ],
            "future_scope": FUTURE_SCOPE_ATTENDANCE,
        }

    async def get_attendance_overview(
        self,
        main_db: AsyncSession,
        central_db: AsyncSession,
        *,
        from_date: str,
        to_date: str,
        department_id: int | None,
        limit: int,
    ) -> dict[str, Any]:
        rows = await self.repo.list_employees(
            main_db,
            status=1,
            department_id=department_id,
            position_id=None,
            q=None,
            limit=limit,
            offset=0,
        )
        departments = await self.repo.list_departments(central_db)
        positions = await self.repo.list_positions(central_db)
        department_map = self._map_lookup_by_id(departments)
        position_map = self._map_lookup_by_id(positions)

        employees = [
            self._serialize_employee_row(
                row,
                department_map=department_map,
                position_map=position_map,
            )
            for row in rows
        ]

        active_employee_count = await self.repo.count_employees(
            main_db,
            q=None,
            status=1,
            department_id=department_id,
            position_id=None,
        )
        attendance_ready_count = await self.repo.count_attendance_ready_employees(
            main_db,
            department_id=department_id,
        )
        attendance_record_count = await self.repo.count_attendance_records(
            main_db,
            employee_id=None,
            from_date=from_date,
            to_date=to_date,
            status=None,
            regularised=None,
            invalid=None,
            department_id=department_id,
        )
        correction_request_count = await self.repo.count_attendance_requests(
            main_db,
            employee_id=None,
            from_date=from_date,
            to_date=to_date,
            status=None,
            request_type=None,
            department_id=department_id,
        )
        approved_request_count = await self.repo.count_attendance_requests(
            main_db,
            employee_id=None,
            from_date=from_date,
            to_date=to_date,
            status=1,
            request_type=None,
            department_id=department_id,
        )

        return {
            "from_date": from_date,
            "to_date": to_date,
            "department_id": department_id,
            "summary": {
                "active_employee_count": active_employee_count,
                "attendance_ready_count": attendance_ready_count,
                "attendance_record_count": attendance_record_count,
                "correction_request_count": correction_request_count,
                "approved_request_count": approved_request_count,
            },
            "employees": employees,
            "filter_options": {
                "departments": departments,
            },
            "future_scope": FUTURE_SCOPE_ATTENDANCE,
        }

    async def list_attendance_records(
        self,
        main_db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        status: int | None,
        regularised: int | None,
        invalid: int | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        rows = await self.repo.list_attendance_records(
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
        total = await self.repo.count_attendance_records(
            main_db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            status=status,
            regularised=regularised,
            invalid=invalid,
        )
        return {
            "rows": [self._serialize_attendance_record_row(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def update_attendance_record(
        self,
        main_db: AsyncSession,
        *,
        record_id: int,
        payload: dict[str, Any],
        modified_by: int,
    ) -> dict[str, Any]:
        existing = await self.repo.get_attendance_record(main_db, record_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Attendance record not found")

        normalized = self._normalize_attendance_record_payload(payload)
        if normalized:
            await self.repo.update_attendance_record(
                main_db,
                record_id=record_id,
                payload=normalized,
                modified_by=modified_by,
            )
            await main_db.commit()

        refreshed = await self.repo.get_attendance_record(main_db, record_id)
        if refreshed is None:
            raise HTTPException(status_code=404, detail="Attendance record not found after update")
        return {"row": self._serialize_attendance_record_row(refreshed)}

    async def create_attendance_record(
        self,
        main_db: AsyncSession,
        *,
        payload: dict[str, Any],
        created_by: int,
    ) -> dict[str, Any]:
        normalized = self._normalize_attendance_record_payload(payload)
        today = date.today().isoformat()
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        defaults: dict[str, Any] = {
            "date": today,
            "ip_address": "",
            "logout_ip_address": "",
            "mac_address": "",
            "login_bssid": "",
            "logout_bssid": "",
            "login_wifi_details": "",
            "logout_wifi_details": "",
            "login_details": "",
            "logout_details": "",
            "in_time": now_text,
            "out_time": now_text,
            "comment": "Regular",
            "status": 0,
            "regularised": 0,
            "regularised_type_id": 0,
            "invalid": 0,
            "park": 0,
        }
        merged = {**defaults, **normalized}
        contact_id = self._as_int(merged.get("contact_id"))
        if contact_id is None or contact_id <= 0:
            raise HTTPException(status_code=400, detail="contact_id is required to create attendance row")
        merged["contact_id"] = int(contact_id)

        record_id = await self.repo.create_attendance_record(
            main_db,
            payload=merged,
            created_by=created_by,
        )
        await main_db.commit()
        if record_id <= 0:
            raise HTTPException(status_code=500, detail="Failed to create attendance record")
        created = await self.repo.get_attendance_record(main_db, record_id)
        if created is None:
            raise HTTPException(status_code=500, detail="Created attendance record could not be loaded")
        return {"row": self._serialize_attendance_record_row(created)}

    async def list_attendance_requests(
        self,
        main_db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        status: int | None,
        request_type: int | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        rows = await self.repo.list_attendance_requests(
            main_db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            status=status,
            request_type=request_type,
            limit=limit,
            offset=offset,
        )
        total = await self.repo.count_attendance_requests(
            main_db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            status=status,
            request_type=request_type,
        )
        return {
            "rows": [self._serialize_attendance_request_row(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def update_attendance_request(
        self,
        main_db: AsyncSession,
        *,
        request_id: int,
        payload: dict[str, Any],
        modified_by: int,
    ) -> dict[str, Any]:
        existing = await self.repo.get_attendance_request(main_db, request_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Attendance request not found")

        normalized = self._normalize_attendance_request_payload(payload)
        if normalized:
            await self.repo.update_attendance_request(
                main_db,
                request_id=request_id,
                payload=normalized,
                modified_by=modified_by,
            )
            await main_db.commit()

        refreshed = await self.repo.get_attendance_request(main_db, request_id)
        if refreshed is None:
            raise HTTPException(status_code=404, detail="Attendance request not found after update")
        return {"row": self._serialize_attendance_request_row(refreshed)}

    async def create_attendance_request(
        self,
        main_db: AsyncSession,
        *,
        payload: dict[str, Any],
        created_by: int,
    ) -> dict[str, Any]:
        normalized = self._normalize_attendance_request_payload(payload)
        today = date.today().isoformat()
        defaults: dict[str, Any] = {
            "parent_id": 0,
            "date": today,
            "action_date": today,
            "request_type": 1,
            "no_of_days": "1",
            "start_date": today,
            "in_time": "09:30:00",
            "end_date": today,
            "out_time": "18:30:00",
            "status": 0,
            "request_comment": "",
            "parent_comment": "",
            "bid": 0,
            "park": 0,
        }
        merged = {**defaults, **normalized}
        emp_id = self._as_int(merged.get("emp_id"))
        if emp_id is None or emp_id <= 0:
            raise HTTPException(status_code=400, detail="emp_id is required to create attendance request")
        merged["emp_id"] = int(emp_id)

        request_id = await self.repo.create_attendance_request(
            main_db,
            payload=merged,
            created_by=created_by,
        )
        await main_db.commit()
        if request_id <= 0:
            raise HTTPException(status_code=500, detail="Failed to create attendance request")
        created = await self.repo.get_attendance_request(main_db, request_id)
        if created is None:
            raise HTTPException(status_code=500, detail="Created attendance request could not be loaded")
        return {"row": self._serialize_attendance_request_row(created)}

    async def bulk_update_attendance_request_status(
        self,
        main_db: AsyncSession,
        *,
        request_ids: list[int],
        status: int,
        modified_by: int,
    ) -> dict[str, Any]:
        if status not in {0, 1, 2, 3, 4}:
            raise HTTPException(status_code=400, detail="Invalid status for attendance request")
        cleaned_ids: list[int] = []
        seen: set[int] = set()
        for item in request_ids:
            request_id = self._as_int(item)
            if request_id is None or request_id <= 0:
                continue
            if request_id in seen:
                continue
            seen.add(request_id)
            cleaned_ids.append(request_id)
        if not cleaned_ids:
            raise HTTPException(status_code=400, detail="request_ids is required")

        updated_count = await self.repo.bulk_update_attendance_request_status(
            main_db,
            request_ids=cleaned_ids,
            status=int(status),
            modified_by=modified_by,
        )
        await main_db.commit()
        return {
            "updated_count": updated_count,
            "request_ids": cleaned_ids,
            "status": int(status),
        }

    async def list_attendance_leaves(
        self,
        main_db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        category: int | None,
        expired: int | None,
        park: int | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        rows = await self.repo.list_attendance_leaves(
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
        total = await self.repo.count_attendance_leaves(
            main_db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            category=category,
            expired=expired,
            park=park,
        )
        return {
            "rows": [self._serialize_attendance_leave_row(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def update_attendance_leave(
        self,
        main_db: AsyncSession,
        *,
        leave_id: int,
        payload: dict[str, Any],
        modified_by: int,
    ) -> dict[str, Any]:
        del modified_by
        existing = await self.repo.get_attendance_leave(main_db, leave_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Attendance leave not found")

        normalized = self._normalize_attendance_leave_payload(payload)
        if normalized:
            await self.repo.update_attendance_leave(
                main_db,
                leave_id=leave_id,
                payload=normalized,
            )
            await main_db.commit()

        refreshed = await self.repo.get_attendance_leave(main_db, leave_id)
        if refreshed is None:
            raise HTTPException(status_code=404, detail="Attendance leave not found after update")
        return {"row": self._serialize_attendance_leave_row(refreshed)}

    async def create_attendance_leave(
        self,
        main_db: AsyncSession,
        *,
        payload: dict[str, Any],
        created_by: int,
    ) -> dict[str, Any]:
        del created_by
        normalized = self._normalize_attendance_leave_payload(payload)
        today = date.today().isoformat()
        defaults: dict[str, Any] = {
            "emp_code": 0,
            "doi": today,
            "doc": today,
            "doe": today,
            "earned": 1.0,
            "category": 1,
            "day_code": 7,
            "consumed": 0,
            "expired": 0,
            "park": 0,
        }
        merged = {**defaults, **normalized}
        contact_id = self._as_int(merged.get("contact_id"))
        emp_id = self._as_int(merged.get("emp_id"))
        if contact_id is None or contact_id <= 0:
            raise HTTPException(status_code=400, detail="contact_id is required to create attendance leave")
        if emp_id is None or emp_id <= 0:
            raise HTTPException(status_code=400, detail="emp_id is required to create attendance leave")
        merged["contact_id"] = int(contact_id)
        merged["emp_id"] = int(emp_id)

        leave_id = await self.repo.create_attendance_leave(
            main_db,
            payload=merged,
        )
        await main_db.commit()
        if leave_id <= 0:
            raise HTTPException(status_code=500, detail="Failed to create attendance leave")
        created = await self.repo.get_attendance_leave(main_db, leave_id)
        if created is None:
            raise HTTPException(status_code=500, detail="Created attendance leave could not be loaded")
        return {"row": self._serialize_attendance_leave_row(created)}

    async def get_payroll_overview(
        self,
        main_db: AsyncSession,
        central_db: AsyncSession,
        *,
        from_date: str,
        to_date: str,
        employee_id: int | None,
        limit: int,
    ) -> dict[str, Any]:
        employee_rows = await self.repo.list_employees(
            main_db,
            status=None,
            department_id=None,
            position_id=None,
            q=None,
            limit=limit,
            offset=0,
        )
        departments = await self.repo.list_departments(central_db)
        positions = await self.repo.list_positions(central_db)
        department_map = self._map_lookup_by_id(departments)
        position_map = self._map_lookup_by_id(positions)

        employees = [
            self._serialize_employee_row(
                row,
                department_map=department_map,
                position_map=position_map,
            )
            for row in employee_rows
        ]

        payroll_total = await self.repo.count_payroll_records(
            main_db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
        )
        salary_sum = await self.repo.sum_payroll_salary(
            main_db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
        )
        paid_sum = await self.repo.sum_payroll_paid(
            main_db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
        )

        return {
            "from_date": from_date,
            "to_date": to_date,
            "employee_id": employee_id,
            "summary": {
                "employee_count": len(employees),
                "payroll_count": payroll_total,
                "salary_sum": salary_sum,
                "paid_sum": paid_sum,
                "unpaid_sum": salary_sum - paid_sum,
            },
            "employees": employees,
            "filter_options": {
                "departments": departments,
                "positions": positions,
                "statuses": self.STATUS_OPTIONS,
            },
            "future_scope": FUTURE_SCOPE_PAYROLL,
        }

    async def list_payroll_records(
        self,
        main_db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        paid: int | None,
        park: int | None,
        limit: int,
        offset: int,
        paid_nonzero: bool = False,
    ) -> dict[str, Any]:
        rows = await self.repo.list_payroll_records(
            main_db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            paid=paid,
            park=park,
            limit=limit,
            offset=offset,
            paid_nonzero=paid_nonzero,
        )
        total = await self.repo.count_payroll_records(
            main_db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            paid=paid,
            park=park,
            paid_nonzero=paid_nonzero,
        )
        return {
            "rows": [self._serialize_payroll_record_row(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def update_payroll_record(
        self,
        main_db: AsyncSession,
        *,
        record_id: int,
        payload: dict[str, Any],
        modified_by: int,
    ) -> dict[str, Any]:
        existing = await self.repo.get_payroll_record(main_db, record_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Payroll record not found")

        normalized = self._normalize_payroll_payload(payload)
        if normalized:
            await self.repo.update_payroll_record(
                main_db,
                record_id=record_id,
                payload=normalized,
                modified_by=modified_by,
            )
            await main_db.commit()

        refreshed = await self.repo.get_payroll_record(main_db, record_id)
        if refreshed is None:
            raise HTTPException(status_code=404, detail="Payroll record not found after update")
        return {"row": self._serialize_payroll_record_row(refreshed)}

    async def create_payroll_record(
        self,
        main_db: AsyncSession,
        *,
        payload: dict[str, Any],
        created_by: int,
    ) -> dict[str, Any]:
        normalized = self._normalize_payroll_payload(payload)
        today = date.today().isoformat()
        month_stamp = today[:7]
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        defaults: dict[str, Any] = {
            "from_date": today,
            "to_date": today,
            "working_days": 0,
            "present_days": 0,
            "absent_days": 0,
            "wo_days": 0,
            "par_day": 0,
            "total_leaves": 0.0,
            "paid_leave": 0.0,
            "unpaid_leave": 0.0,
            "leave_balance_before": 0.0,
            "leave_balance_after": 0.0,
            "tax_amount": 0,
            "tax_comment": "",
            "advance_amount": 0,
            "advance_comment": "",
            "fine_amount": 0,
            "fine_comment": "",
            "base_amount": 0,
            "incentive": 0,
            "incentive_comment": "",
            "deduction": 0,
            "deduction_comment": "",
            "addition": 0,
            "addition_comment": "",
            "extra_amount": 0,
            "extra_comment": "",
            "allowance": 0,
            "pay_mode": 0,
            "sub_total": 0,
            "salary": 0,
            "paid": 0,
            "bid": 0,
            "comments": "",
            "paid_holidays": 0,
            "paid_holidays_dates": "",
            "leaves": 0,
            "leaves_dates": "",
            "wfh": 0,
            "wfh_dates": "",
            "half_day": 0,
            "half_day_dates": "",
            "optional_holiday": 0,
            "optional_holiday_dates": "",
            "punch_inout": 0,
            "punch_inout_dates": "",
            "break": 0,
            "break_dates": "",
            "supplementary": 0,
            "supplementary_dates": "",
            "park": 0,
            "response_data": {},
            "response_data_app": {},
            "pay_slip_data": {},
            "incentive_html": "",
            "year_month": month_stamp,
            "created_at": now_text,
            "modified_at": now_text,
        }
        merged = {**defaults, **normalized}
        contact_id = self._as_int(merged.get("contact_id"))
        if contact_id is None or contact_id <= 0:
            raise HTTPException(status_code=400, detail="contact_id is required to create payroll row")
        merged["contact_id"] = int(contact_id)

        record_id = await self.repo.create_payroll_record(
            main_db,
            payload=merged,
            created_by=created_by,
        )
        await main_db.commit()
        if record_id <= 0:
            raise HTTPException(status_code=500, detail="Failed to create payroll record")
        created = await self.repo.get_payroll_record(main_db, record_id)
        if created is None:
            raise HTTPException(status_code=500, detail="Created payroll record could not be loaded")
        return {"row": self._serialize_payroll_record_row(created)}

    async def delete_payroll_record(
        self,
        main_db: AsyncSession,
        *,
        record_id: int,
    ) -> None:
        existing = await self.repo.get_payroll_record(main_db, record_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Payroll record not found")

        await self.repo.delete_payroll_record(main_db, record_id=record_id)
        await main_db.commit()

    async def salary_track(
        self,
        main_db: AsyncSession,
        central_db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        positions = await self.repo.list_positions(central_db)
        position_map = self._map_lookup_by_id(positions)
        rows = await self.repo.list_salary_track(
            main_db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            offset=offset,
            position_map=position_map,
        )
        total = await self.repo.count_salary_track(
            main_db,
            employee_id=employee_id,
        )
        return {
            "rows": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def _serialize_employee_row(
        self,
        row: dict[str, Any],
        *,
        department_map: dict[int, str] | None = None,
        position_map: dict[int, str] | None = None,
    ) -> dict[str, Any]:
        workshift_id = self._as_int(row.get("workshift_id"))
        workshift_in_time = self._as_time_text(row.get("workshift_in_time"))
        workshift_out_time = self._as_time_text(row.get("workshift_out_time"))
        department_id = self._as_int(row.get("department_id"))
        position_id = self._as_int(row.get("position_id"))
        department_name = self._as_text(row.get("department"))
        position_name = self._as_text(row.get("position"))

        if department_name is None and department_id is not None and department_map:
            department_name = department_map.get(department_id)
        if position_name is None and position_id is not None and position_map:
            position_name = position_map.get(position_id)

        attendance_ready = bool(
            (workshift_id is not None and workshift_id > 0)
            or (workshift_in_time and workshift_out_time)
        )

        return {
            "employee_id": self._as_int(row.get("employee_id")),
            "contact_id": self._as_int(row.get("contact_id")),
            "ecode": row.get("ecode"),
            "department_id": department_id,
            "department": department_name,
            "position_id": position_id,
            "position": position_name,
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
            "parent_id": self._as_int(row.get("parent_id")),
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
            "dob": self._as_date_text(row.get("dob")),
            "mobile2": self._as_text(row.get("mobile2")),
            "country_code_2": self._as_text(row.get("country_code_2")),
            "phone_no": self._as_text(row.get("phone_no")),
            "ename": self._as_text(row.get("ename")),
            "emobile": self._as_text(row.get("emobile")),
            "ecountry_code": self._as_text(row.get("ecountry_code")),
            "relation": self._as_text(row.get("relation")),
            "document_type_id": self._as_int(row.get("document_type_id")),
            "document_number": self._as_text(row.get("document_number")),
            "document_image": self._as_text(row.get("document_image")),
            "document_type_id_2": self._as_int(row.get("document_type_id_2")),
            "document_number_2": self._as_text(row.get("document_number_2")),
            "document_image_2": self._as_text(row.get("document_image_2")),
            "document_type_id_3": self._as_int(row.get("document_type_id_3")),
            "document_image_3": self._as_text(row.get("document_image_3")),
            "calculate_salary": self._as_int(row.get("calculate_salary")),
            "is_parent": self._as_int(row.get("is_parent")),
            "demo_owner": self._as_int(row.get("demo_owner")),
            "cash_collector": self._as_int(row.get("cash_collector")),
            "tds_type": self._as_int(row.get("tds_type")),
            "tds_percent": row.get("tds_percent"),
            "rate_multiplier": row.get("rate_multiplier"),
            "incentive_new": row.get("incentive_new"),
            "incentive_renew": row.get("incentive_renew"),
            "p_incentive_c": row.get("p_incentive_c"),
            "p_incentive_sc": row.get("p_incentive_sc"),
            "trainer_incentive": row.get("trainer_incentive"),
            "mt_incentive": row.get("mt_incentive"),
        }

    def _serialize_attendance_record_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": self._as_int(row.get("id")),
            "employee_id": self._as_int(row.get("employee_id")),
            "contact_id": self._as_int(row.get("contact_id")),
            "full_name": self._normalize_full_name(
                row.get("full_name"),
                row.get("fname"),
                row.get("mname"),
                row.get("lname"),
            ),
            "email": self._as_text(row.get("email")),
            "mobile": self._as_text(row.get("mobile")),
            "date": self._as_date_text(row.get("date")),
            "ip_address": self._as_text(row.get("ip_address")) or "",
            "logout_ip_address": self._as_text(row.get("logout_ip_address")) or "",
            "mac_address": self._as_text(row.get("mac_address")) or "",
            "login_bssid": self._as_text(row.get("login_bssid")),
            "logout_bssid": self._as_text(row.get("logout_bssid")),
            "login_wifi_details": self._as_text(row.get("login_wifi_details")),
            "logout_wifi_details": self._as_text(row.get("logout_wifi_details")),
            "login_details": self._as_text(row.get("login_details")) or "",
            "logout_details": self._as_text(row.get("logout_details")) or "",
            "in_time": self._as_datetime_text(row.get("in_time")),
            "out_time": self._as_datetime_text(row.get("out_time")),
            "comment": self._as_text(row.get("comment")) or "",
            "status": self._as_int(row.get("status")),
            "regularised": self._as_int(row.get("regularised")),
            "regularised_type_id": self._as_int(row.get("regularised_type_id")),
            "invalid": self._as_int(row.get("invalid")),
            "park": self._as_int(row.get("park")),
            "created_by": self._as_int(row.get("created_by")),
            "created_at": self._as_datetime_text(row.get("created_at")),
            "modified_by": self._as_int(row.get("modified_by")),
            "modified_at": self._as_datetime_text(row.get("modified_at")),
        }

    def _serialize_attendance_request_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": self._as_int(row.get("id")),
            "employee_id": self._as_int(row.get("emp_id")),
            "contact_id": self._as_int(row.get("contact_id")),
            "full_name": self._normalize_full_name(
                row.get("full_name"),
                row.get("fname"),
                row.get("mname"),
                row.get("lname"),
            ),
            "email": self._as_text(row.get("email")),
            "mobile": self._as_text(row.get("mobile")),
            "date": self._as_date_text(row.get("date")),
            "action_date": self._as_date_text(row.get("action_date")),
            "parent_id": self._as_int(row.get("parent_id")),
            "request_type": self._as_int(row.get("request_type")),
            "no_of_days": self._as_text(row.get("no_of_days")) or "",
            "start_date": self._as_date_text(row.get("start_date")),
            "in_time": self._as_time_text(row.get("in_time")),
            "end_date": self._as_date_text(row.get("end_date")),
            "out_time": self._as_time_text(row.get("out_time")),
            "status": self._as_int(row.get("status")),
            "request_comment": self._as_text(row.get("request_comment")) or "",
            "parent_comment": self._as_text(row.get("parent_comment")) or "",
            "bid": self._as_int(row.get("bid")),
            "park": self._as_int(row.get("park")),
            "created_by": self._as_int(row.get("created_by")),
            "created_at": self._as_datetime_text(row.get("created_at")),
            "modified_by": self._as_int(row.get("modified_by")),
            "modified_at": self._as_datetime_text(row.get("modified_at")),
        }

    def _serialize_attendance_leave_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": self._as_int(row.get("id")),
            "employee_id": self._as_int(row.get("emp_id")),
            "contact_id": self._as_int(row.get("contact_id")),
            "emp_code": row.get("emp_code"),
            "full_name": self._normalize_full_name(
                row.get("full_name"),
                row.get("fname"),
                row.get("mname"),
                row.get("lname"),
            ),
            "email": self._as_text(row.get("email")),
            "mobile": self._as_text(row.get("mobile")),
            "doi": self._as_date_text(row.get("doi")),
            "doc": self._as_date_text(row.get("doc")),
            "doe": self._as_date_text(row.get("doe")),
            "earned": row.get("earned"),
            "category": self._as_int(row.get("category")),
            "day_code": self._as_int(row.get("day_code")),
            "consumed": self._as_int(row.get("consumed")),
            "expired": self._as_int(row.get("expired")),
            "park": self._as_int(row.get("park")),
            "created_at": self._as_datetime_text(row.get("created_at")),
            "modified_at": self._as_datetime_text(row.get("modified_at")),
        }

    def _serialize_payroll_record_row(self, row: dict[str, Any]) -> dict[str, Any]:
        def _json_field(value: Any) -> Any:
            if value is None:
                return {}
            if isinstance(value, (dict, list)):
                return value
            try:
                import json

                parsed = json.loads(str(value))
                return parsed
            except (ValueError, TypeError):
                return {}

        return {
            "id": self._as_int(row.get("id")),
            "employee_id": self._as_int(row.get("employee_id")),
            "contact_id": self._as_int(row.get("contact_id")),
            "full_name": self._normalize_full_name(
                row.get("full_name"),
                row.get("fname"),
                row.get("mname"),
                row.get("lname"),
            ),
            "email": self._as_text(row.get("email")),
            "mobile": self._as_text(row.get("mobile")),
            "from_date": self._as_date_text(row.get("from_date")),
            "to_date": self._as_date_text(row.get("to_date")),
            "working_days": self._as_int(row.get("working_days")),
            "present_days": self._as_int(row.get("present_days")),
            "absent_days": self._as_int(row.get("absent_days")),
            "wo_days": self._as_int(row.get("wo_days")),
            "par_day": self._as_int(row.get("par_day")),
            "total_leaves": row.get("total_leaves"),
            "paid_leave": row.get("paid_leave"),
            "unpaid_leave": row.get("unpaid_leave"),
            "leave_balance_before": row.get("leave_balance_before"),
            "leave_balance_after": row.get("leave_balance_after"),
            "tax_amount": self._as_int(row.get("tax_amount")),
            "tax_comment": self._as_text(row.get("tax_comment")) or "",
            "advance_amount": self._as_int(row.get("advance_amount")),
            "advance_comment": self._as_text(row.get("advance_comment")) or "",
            "fine_amount": self._as_int(row.get("fine_amount")),
            "fine_comment": self._as_text(row.get("fine_comment")) or "",
            "base_amount": self._as_int(row.get("base_amount")),
            "incentive": self._as_int(row.get("incentive")),
            "incentive_comment": self._as_text(row.get("incentive_comment")) or "",
            "deduction": self._as_int(row.get("deduction")),
            "deduction_comment": self._as_text(row.get("deduction_comment")) or "",
            "addition": self._as_int(row.get("addition")),
            "addition_comment": self._as_text(row.get("addition_comment")) or "",
            "extra_amount": self._as_int(row.get("extra_amount")),
            "extra_comment": self._as_text(row.get("extra_comment")) or "",
            "allowance": self._as_int(row.get("allowance")),
            "pay_mode": self._as_int(row.get("pay_mode")),
            "sub_total": self._as_int(row.get("sub_total")),
            "salary": self._as_int(row.get("salary")),
            "paid": self._as_int(row.get("paid")),
            "bid": self._as_int(row.get("bid")),
            "comments": self._as_text(row.get("comments")) or "",
            "paid_holidays": self._as_int(row.get("paid_holidays")),
            "paid_holidays_dates": self._as_text(row.get("paid_holidays_dates")) or "",
            "leaves": self._as_int(row.get("leaves")),
            "leaves_dates": self._as_text(row.get("leaves_dates")) or "",
            "wfh": self._as_int(row.get("wfh")),
            "wfh_dates": self._as_text(row.get("wfh_dates")) or "",
            "half_day": self._as_int(row.get("half_day")),
            "half_day_dates": self._as_text(row.get("half_day_dates")) or "",
            "optional_holiday": self._as_int(row.get("optional_holiday")),
            "optional_holiday_dates": self._as_text(row.get("optional_holiday_dates")) or "",
            "punch_inout": self._as_int(row.get("punch_inout")),
            "punch_inout_dates": self._as_text(row.get("punch_inout_dates")) or "",
            "break": self._as_int(row.get("break")),
            "break_dates": self._as_text(row.get("break_dates")) or "",
            "supplementary": self._as_int(row.get("supplementary")),
            "supplementary_dates": self._as_text(row.get("supplementary_dates")) or "",
            "park": self._as_int(row.get("park")),
            "response_data": _json_field(row.get("response_data")),
            "response_data_app": _json_field(row.get("response_data_app")),
            "pay_slip_data": _json_field(row.get("pay_slip_data")),
            "incentive_html": self._as_text(row.get("incentive_html")) or "",
            "year_month": self._as_text(row.get("year_month")) or "",
            "created_at": self._as_datetime_text(row.get("created_at")),
            "created_by": self._as_int(row.get("created_by")),
            "modified_at": self._as_datetime_text(row.get("modified_at")),
            "modified_by": self._as_int(row.get("modified_by")),
        }

    def _normalize_attendance_record_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        int_fields = {
            "contact_id",
            "status",
            "regularised",
            "regularised_type_id",
            "invalid",
            "park",
        }
        str_fields = {
            "date",
            "ip_address",
            "logout_ip_address",
            "mac_address",
            "login_bssid",
            "logout_bssid",
            "login_wifi_details",
            "logout_wifi_details",
            "login_details",
            "logout_details",
            "in_time",
            "out_time",
            "comment",
        }
        for field, value in payload.items():
            if field in int_fields:
                normalized[field] = self._coerce_int(value)
            elif field in str_fields:
                normalized[field] = self._coerce_string(value)
        return normalized

    def _normalize_attendance_request_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        int_fields = {
            "emp_id",
            "parent_id",
            "request_type",
            "status",
            "bid",
            "park",
        }
        str_fields = {
            "date",
            "action_date",
            "no_of_days",
            "start_date",
            "in_time",
            "end_date",
            "out_time",
            "request_comment",
            "parent_comment",
        }
        for field, value in payload.items():
            if field in int_fields:
                normalized[field] = self._coerce_int(value)
            elif field in str_fields:
                normalized[field] = self._coerce_string(value)
        return normalized

    def _normalize_payroll_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        int_fields = {
            "contact_id",
            "working_days",
            "present_days",
            "absent_days",
            "wo_days",
            "par_day",
            "tax_amount",
            "advance_amount",
            "fine_amount",
            "base_amount",
            "incentive",
            "deduction",
            "addition",
            "extra_amount",
            "allowance",
            "pay_mode",
            "sub_total",
            "salary",
            "paid",
            "bid",
            "paid_holidays",
            "leaves",
            "wfh",
            "half_day",
            "optional_holiday",
            "punch_inout",
            "break",
            "supplementary",
            "park",
            "created_by",
            "modified_by",
        }
        float_fields = {
            "total_leaves",
            "paid_leave",
            "unpaid_leave",
            "leave_balance_before",
            "leave_balance_after",
        }
        str_fields = {
            "from_date",
            "to_date",
            "tax_comment",
            "advance_comment",
            "fine_comment",
            "incentive_comment",
            "deduction_comment",
            "addition_comment",
            "extra_comment",
            "comments",
            "paid_holidays_dates",
            "leaves_dates",
            "wfh_dates",
            "half_day_dates",
            "optional_holiday_dates",
            "punch_inout_dates",
            "break_dates",
            "supplementary_dates",
            "incentive_html",
            "year_month",
            "created_at",
            "modified_at",
        }
        json_fields = {"response_data", "response_data_app", "pay_slip_data"}

        for field, value in payload.items():
            if field in int_fields:
                normalized[field] = self._coerce_int(value)
            elif field in float_fields:
                normalized[field] = self._coerce_float(value)
            elif field in str_fields:
                normalized[field] = self._coerce_string(value)
            elif field in json_fields:
                normalized[field] = self._coerce_json(value)
        return normalized

    def _normalize_attendance_leave_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        int_fields = {
            "contact_id",
            "emp_id",
            "emp_code",
            "category",
            "day_code",
            "consumed",
            "expired",
            "park",
        }
        float_fields = {"earned"}
        str_fields = {"doi", "doc", "doe"}
        for field, value in payload.items():
            if field in int_fields:
                normalized[field] = self._coerce_int(value)
            elif field in float_fields:
                normalized[field] = self._coerce_float(value)
            elif field in str_fields:
                normalized[field] = self._coerce_string(value)
        return normalized

    # -------------------------------------------------------------------------
    # Employee form-meta / create / update
    # -------------------------------------------------------------------------

    async def get_employee_form_meta(
        self,
        main_db: AsyncSession,
        central_db: AsyncSession,
    ) -> dict[str, Any]:
        departments = await self.repo.list_departments(central_db)
        positions = await self.repo.list_positions(central_db)
        workshifts = await self.repo.list_workshifts(main_db)
        document_types = await self.repo.list_document_types(central_db)
        all_employees = await self.repo.list_all_employees_simple(main_db)
        return {
            "departments": departments,
            "positions": positions,
            "workshifts": workshifts,
            "statuses": self.STATUS_OPTIONS,
            "document_types": document_types,
            "all_employees": all_employees,
            "genders": [
                {"value": "M", "label": "Male"},
                {"value": "F", "label": "Female"},
                {"value": "O", "label": "Other"},
            ],
            "salary_types": [
                {"value": 1, "label": "Fixed Salary"},
                {"value": 2, "label": "Per Day"},
                {"value": 3, "label": "Per Hour"},
            ],
            "employee_types": [
                {"value": 0, "label": "Permanent"},
                {"value": 1, "label": "Franchisee"},
            ],
        }

    async def create_employee(
        self,
        main_db: AsyncSession,
        central_db: AsyncSession,
        payload: dict[str, Any],
        created_by: int,
    ) -> dict[str, Any]:
        mobile = self._as_text(payload.get("mobile"))
        if not mobile:
            raise HTTPException(status_code=422, detail="mobile is required")
        # Strip to digits for global uniqueness check
        if not await self.repo.check_mobile_unique(main_db, mobile):
            raise HTTPException(status_code=409, detail="Mobile number already registered")

        fname = self._as_text(payload.get("fname"))
        if not fname:
            raise HTTPException(status_code=422, detail="fname (first name) is required")

        department_id = self._as_int(payload.get("department_id"))
        position_id = self._as_int(payload.get("position_id"))
        doj = self._as_text(payload.get("doj"))
        if not doj:
            raise HTTPException(status_code=422, detail="doj (date of joining) is required")

        contact_data: dict[str, Any] = {
            "fname": fname,
            "mname": self._as_text(payload.get("mname")),
            "lname": self._as_text(payload.get("lname")),
            "mobile": mobile,
            "country_code": self._as_text(payload.get("country_code")) or "+91",
            "mobile2": self._as_text(payload.get("mobile2")),
            "country_code_2": self._as_text(payload.get("country_code_2")) or "+91",
            "phone_no": self._as_text(payload.get("phone_no")),
            "email": self._as_text(payload.get("email")),
            "personal_email": self._as_text(payload.get("personal_email")),
            "gender": self._as_text(payload.get("gender")),
            "dob": self._as_text(payload.get("dob")),
            "address": self._as_text(payload.get("address")),
            "city": self._as_text(payload.get("city")),
            "state": self._as_text(payload.get("state")),
            "country": self._as_text(payload.get("country")),
            "pincode": self._as_text(payload.get("pincode")),
            "document_type_id": self._as_int(payload.get("document_type_id")),
            "document_number": self._as_text(payload.get("document_number")),
            "document_type_id_2": self._as_int(payload.get("document_type_id_2")),
            "document_number_2": self._as_text(payload.get("document_number_2")),
            "document_type_id_3": self._as_int(payload.get("document_type_id_3")),
            "bid": self._as_int(payload.get("bid")) or 0,
        }
        contact_data = {k: v for k, v in contact_data.items() if v is not None}

        # Handle emergency contact — stored as a separate contact row linked via parent_id
        ename = self._as_text(payload.get("ename"))
        emobile = self._as_text(payload.get("emobile"))
        ecountry_code = self._as_text(payload.get("ecountry_code")) or "+91"
        relation = self._as_text(payload.get("relation"))
        if ename and emobile and relation:
            name_parts = ename.strip().split(" ", 1)
            emergency_data: dict[str, Any] = {
                "fname": name_parts[0],
                "lname": name_parts[1] if len(name_parts) > 1 else "",
                "mobile": emobile,
                "country_code": ecountry_code,
                "bid": self._as_int(payload.get("bid")) or 0,
            }
            parent_contact_id = await self.repo.create_contact(main_db, emergency_data)
            contact_data["parent_id"] = parent_contact_id
            contact_data["relation"] = relation

        employee_data: dict[str, Any] = {
            "ecode": self._as_text(payload.get("ecode")),
            "department_id": department_id,
            "position_id": position_id,
            "doj": doj,
            "doe": self._as_text(payload.get("doe")),
            "exit_date": self._as_text(payload.get("exit_date")),
            "workshift_id": self._as_int(payload.get("workshift_id")),
            "workshift_in_time": self._as_text(payload.get("workshift_in_time")),
            "workshift_out_time": self._as_text(payload.get("workshift_out_time")),
            "salary_type": self._as_int(payload.get("salary_type")) or 1,
            "salary": payload.get("salary"),
            "allowance": payload.get("allowance"),
            "type": self._as_int(payload.get("employee_type")) or 0,
            "status": self._as_int(payload.get("status")) if payload.get("status") is not None else 1,
            "grade": self._as_int(payload.get("grade")),
            "bid": self._as_int(payload.get("bid")) or 0,
            "park": 0,
            # Toggles
            "user_account": self._as_int(payload.get("user_account")) or 0,
            "is_admin": self._as_int(payload.get("is_admin")) or 0,
            "calculate_salary": self._as_int(payload.get("calculate_salary")) or 0,
            "is_parent": self._as_int(payload.get("is_parent")) or 0,
            "demo_owner": self._as_int(payload.get("demo_owner")) or 0,
            "cash_collector": self._as_int(payload.get("cash_collector")) or 0,
            "auto_assign_inq": self._as_int(payload.get("auto_assign_inq")) or 0,
            "qualifier": self._as_int(payload.get("qualifier")) or 0,
            # Financial
            "tds_type": self._as_int(payload.get("tds_type")) or 0,
            "tds_percent": payload.get("tds_percent"),
            "rate_multiplier": payload.get("rate_multiplier"),
            "incentive_new": payload.get("incentive_new"),
            "incentive_renew": payload.get("incentive_renew"),
            "p_incentive_c": payload.get("p_incentive_c"),
            "p_incentive_sc": payload.get("p_incentive_sc"),
            "trainer_incentive": payload.get("trainer_incentive"),
            "mt_incentive": payload.get("mt_incentive"),
        }
        employee_data = {k: v for k, v in employee_data.items() if v is not None}

        contact_id = await self.repo.create_contact(main_db, contact_data)
        employee_data["contact_id"] = contact_id
        employee_id = await self.repo.create_employee_record(main_db, employee_data)

        # Save parent positions if provided
        parent_ids_raw = payload.get("parent_position_ids")
        if parent_ids_raw and isinstance(parent_ids_raw, list):
            parent_ids = [int(x) for x in parent_ids_raw if x is not None]
            await self.repo.set_employee_parents(main_db, employee_id, parent_ids)

        await main_db.commit()

        row = await self.repo.get_employee(main_db, employee_id)
        if row is None:
            raise HTTPException(status_code=500, detail="Employee created but could not be loaded")

        departments = await self.repo.list_departments(central_db)
        positions = await self.repo.list_positions(central_db)
        return {
            "employee": self._serialize_employee_row(
                row,
                department_map=self._map_lookup_by_id(departments),
                position_map=self._map_lookup_by_id(positions),
            )
        }

    async def update_employee(
        self,
        main_db: AsyncSession,
        central_db: AsyncSession,
        employee_id: int,
        payload: dict[str, Any],
        modified_by: int,
    ) -> dict[str, Any]:
        existing = await self.repo.get_employee(main_db, employee_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Employee not found")

        contact_id = self._as_int(existing.get("contact_id"))

        # Mobile uniqueness check only if mobile is being changed
        new_mobile = self._as_text(payload.get("mobile"))
        if new_mobile and new_mobile != self._as_text(existing.get("mobile")):
            if not await self.repo.check_mobile_unique(main_db, new_mobile, exclude_contact_id=contact_id):
                raise HTTPException(status_code=409, detail="Mobile number already registered")

        contact_data: dict[str, Any] = {}
        for field in ("fname", "mname", "lname", "mobile", "country_code",
                      "mobile2", "country_code_2", "phone_no",
                      "email", "personal_email", "gender", "dob",
                      "address", "city", "state", "country", "pincode",
                      "document_type_id", "document_number",
                      "document_type_id_2", "document_number_2",
                      "document_type_id_3"):
            if field in payload:
                contact_data[field] = self._as_text(payload[field]) if isinstance(payload[field], str) else payload[field]

        # Handle emergency contact — create a new emergency contact row and link via parent_id
        ename = self._as_text(payload.get("ename"))
        emobile = self._as_text(payload.get("emobile"))
        ecountry_code = self._as_text(payload.get("ecountry_code")) or "+91"
        relation = self._as_text(payload.get("relation"))
        if ename and emobile and relation:
            name_parts = ename.strip().split(" ", 1)
            emergency_data: dict[str, Any] = {
                "fname": name_parts[0],
                "lname": name_parts[1] if len(name_parts) > 1 else "",
                "mobile": emobile,
                "country_code": ecountry_code,
                "bid": self._as_int(existing.get("bid")) or 0,
            }
            parent_contact_id = await self.repo.create_contact(main_db, emergency_data)
            contact_data["parent_id"] = parent_contact_id
            contact_data["relation"] = relation
        elif "relation" in payload and not ename:
            # Allow updating just the relation without changing emergency contact
            if relation is not None:
                contact_data["relation"] = relation

        employee_data: dict[str, Any] = {}
        for field in ("ecode", "department_id", "position_id", "doj", "doe", "exit_date",
                      "workshift_id", "workshift_in_time", "workshift_out_time",
                      "salary_type", "salary", "allowance", "grade",
                      "user_account", "is_admin", "calculate_salary", "is_parent",
                      "demo_owner", "cash_collector", "auto_assign_inq", "qualifier",
                      "associate", "on_notice",
                      "tds_type", "tds_percent", "rate_multiplier",
                      "incentive_new", "incentive_renew", "p_incentive_c", "p_incentive_sc",
                      "trainer_incentive", "mt_incentive"):
            if field in payload:
                employee_data[field] = payload[field]
        if "employee_type" in payload:
            employee_data["type"] = payload["employee_type"]
        if "status" in payload:
            employee_data["status"] = self._as_int(payload["status"])

        if contact_id is not None and contact_data:
            await self.repo.update_contact(main_db, contact_id, contact_data)
        if employee_data:
            await self.repo.update_employee_record(main_db, employee_id, employee_data)

        # Save parent positions if provided
        parent_ids_raw = payload.get("parent_position_ids")
        if parent_ids_raw is not None and isinstance(parent_ids_raw, list):
            parent_ids = [int(x) for x in parent_ids_raw if x is not None]
            await self.repo.set_employee_parents(main_db, employee_id, parent_ids)

        await main_db.commit()

        row = await self.repo.get_employee(main_db, employee_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Employee not found after update")

        departments = await self.repo.list_departments(central_db)
        positions = await self.repo.list_positions(central_db)
        return {
            "employee": self._serialize_employee_row(
                row,
                department_map=self._map_lookup_by_id(departments),
                position_map=self._map_lookup_by_id(positions),
            )
        }

    @staticmethod
    def _normalize_full_name(full_name: Any, first_name: Any, middle_name: Any, last_name: Any) -> str | None:
        explicit_full_name = str(full_name or "").strip()
        if explicit_full_name:
            return explicit_full_name
        cleaned = [
            str(part).strip()
            for part in (first_name, middle_name, last_name)
            if str(part or "").strip()
        ]
        if not cleaned:
            return None
        return " ".join(cleaned)

    @staticmethod
    def _map_lookup_by_id(items: list[dict[str, Any]]) -> dict[int, str]:
        mapped: dict[int, str] = {}
        for item in items:
            try:
                key = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                mapped[key] = name
        return mapped

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
    def _as_datetime_text(value: Any) -> str | None:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat(sep=" ", timespec="seconds")
        text_value = str(value).strip()
        return text_value or None

    @staticmethod
    def _as_time_text(value: Any) -> str | None:
        """Convert a DB TIME value (timedelta or str) to 'HH:MM:SS' string."""
        if value is None:
            return None
        # MySQL TIME columns come back as datetime.timedelta via SQLAlchemy
        if hasattr(value, "total_seconds"):
            total = int(value.total_seconds())
            h = total // 3600
            m = (total % 3600) // 60
            s = total % 60
            return f"{h:02d}:{m:02d}:{s:02d}"
        # datetime.time object
        if hasattr(value, "strftime"):
            return value.strftime("%H:%M:%S")
        text_value = str(value).strip()
        return text_value or None

    @staticmethod
    def _truthy_int(value: Any) -> bool:
        try:
            return int(value or 0) > 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _coerce_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid integer value: {value}") from exc

    @staticmethod
    def _coerce_string(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _coerce_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid float value: {value}") from exc

    @staticmethod
    def _coerce_json(value: Any) -> Any:
        if value is None:
            return {}
        if isinstance(value, (dict, list)):
            return value
        try:
            import json

            return json.loads(str(value))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON value: {value}") from exc
