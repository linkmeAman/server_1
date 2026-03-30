"""Workforce service."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas.models import FUTURE_SCOPE_ATTENDANCE, FUTURE_SCOPE_EMPLOYEE
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
            "future_scope": sorted(set(FUTURE_SCOPE_EMPLOYEE + FUTURE_SCOPE_ATTENDANCE)),
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

        return {
            "employee": self._serialize_employee_row(
                row,
                department_map=department_map,
                position_map=position_map,
            ),
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
