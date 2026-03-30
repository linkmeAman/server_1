"""Workforce repository."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class WorkforceRepository:
    async def list_departments(self, central_db: AsyncSession) -> list[dict[str, Any]]:
        result = await central_db.execute(
            text(
                """
                SELECT id, department AS name
                FROM employee_department
                WHERE id IS NOT NULL
                ORDER BY department ASC
                """
            )
        )
        return [dict(row._mapping) for row in result.fetchall()]

    async def list_positions(self, central_db: AsyncSession) -> list[dict[str, Any]]:
        result = await central_db.execute(
            text(
                """
                SELECT id, position AS name
                FROM employee_position
                WHERE id IS NOT NULL
                ORDER BY position ASC
                """
            )
        )
        return [dict(row._mapping) for row in result.fetchall()]

    async def list_valid_bssid_options(self, db: AsyncSession) -> list[dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT
                    MIN(id) AS id,
                    bssid,
                    MAX(NULLIF(TRIM(bssid_name), '')) AS bssid_name,
                    MAX(NULLIF(TRIM(venue_name), '')) AS venue_name,
                    MAX(NULLIF(TRIM(wifi_name), '')) AS wifi_name
                FROM venue_details
                WHERE NULLIF(TRIM(bssid), '') IS NOT NULL
                GROUP BY bssid
                ORDER BY
                    MAX(NULLIF(TRIM(bssid_name), '')) ASC,
                    MAX(NULLIF(TRIM(venue_name), '')) ASC,
                    MAX(NULLIF(TRIM(wifi_name), '')) ASC,
                    bssid ASC
                """
            )
        )
        return [dict(row._mapping) for row in result.fetchall()]

    async def count_employees(
        self,
        db: AsyncSession,
        *,
        q: str | None = None,
        status: int | None = None,
        department_id: int | None = None,
        position_id: int | None = None,
    ) -> int:
        query, params = self._employee_base_query(
            select_sql="SELECT COUNT(*) AS total",
            q=q,
            status=status,
            department_id=department_id,
            position_id=position_id,
        )
        result = await db.execute(text(query), params)
        row = result.fetchone()
        return int(row._mapping["total"]) if row else 0

    async def list_employees(
        self,
        db: AsyncSession,
        *,
        q: str | None = None,
        status: int | None = None,
        department_id: int | None = None,
        position_id: int | None = None,
        limit: int | None = 25,
        offset: int | None = 0,
    ) -> list[dict[str, Any]]:
        query, params = self._employee_base_query(
            select_sql="""
                SELECT
                    e.id AS employee_id,
                    e.contact_id,
                    e.ecode,
                    e.department_id,
                    e.position_id,
                    e.status,
                    e.user_account,
                    e.is_admin,
                    e.type AS employee_type,
                    e.doj,
                    e.doe,
                    e.exit_date,
                    e.workshift_id,
                    e.workshift_hours,
                    e.workshift_in_time,
                    e.workshift_out_time,
                    e.week_off_code,
                    e.salary_type,
                    e.salary,
                    e.allowance,
                    c.fname,
                    c.mname,
                    c.lname,
                    c.email,
                    c.mobile,
                    c.country_code,
                    c.bid,
                    CONCAT_WS(' ', NULLIF(TRIM(c.fname), ''), NULLIF(TRIM(c.mname), ''), NULLIF(TRIM(c.lname), '')) AS full_name
            """,
            q=q,
            status=status,
            department_id=department_id,
            position_id=position_id,
            order_by="""
                ORDER BY
                    CASE WHEN e.status = 1 THEN 0 ELSE 1 END,
                    full_name ASC,
                    e.id ASC
            """,
            limit=limit,
            offset=offset,
        )
        result = await db.execute(text(query), params)
        return [dict(row._mapping) for row in result.fetchall()]

    async def get_employee(self, db: AsyncSession, employee_id: int) -> dict[str, Any] | None:
        result = await db.execute(
            text(
                """
                SELECT
                    e.id AS employee_id,
                    e.contact_id,
                    e.ecode,
                    e.department_id,
                    e.position_id,
                    e.status,
                    e.user_account,
                    e.is_admin,
                    e.type AS employee_type,
                    e.doj,
                    e.doe,
                    e.exit_date,
                    e.workshift_id,
                    e.workshift_hours,
                    e.workshift_in_time,
                    e.workshift_out_time,
                    e.week_off_code,
                    e.salary_type,
                    e.salary,
                    e.allowance,
                    e.grade,
                    e.interviewer,
                    e.notice_start_date,
                    e.on_notice,
                    e.auto_assign_inq,
                    e.associate,
                    e.qualifier,
                    c.fname,
                    c.mname,
                    c.lname,
                    c.email,
                    c.personal_email,
                    c.mobile,
                    c.country_code,
                    c.bid,
                    c.gender,
                    c.address,
                    c.city,
                    c.state,
                    c.country,
                    c.pincode,
                    CONCAT_WS(' ', NULLIF(TRIM(c.fname), ''), NULLIF(TRIM(c.mname), ''), NULLIF(TRIM(c.lname), '')) AS full_name
                FROM employee e
                LEFT JOIN contact c ON c.id = e.contact_id
                WHERE e.id = :employee_id
                  AND (e.park IS NULL OR e.park = 0)
                LIMIT 1
                """
            ),
            {"employee_id": int(employee_id)},
        )
        row = result.fetchone()
        return dict(row._mapping) if row else None

    async def list_correction_requests(
        self,
        db: AsyncSession,
        *,
        employee_id: int,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        columns = await self._get_table_columns(db, "emp_att_request")
        if not columns:
            return []

        id_column = self._first_matching_column(columns, ["id", "request_id", "att_request_id"])
        employee_ref_column = self._first_matching_column(columns, ["employee_id", "emp_id", "contact_id"])
        start_column = self._first_matching_column(columns, ["start_date", "from_date", "leave_from"])
        end_column = self._first_matching_column(columns, ["end_date", "to_date", "leave_to"])
        status_column = self._first_matching_column(columns, ["status", "request_status", "leave_status", "approval_status"])
        request_type_column = self._first_matching_column(columns, ["request_type", "type", "req_type", "leave_type"])

        if not all([id_column, employee_ref_column, start_column, end_column]):
            return []

        employee_lookup = await db.execute(
            text("SELECT contact_id FROM employee WHERE id = :employee_id LIMIT 1"),
            {"employee_id": int(employee_id)},
        )
        employee_row = employee_lookup.fetchone()
        if employee_row is None:
            return []

        employee_ref_value = int(employee_id)
        if employee_ref_column == "contact_id":
            contact_id = employee_row._mapping.get("contact_id")
            if contact_id is None:
                return []
            employee_ref_value = int(contact_id)

        status_select = f"l.{status_column} AS status" if status_column else "NULL AS status"
        request_type_select = f"l.{request_type_column} AS request_type" if request_type_column else "NULL AS request_type"

        sql = f"""
            SELECT
                l.{id_column} AS request_id,
                l.{start_column} AS start_date,
                l.{end_column} AS end_date,
                {status_select},
                {request_type_select}
            FROM emp_att_request l
            WHERE l.{employee_ref_column} = :employee_ref
              AND l.{start_column} <= :to_date
              AND l.{end_column} >= :from_date
            ORDER BY l.{start_column} ASC, l.{id_column} ASC
        """
        result = await db.execute(
            text(sql),
            {
                "employee_ref": employee_ref_value,
                "from_date": from_date,
                "to_date": to_date,
            },
        )
        return [dict(row._mapping) for row in result.fetchall()]

    async def resolve_contact_id_for_employee(
        self,
        db: AsyncSession,
        employee_id: int,
    ) -> int | None:
        result = await db.execute(
            text(
                """
                SELECT contact_id
                FROM employee
                WHERE id = :employee_id
                  AND (park IS NULL OR park = 0)
                LIMIT 1
                """
            ),
            {"employee_id": int(employee_id)},
        )
        row = result.fetchone()
        if row is None:
            return None
        contact_id = row._mapping.get("contact_id")
        return int(contact_id) if contact_id is not None else None

    async def count_attendance_records(
        self,
        db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        status: int | None,
        regularised: int | None,
        invalid: int | None,
        department_id: int | None = None,
    ) -> int:
        sql, params = await self._attendance_records_query(
            db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            status=status,
            regularised=regularised,
            invalid=invalid,
            department_id=department_id,
            select_sql="SELECT COUNT(*) AS total",
        )
        result = await db.execute(text(sql), params)
        row = result.fetchone()
        return int(row._mapping["total"]) if row else 0

    async def list_attendance_records(
        self,
        db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        status: int | None,
        regularised: int | None,
        invalid: int | None,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        sql, params = await self._attendance_records_query(
            db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            status=status,
            regularised=regularised,
            invalid=invalid,
            department_id=None,
            select_sql="""
                SELECT
                    a.id,
                    e.id AS employee_id,
                    a.contact_id,
                    a.date,
                    a.ip_address,
                    a.logout_ip_address,
                    a.mac_address,
                    a.login_bssid,
                    a.logout_bssid,
                    a.login_wifi_details,
                    a.logout_wifi_details,
                    a.login_details,
                    a.logout_details,
                    a.in_time,
                    a.out_time,
                    a.comment,
                    a.status,
                    a.regularised,
                    a.regularised_type_id,
                    a.invalid,
                    a.park,
                    a.created_by,
                    a.created_at,
                    a.modified_by,
                    a.modified_at,
                    c.fname,
                    c.mname,
                    c.lname,
                    c.email,
                    c.mobile,
                    CONCAT_WS(' ', NULLIF(TRIM(c.fname), ''), NULLIF(TRIM(c.mname), ''), NULLIF(TRIM(c.lname), '')) AS full_name
            """,
        )
        sql += """
            ORDER BY a.date DESC, a.in_time DESC, a.id DESC
            LIMIT :limit OFFSET :offset
        """
        params["limit"] = int(limit)
        params["offset"] = int(offset)
        result = await db.execute(text(sql), params)
        return [dict(row._mapping) for row in result.fetchall()]

    async def get_attendance_record(self, db: AsyncSession, record_id: int) -> dict[str, Any] | None:
        result = await db.execute(
            text(
                """
                SELECT
                    a.id,
                    e.id AS employee_id,
                    a.contact_id,
                    a.date,
                    a.ip_address,
                    a.logout_ip_address,
                    a.mac_address,
                    a.login_bssid,
                    a.logout_bssid,
                    a.login_wifi_details,
                    a.logout_wifi_details,
                    a.login_details,
                    a.logout_details,
                    a.in_time,
                    a.out_time,
                    a.comment,
                    a.status,
                    a.regularised,
                    a.regularised_type_id,
                    a.invalid,
                    a.park,
                    a.created_by,
                    a.created_at,
                    a.modified_by,
                    a.modified_at,
                    c.fname,
                    c.mname,
                    c.lname,
                    c.email,
                    c.mobile,
                    CONCAT_WS(' ', NULLIF(TRIM(c.fname), ''), NULLIF(TRIM(c.mname), ''), NULLIF(TRIM(c.lname), '')) AS full_name
                FROM emp_attendance a
                LEFT JOIN employee e ON e.contact_id = a.contact_id AND (e.park IS NULL OR e.park = 0)
                LEFT JOIN contact c ON c.id = a.contact_id
                WHERE a.id = :record_id
                LIMIT 1
                """
            ),
            {"record_id": int(record_id)},
        )
        row = result.fetchone()
        return dict(row._mapping) if row else None

    async def update_attendance_record(
        self,
        db: AsyncSession,
        *,
        record_id: int,
        payload: dict[str, Any],
        modified_by: int,
    ) -> None:
        allowed_fields = {
            "contact_id",
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
            "status",
            "regularised",
            "regularised_type_id",
            "invalid",
            "park",
        }
        assignments: list[str] = []
        params: dict[str, Any] = {"record_id": int(record_id), "modified_by": int(modified_by)}
        for field, value in payload.items():
            if field not in allowed_fields:
                continue
            assignments.append(f"{field} = :{field}")
            params[field] = value
        if not assignments:
            return
        assignments.append("modified_by = :modified_by")
        await db.execute(
            text(
                f"""
                UPDATE emp_attendance
                SET {", ".join(assignments)}
                WHERE id = :record_id
                """
            ),
            params,
        )

    async def create_attendance_record(
        self,
        db: AsyncSession,
        *,
        payload: dict[str, Any],
        created_by: int,
    ) -> int:
        params = {
            "contact_id": int(payload.get("contact_id", 0)),
            "date": payload.get("date", "1970-01-01"),
            "ip_address": payload.get("ip_address", ""),
            "logout_ip_address": payload.get("logout_ip_address", ""),
            "mac_address": payload.get("mac_address", ""),
            "login_bssid": payload.get("login_bssid", ""),
            "logout_bssid": payload.get("logout_bssid", ""),
            "login_wifi_details": payload.get("login_wifi_details", ""),
            "logout_wifi_details": payload.get("logout_wifi_details", ""),
            "login_details": payload.get("login_details", ""),
            "logout_details": payload.get("logout_details", ""),
            "in_time": payload.get("in_time", "1970-01-01 00:00:00"),
            "out_time": payload.get("out_time", "1970-01-01 00:00:00"),
            "comment": payload.get("comment", "Regular"),
            "status": int(payload.get("status", 0)),
            "regularised": int(payload.get("regularised", 0)),
            "regularised_type_id": int(payload.get("regularised_type_id", 0)),
            "invalid": int(payload.get("invalid", 0)),
            "park": int(payload.get("park", 0)),
            "created_by": int(created_by),
            "modified_by": int(created_by),
        }
        result = await db.execute(
            text(
                """
                INSERT INTO emp_attendance (
                    contact_id,
                    date,
                    ip_address,
                    logout_ip_address,
                    mac_address,
                    login_bssid,
                    logout_bssid,
                    login_wifi_details,
                    logout_wifi_details,
                    login_details,
                    logout_details,
                    in_time,
                    out_time,
                    comment,
                    status,
                    regularised,
                    regularised_type_id,
                    invalid,
                    park,
                    created_by,
                    modified_by
                ) VALUES (
                    :contact_id,
                    :date,
                    :ip_address,
                    :logout_ip_address,
                    :mac_address,
                    :login_bssid,
                    :logout_bssid,
                    :login_wifi_details,
                    :logout_wifi_details,
                    :login_details,
                    :logout_details,
                    :in_time,
                    :out_time,
                    :comment,
                    :status,
                    :regularised,
                    :regularised_type_id,
                    :invalid,
                    :park,
                    :created_by,
                    :modified_by
                )
                """
            ),
            params,
        )
        inserted_id = getattr(result, "lastrowid", None)
        return int(inserted_id or 0)

    async def count_attendance_requests(
        self,
        db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        status: int | None,
        request_type: int | None,
        department_id: int | None = None,
    ) -> int:
        sql, params = self._attendance_requests_query(
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            status=status,
            request_type=request_type,
            department_id=department_id,
            select_sql="SELECT COUNT(*) AS total",
        )
        result = await db.execute(text(sql), params)
        row = result.fetchone()
        return int(row._mapping["total"]) if row else 0

    async def list_attendance_requests(
        self,
        db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        status: int | None,
        request_type: int | None,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        sql, params = self._attendance_requests_query(
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            status=status,
            request_type=request_type,
            department_id=None,
            select_sql="""
                SELECT
                    r.id,
                    r.date,
                    r.action_date,
                    r.emp_id,
                    r.parent_id,
                    r.request_type,
                    r.no_of_days,
                    r.start_date,
                    r.in_time,
                    r.end_date,
                    r.out_time,
                    r.status,
                    r.request_comment,
                    r.parent_comment,
                    r.bid,
                    r.park,
                    r.created_by,
                    r.created_at,
                    r.modified_by,
                    r.modified_at,
                    e.contact_id,
                    c.fname,
                    c.mname,
                    c.lname,
                    c.email,
                    c.mobile,
                    CONCAT_WS(' ', NULLIF(TRIM(c.fname), ''), NULLIF(TRIM(c.mname), ''), NULLIF(TRIM(c.lname), '')) AS full_name
            """,
        )
        sql += """
            ORDER BY r.start_date DESC, r.id DESC
            LIMIT :limit OFFSET :offset
        """
        params["limit"] = int(limit)
        params["offset"] = int(offset)
        result = await db.execute(text(sql), params)
        return [dict(row._mapping) for row in result.fetchall()]

    async def get_attendance_request(self, db: AsyncSession, request_id: int) -> dict[str, Any] | None:
        result = await db.execute(
            text(
                """
                SELECT
                    r.id,
                    r.date,
                    r.action_date,
                    r.emp_id,
                    r.parent_id,
                    r.request_type,
                    r.no_of_days,
                    r.start_date,
                    r.in_time,
                    r.end_date,
                    r.out_time,
                    r.status,
                    r.request_comment,
                    r.parent_comment,
                    r.bid,
                    r.park,
                    r.created_by,
                    r.created_at,
                    r.modified_by,
                    r.modified_at,
                    e.contact_id,
                    c.fname,
                    c.mname,
                    c.lname,
                    c.email,
                    c.mobile,
                    CONCAT_WS(' ', NULLIF(TRIM(c.fname), ''), NULLIF(TRIM(c.mname), ''), NULLIF(TRIM(c.lname), '')) AS full_name
                FROM emp_att_request r
                LEFT JOIN employee e ON e.id = r.emp_id AND (e.park IS NULL OR e.park = 0)
                LEFT JOIN contact c ON c.id = e.contact_id
                WHERE r.id = :request_id
                LIMIT 1
                """
            ),
            {"request_id": int(request_id)},
        )
        row = result.fetchone()
        return dict(row._mapping) if row else None

    async def update_attendance_request(
        self,
        db: AsyncSession,
        *,
        request_id: int,
        payload: dict[str, Any],
        modified_by: int,
    ) -> None:
        allowed_fields = {
            "emp_id",
            "parent_id",
            "date",
            "action_date",
            "request_type",
            "no_of_days",
            "start_date",
            "in_time",
            "end_date",
            "out_time",
            "status",
            "request_comment",
            "parent_comment",
            "bid",
            "park",
        }
        assignments: list[str] = []
        params: dict[str, Any] = {"request_id": int(request_id), "modified_by": int(modified_by)}
        for field, value in payload.items():
            if field not in allowed_fields:
                continue
            assignments.append(f"{field} = :{field}")
            params[field] = value
        if not assignments:
            return
        assignments.append("modified_by = :modified_by")
        await db.execute(
            text(
                f"""
                UPDATE emp_att_request
                SET {", ".join(assignments)}
                WHERE id = :request_id
                """
            ),
            params,
        )

    async def create_attendance_request(
        self,
        db: AsyncSession,
        *,
        payload: dict[str, Any],
        created_by: int,
    ) -> int:
        params = {
            "emp_id": int(payload.get("emp_id", 0)),
            "parent_id": int(payload.get("parent_id", 0)),
            "date": payload.get("date", "1970-01-01"),
            "action_date": payload.get("action_date", "1970-01-01"),
            "request_type": int(payload.get("request_type", 0)),
            "no_of_days": payload.get("no_of_days", ""),
            "start_date": payload.get("start_date", "1970-01-01"),
            "in_time": payload.get("in_time", "00:00:00"),
            "end_date": payload.get("end_date", "1970-01-01"),
            "out_time": payload.get("out_time", "00:00:00"),
            "status": int(payload.get("status", 0)),
            "request_comment": payload.get("request_comment", ""),
            "parent_comment": payload.get("parent_comment", ""),
            "bid": int(payload.get("bid", 0)),
            "park": int(payload.get("park", 0)),
            "created_by": int(created_by),
            "modified_by": int(created_by),
        }
        result = await db.execute(
            text(
                """
                INSERT INTO emp_att_request (
                    date,
                    action_date,
                    emp_id,
                    parent_id,
                    request_type,
                    no_of_days,
                    start_date,
                    in_time,
                    end_date,
                    out_time,
                    status,
                    request_comment,
                    parent_comment,
                    bid,
                    park,
                    created_by,
                    modified_by
                ) VALUES (
                    :date,
                    :action_date,
                    :emp_id,
                    :parent_id,
                    :request_type,
                    :no_of_days,
                    :start_date,
                    :in_time,
                    :end_date,
                    :out_time,
                    :status,
                    :request_comment,
                    :parent_comment,
                    :bid,
                    :park,
                    :created_by,
                    :modified_by
                )
                """
            ),
            params,
        )
        inserted_id = getattr(result, "lastrowid", None)
        return int(inserted_id or 0)

    async def _attendance_records_query(
        self,
        db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        status: int | None,
        regularised: int | None,
        invalid: int | None,
        department_id: int | None,
        select_sql: str,
    ) -> tuple[str, dict[str, Any]]:
        sql = f"""
            {select_sql}
            FROM emp_attendance a
            LEFT JOIN employee e ON e.contact_id = a.contact_id AND (e.park IS NULL OR e.park = 0)
            LEFT JOIN contact c ON c.id = a.contact_id
            WHERE 1 = 1
        """
        params: dict[str, Any] = {}
        if employee_id is not None:
            contact_id = await self.resolve_contact_id_for_employee(db, employee_id)
            if contact_id is None:
                sql += " AND 1 = 0"
                return sql, params
            sql += " AND a.contact_id = :contact_id"
            params["contact_id"] = int(contact_id)
        if from_date:
            sql += " AND a.date >= :from_date"
            params["from_date"] = from_date
        if to_date:
            sql += " AND a.date <= :to_date"
            params["to_date"] = to_date
        if status is not None:
            sql += " AND a.status = :status"
            params["status"] = int(status)
        if regularised is not None:
            sql += " AND a.regularised = :regularised"
            params["regularised"] = int(regularised)
        if invalid is not None:
            sql += " AND a.invalid = :invalid"
            params["invalid"] = int(invalid)
        if department_id is not None:
            sql += " AND e.department_id = :department_id"
            params["department_id"] = int(department_id)
        return sql, params

    def _attendance_requests_query(
        self,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        status: int | None,
        request_type: int | None,
        department_id: int | None,
        select_sql: str,
    ) -> tuple[str, dict[str, Any]]:
        sql = f"""
            {select_sql}
            FROM emp_att_request r
            LEFT JOIN employee e ON e.id = r.emp_id AND (e.park IS NULL OR e.park = 0)
            LEFT JOIN contact c ON c.id = e.contact_id
            WHERE 1 = 1
        """
        params: dict[str, Any] = {}
        if employee_id is not None:
            sql += " AND r.emp_id = :employee_id"
            params["employee_id"] = int(employee_id)
        if from_date and to_date:
            sql += " AND r.start_date <= :to_date AND r.end_date >= :from_date"
            params["from_date"] = from_date
            params["to_date"] = to_date
        elif from_date:
            sql += " AND r.start_date >= :from_date"
            params["from_date"] = from_date
        elif to_date:
            sql += " AND r.end_date <= :to_date"
            params["to_date"] = to_date
        if status is not None:
            sql += " AND r.status = :status"
            params["status"] = int(status)
        if request_type is not None:
            sql += " AND r.request_type = :request_type"
            params["request_type"] = int(request_type)
        if department_id is not None:
            sql += " AND e.department_id = :department_id"
            params["department_id"] = int(department_id)
        return sql, params

    async def count_attendance_ready_employees(
        self,
        db: AsyncSession,
        *,
        department_id: int | None = None,
    ) -> int:
        sql = """
            SELECT COUNT(*) AS total
            FROM employee e
            WHERE (e.park IS NULL OR e.park = 0)
              AND e.status = 1
              AND (
                    (e.workshift_id IS NOT NULL AND e.workshift_id > 0)
                    OR (
                        NULLIF(TRIM(CAST(e.workshift_in_time AS CHAR)), '') IS NOT NULL
                        AND NULLIF(TRIM(CAST(e.workshift_out_time AS CHAR)), '') IS NOT NULL
                    )
              )
        """
        params: dict[str, Any] = {}
        if department_id is not None:
            sql += " AND e.department_id = :department_id"
            params["department_id"] = int(department_id)
        result = await db.execute(text(sql), params)
        row = result.fetchone()
        return int(row._mapping["total"]) if row else 0

    def _employee_base_query(
        self,
        *,
        select_sql: str,
        q: str | None,
        status: int | None,
        department_id: int | None,
        position_id: int | None,
        order_by: str = "",
        limit: int | None = None,
        offset: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        sql = f"""
            {select_sql}
            FROM employee e
            LEFT JOIN contact c ON c.id = e.contact_id
            WHERE (e.park IS NULL OR e.park = 0)
        """
        params: dict[str, Any] = {}
        if q:
            sql += """
                AND (
                    c.fname LIKE :q
                    OR c.mname LIKE :q
                    OR c.lname LIKE :q
                    OR c.email LIKE :q
                    OR c.mobile LIKE :q
                    OR CAST(e.ecode AS CHAR) LIKE :q
                )
            """
            params["q"] = f"%{q.strip()}%"
        if status is not None:
            sql += " AND e.status = :status"
            params["status"] = int(status)
        if department_id is not None:
            sql += " AND e.department_id = :department_id"
            params["department_id"] = int(department_id)
        if position_id is not None:
            sql += " AND e.position_id = :position_id"
            params["position_id"] = int(position_id)
        if order_by:
            sql += f" {order_by}"
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = int(limit)
        if offset is not None:
            sql += " OFFSET :offset"
            params["offset"] = int(offset)
        return sql, params

    async def _get_table_columns(self, db: AsyncSession, table_name: str) -> set[str]:
        result = await db.execute(
            text(
                """
                SELECT COLUMN_NAME
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = :table_name
                """
            ),
            {"table_name": table_name},
        )
        return {str(row._mapping["COLUMN_NAME"]) for row in result.fetchall()}

    @staticmethod
    def _first_matching_column(columns: set[str], candidates: list[str]) -> str | None:
        for candidate in candidates:
            if candidate in columns:
                return candidate
        return None
