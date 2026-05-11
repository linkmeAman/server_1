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
                    ep.parent_emp_id AS parent_id,
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
                    e.calculate_salary,
                    e.is_parent,
                    e.demo_owner,
                    e.cash_collector,
                    e.tds_type,
                    e.tds_percent,
                    e.rate_multiplier,
                    e.incentive_new,
                    e.incentive_renew,
                    e.p_incentive_c,
                    e.p_incentive_sc,
                    e.trainer_incentive,
                    e.mt_incentive,
                    c.fname,
                    c.mname,
                    c.lname,
                    c.email,
                    c.personal_email,
                    c.mobile,
                    c.country_code,
                    c.mobile2,
                    c.country_code_2,
                    c.phone_no,
                    c.bid,
                    ep.parent_emp_id AS parent_id,
                    c.gender,
                    c.dob,
                    c.address,
                    c.city,
                    c.state,
                    c.country,
                    c.pincode,
                    c.relation,
                    c.document_type_id,
                    c.document_number,
                    c.document_image,
                    c.document_type_id_2,
                    c.document_number_2,
                    c.document_image_2,
                    c.document_type_id_3,
                    c.document_image_3,
                    NULL AS ename,
                    NULL AS emobile,
                    NULL AS ecountry_code,
                    CONCAT_WS(' ', NULLIF(TRIM(c.fname), ''), NULLIF(TRIM(c.mname), ''), NULLIF(TRIM(c.lname), '')) AS full_name
                FROM employee e
                LEFT JOIN contact c ON c.id = e.contact_id
                LEFT JOIN (
                    SELECT emp_id, MIN(parent_emp_id) AS parent_emp_id
                    FROM employee_parent
                    WHERE (park IS NULL OR park = 0)
                    GROUP BY emp_id
                ) ep ON ep.emp_id = e.id
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

    async def bulk_update_attendance_request_status(
        self,
        db: AsyncSession,
        *,
        request_ids: list[int],
        status: int,
        modified_by: int,
    ) -> int:
        if not request_ids:
            return 0
        id_placeholders: list[str] = []
        params: dict[str, Any] = {
            "status": int(status),
            "modified_by": int(modified_by),
        }
        for index, request_id in enumerate(request_ids):
            key = f"request_id_{index}"
            id_placeholders.append(f":{key}")
            params[key] = int(request_id)
        sql = f"""
            UPDATE emp_att_request
            SET status = :status,
                modified_by = :modified_by
            WHERE id IN ({", ".join(id_placeholders)})
        """
        result = await db.execute(text(sql), params)
        return int(getattr(result, "rowcount", 0) or 0)

    async def count_attendance_leaves(
        self,
        db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        category: int | None,
        expired: int | None,
        park: int | None,
    ) -> int:
        sql, params = self._attendance_leaves_query(
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            category=category,
            expired=expired,
            park=park,
            select_sql="SELECT COUNT(*) AS total",
        )
        result = await db.execute(text(sql), params)
        row = result.fetchone()
        return int(row._mapping["total"]) if row else 0

    async def list_attendance_leaves(
        self,
        db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        category: int | None,
        expired: int | None,
        park: int | None,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        sql, params = self._attendance_leaves_query(
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            category=category,
            expired=expired,
            park=park,
            select_sql="""
                SELECT
                    lb.id,
                    lb.contact_id,
                    lb.emp_id,
                    lb.emp_code,
                    lb.doi,
                    lb.doc,
                    lb.doe,
                    lb.earned,
                    lb.category,
                    lb.day_code,
                    lb.consumed,
                    lb.expired,
                    lb.park,
                    lb.created_at,
                    lb.modified_at,
                    c.fname,
                    c.mname,
                    c.lname,
                    c.email,
                    c.mobile,
                    CONCAT_WS(' ', NULLIF(TRIM(c.fname), ''), NULLIF(TRIM(c.mname), ''), NULLIF(TRIM(c.lname), '')) AS full_name
            """,
        )
        sql += """
            ORDER BY lb.doc DESC, lb.id DESC
            LIMIT :limit OFFSET :offset
        """
        params["limit"] = int(limit)
        params["offset"] = int(offset)
        result = await db.execute(text(sql), params)
        return [dict(row._mapping) for row in result.fetchall()]

    async def get_attendance_leave(self, db: AsyncSession, leave_id: int) -> dict[str, Any] | None:
        result = await db.execute(
            text(
                """
                SELECT
                    lb.id,
                    lb.contact_id,
                    lb.emp_id,
                    lb.emp_code,
                    lb.doi,
                    lb.doc,
                    lb.doe,
                    lb.earned,
                    lb.category,
                    lb.day_code,
                    lb.consumed,
                    lb.expired,
                    lb.park,
                    lb.created_at,
                    lb.modified_at,
                    c.fname,
                    c.mname,
                    c.lname,
                    c.email,
                    c.mobile,
                    CONCAT_WS(' ', NULLIF(TRIM(c.fname), ''), NULLIF(TRIM(c.mname), ''), NULLIF(TRIM(c.lname), '')) AS full_name
                FROM leave_bucket lb
                LEFT JOIN employee e ON e.id = lb.emp_id AND (e.park IS NULL OR e.park = 0)
                LEFT JOIN contact c ON c.id = COALESCE(lb.contact_id, e.contact_id)
                WHERE lb.id = :leave_id
                LIMIT 1
                """
            ),
            {"leave_id": int(leave_id)},
        )
        row = result.fetchone()
        return dict(row._mapping) if row else None

    async def update_attendance_leave(
        self,
        db: AsyncSession,
        *,
        leave_id: int,
        payload: dict[str, Any],
    ) -> None:
        allowed_fields = {
            "contact_id",
            "emp_id",
            "emp_code",
            "doi",
            "doc",
            "doe",
            "earned",
            "category",
            "day_code",
            "consumed",
            "expired",
            "park",
        }
        assignments: list[str] = []
        params: dict[str, Any] = {"leave_id": int(leave_id)}
        for field, value in payload.items():
            if field not in allowed_fields:
                continue
            assignments.append(f"{field} = :{field}")
            params[field] = value
        if not assignments:
            return
        await db.execute(
            text(
                f"""
                UPDATE leave_bucket
                SET {", ".join(assignments)}
                WHERE id = :leave_id
                """
            ),
            params,
        )

    async def create_attendance_leave(
        self,
        db: AsyncSession,
        *,
        payload: dict[str, Any],
    ) -> int:
        params = {
            "contact_id": int(payload.get("contact_id", 0)),
            "emp_id": int(payload.get("emp_id", 0)),
            "emp_code": int(payload.get("emp_code", 0)),
            "doi": payload.get("doi", "1970-01-01"),
            "doc": payload.get("doc", "1970-01-01"),
            "doe": payload.get("doe", "1970-01-01"),
            "earned": float(payload.get("earned", 0)),
            "category": int(payload.get("category", 1)),
            "day_code": int(payload.get("day_code", 7)),
            "consumed": int(payload.get("consumed", 0)),
            "expired": int(payload.get("expired", 0)),
            "park": int(payload.get("park", 0)),
        }
        result = await db.execute(
            text(
                """
                INSERT INTO leave_bucket (
                    contact_id,
                    emp_id,
                    emp_code,
                    doi,
                    doc,
                    doe,
                    earned,
                    category,
                    day_code,
                    consumed,
                    expired,
                    park
                ) VALUES (
                    :contact_id,
                    :emp_id,
                    :emp_code,
                    :doi,
                    :doc,
                    :doe,
                    :earned,
                    :category,
                    :day_code,
                    :consumed,
                    :expired,
                    :park
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

    def _attendance_leaves_query(
        self,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        category: int | None,
        expired: int | None,
        park: int | None,
        select_sql: str,
    ) -> tuple[str, dict[str, Any]]:
        sql = f"""
            {select_sql}
            FROM leave_bucket lb
            LEFT JOIN employee e ON e.id = lb.emp_id AND (e.park IS NULL OR e.park = 0)
            LEFT JOIN contact c ON c.id = COALESCE(lb.contact_id, e.contact_id)
            WHERE 1 = 1
        """
        params: dict[str, Any] = {}
        if employee_id is not None:
            sql += " AND lb.emp_id = :employee_id"
            params["employee_id"] = int(employee_id)
        if from_date and to_date:
            sql += " AND lb.doc <= :to_date AND lb.doe >= :from_date"
            params["from_date"] = from_date
            params["to_date"] = to_date
        elif from_date:
            sql += " AND lb.doe >= :from_date"
            params["from_date"] = from_date
        elif to_date:
            sql += " AND lb.doc <= :to_date"
            params["to_date"] = to_date
        if category is not None:
            sql += " AND lb.category = :category"
            params["category"] = int(category)
        if expired is not None:
            sql += " AND lb.expired = :expired"
            params["expired"] = int(expired)
        if park is not None:
            sql += " AND lb.park = :park"
            params["park"] = int(park)
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
            LEFT JOIN (
                SELECT emp_id, MIN(parent_emp_id) AS parent_emp_id
                FROM employee_parent
                WHERE (park IS NULL OR park = 0)
                GROUP BY emp_id
            ) ep ON ep.emp_id = e.id
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

    async def count_payroll_records(
        self,
        db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        paid: int | None = None,
        park: int | None = None,
        paid_nonzero: bool = False,
    ) -> int:
        sql, params = await self._payroll_records_query(
            db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            paid=paid,
            park=park,
            select_sql="SELECT COUNT(*) AS total",
            paid_nonzero=paid_nonzero,
        )
        result = await db.execute(text(sql), params)
        row = result.fetchone()
        return int(row._mapping["total"]) if row else 0

    async def sum_payroll_salary(
        self,
        db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
    ) -> int:
        sql, params = await self._payroll_records_query(
            db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            paid=None,
            park=0,
            select_sql="SELECT COALESCE(SUM(s.salary), 0) AS total",
        )
        result = await db.execute(text(sql), params)
        row = result.fetchone()
        return int(row._mapping["total"]) if row else 0

    async def sum_payroll_paid(
        self,
        db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
    ) -> int:
        sql, params = await self._payroll_records_query(
            db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            paid=None,
            park=0,
            select_sql="SELECT COALESCE(SUM(s.paid), 0) AS total",
        )
        result = await db.execute(text(sql), params)
        row = result.fetchone()
        return int(row._mapping["total"]) if row else 0

    async def list_payroll_records(
        self,
        db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        paid: int | None,
        park: int | None,
        limit: int,
        offset: int,
        paid_nonzero: bool = False,
    ) -> list[dict[str, Any]]:
        sql, params = await self._payroll_records_query(
            db,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            paid=paid,
            park=park,
            paid_nonzero=paid_nonzero,
            select_sql="""
                SELECT
                    s.id,
                    e.id AS employee_id,
                    s.contact_id,
                    s.from_date,
                    s.to_date,
                    s.working_days,
                    s.present_days,
                    s.absent_days,
                    s.wo_days,
                    s.par_day,
                    s.total_leaves,
                    s.paid_leave,
                    s.unpaid_leave,
                    s.leave_balance_before,
                    s.leave_balance_after,
                    s.tax_amount,
                    s.tax_comment,
                    s.advance_amount,
                    s.advance_comment,
                    s.fine_amount,
                    s.fine_comment,
                    s.base_amount,
                    s.incentive,
                    s.incentive_comment,
                    s.deduction,
                    s.deduction_comment,
                    s.addition,
                    s.addition_comment,
                    s.extra_amount,
                    s.extra_comment,
                    s.allowance,
                    s.pay_mode,
                    s.sub_total,
                    s.salary,
                    s.paid,
                    s.bid,
                    s.comments,
                    s.paid_holidays,
                    s.paid_holidays_dates,
                    s.leaves,
                    s.leaves_dates,
                    s.wfh,
                    s.wfh_dates,
                    s.half_day,
                    s.half_day_dates,
                    s.optional_holiday,
                    s.optional_holiday_dates,
                    s.punch_inout,
                    s.punch_inout_dates,
                    s.`break`,
                    s.break_dates,
                    s.supplementary,
                    s.supplementary_dates,
                    s.park,
                    s.response_data,
                    s.response_data_app,
                    s.pay_slip_data,
                    s.incentive_html,
                    s.year_month,
                    s.created_at,
                    s.created_by,
                    s.modified_at,
                    s.modified_by,
                    c.fname,
                    c.mname,
                    c.lname,
                    c.email,
                    c.mobile,
                    CONCAT_WS(' ', NULLIF(TRIM(c.fname), ''), NULLIF(TRIM(c.mname), ''), NULLIF(TRIM(c.lname), '')) AS full_name
            """,
        )
        sql += """
            ORDER BY s.from_date DESC, s.id DESC
            LIMIT :limit OFFSET :offset
        """
        params["limit"] = int(limit)
        params["offset"] = int(offset)
        result = await db.execute(text(sql), params)
        return [dict(row._mapping) for row in result.fetchall()]

    async def get_payroll_record(self, db: AsyncSession, record_id: int) -> dict[str, Any] | None:
        result = await db.execute(
            text(
                """
                SELECT
                    s.id,
                    e.id AS employee_id,
                    s.contact_id,
                    s.from_date,
                    s.to_date,
                    s.working_days,
                    s.present_days,
                    s.absent_days,
                    s.wo_days,
                    s.par_day,
                    s.total_leaves,
                    s.paid_leave,
                    s.unpaid_leave,
                    s.leave_balance_before,
                    s.leave_balance_after,
                    s.tax_amount,
                    s.tax_comment,
                    s.advance_amount,
                    s.advance_comment,
                    s.fine_amount,
                    s.fine_comment,
                    s.base_amount,
                    s.incentive,
                    s.incentive_comment,
                    s.deduction,
                    s.deduction_comment,
                    s.addition,
                    s.addition_comment,
                    s.extra_amount,
                    s.extra_comment,
                    s.allowance,
                    s.pay_mode,
                    s.sub_total,
                    s.salary,
                    s.paid,
                    s.bid,
                    s.comments,
                    s.paid_holidays,
                    s.paid_holidays_dates,
                    s.leaves,
                    s.leaves_dates,
                    s.wfh,
                    s.wfh_dates,
                    s.half_day,
                    s.half_day_dates,
                    s.optional_holiday,
                    s.optional_holiday_dates,
                    s.punch_inout,
                    s.punch_inout_dates,
                    s.`break`,
                    s.break_dates,
                    s.supplementary,
                    s.supplementary_dates,
                    s.park,
                    s.response_data,
                    s.response_data_app,
                    s.pay_slip_data,
                    s.incentive_html,
                    s.year_month,
                    s.created_at,
                    s.created_by,
                    s.modified_at,
                    s.modified_by,
                    c.fname,
                    c.mname,
                    c.lname,
                    c.email,
                    c.mobile,
                    CONCAT_WS(' ', NULLIF(TRIM(c.fname), ''), NULLIF(TRIM(c.mname), ''), NULLIF(TRIM(c.lname), '')) AS full_name
                FROM salary s
                LEFT JOIN employee e ON e.contact_id = s.contact_id AND (e.park IS NULL OR e.park = 0)
                LEFT JOIN contact c ON c.id = s.contact_id
                WHERE s.id = :record_id
                LIMIT 1
                """
            ),
            {"record_id": int(record_id)},
        )
        row = result.fetchone()
        return dict(row._mapping) if row else None

    async def update_payroll_record(
        self,
        db: AsyncSession,
        *,
        record_id: int,
        payload: dict[str, Any],
        modified_by: int,
    ) -> None:
        allowed_fields = {
            "contact_id",
            "from_date",
            "to_date",
            "working_days",
            "present_days",
            "absent_days",
            "wo_days",
            "par_day",
            "total_leaves",
            "paid_leave",
            "unpaid_leave",
            "leave_balance_before",
            "leave_balance_after",
            "tax_amount",
            "tax_comment",
            "advance_amount",
            "advance_comment",
            "fine_amount",
            "fine_comment",
            "base_amount",
            "incentive",
            "incentive_comment",
            "deduction",
            "deduction_comment",
            "addition",
            "addition_comment",
            "extra_amount",
            "extra_comment",
            "allowance",
            "pay_mode",
            "sub_total",
            "salary",
            "paid",
            "bid",
            "comments",
            "paid_holidays",
            "paid_holidays_dates",
            "leaves",
            "leaves_dates",
            "wfh",
            "wfh_dates",
            "half_day",
            "half_day_dates",
            "optional_holiday",
            "optional_holiday_dates",
            "punch_inout",
            "punch_inout_dates",
            "break",
            "break_dates",
            "supplementary",
            "supplementary_dates",
            "park",
            "response_data",
            "response_data_app",
            "pay_slip_data",
            "incentive_html",
            "year_month",
            "modified_at",
            "modified_by",
        }
        json_fields = {"response_data", "response_data_app", "pay_slip_data"}
        assignments: list[str] = []
        params: dict[str, Any] = {"record_id": int(record_id), "modified_by": int(modified_by)}
        for field, value in payload.items():
            if field not in allowed_fields:
                continue
            # Reserved-like identifiers must be escaped for MySQL parser safety.
            if field in {"break", "year_month"}:
                column_name = f"`{field}`"
            else:
                column_name = field
            assignments.append(f"{column_name} = :{field}")
            if field in json_fields:
                import json

                params[field] = json.dumps(value)
            else:
                params[field] = value
        if not assignments:
            return
        assignments.append("modified_by = :modified_by")
        await db.execute(
            text(
                f"""
                UPDATE salary
                SET {", ".join(assignments)}
                WHERE id = :record_id
                """
            ),
            params,
        )

    async def create_payroll_record(
        self,
        db: AsyncSession,
        *,
        payload: dict[str, Any],
        created_by: int,
    ) -> int:
        import json

        params = {
            "contact_id": int(payload.get("contact_id", 0)),
            "from_date": payload.get("from_date", "1970-01-01"),
            "to_date": payload.get("to_date", "1970-01-01"),
            "working_days": int(payload.get("working_days", 0)),
            "present_days": int(payload.get("present_days", 0)),
            "absent_days": int(payload.get("absent_days", 0)),
            "wo_days": int(payload.get("wo_days", 0)),
            "par_day": int(payload.get("par_day", 0)),
            "total_leaves": float(payload.get("total_leaves", 0.0)),
            "paid_leave": float(payload.get("paid_leave", 0.0)),
            "unpaid_leave": float(payload.get("unpaid_leave", 0.0)),
            "leave_balance_before": float(payload.get("leave_balance_before", 0.0)),
            "leave_balance_after": float(payload.get("leave_balance_after", 0.0)),
            "tax_amount": int(payload.get("tax_amount", 0)),
            "tax_comment": payload.get("tax_comment", ""),
            "advance_amount": int(payload.get("advance_amount", 0)),
            "advance_comment": payload.get("advance_comment", ""),
            "fine_amount": int(payload.get("fine_amount", 0)),
            "fine_comment": payload.get("fine_comment", ""),
            "base_amount": int(payload.get("base_amount", 0)),
            "incentive": int(payload.get("incentive", 0)),
            "incentive_comment": payload.get("incentive_comment", ""),
            "deduction": int(payload.get("deduction", 0)),
            "deduction_comment": payload.get("deduction_comment", ""),
            "addition": int(payload.get("addition", 0)),
            "addition_comment": payload.get("addition_comment", ""),
            "extra_amount": int(payload.get("extra_amount", 0)),
            "extra_comment": payload.get("extra_comment", ""),
            "allowance": int(payload.get("allowance", 0)),
            "pay_mode": int(payload.get("pay_mode", 0)),
            "sub_total": int(payload.get("sub_total", 0)),
            "salary": int(payload.get("salary", 0)),
            "paid": int(payload.get("paid", 0)),
            "bid": int(payload.get("bid", 0)),
            "comments": payload.get("comments", ""),
            "paid_holidays": int(payload.get("paid_holidays", 0)),
            "paid_holidays_dates": payload.get("paid_holidays_dates", ""),
            "leaves": int(payload.get("leaves", 0)),
            "leaves_dates": payload.get("leaves_dates", ""),
            "wfh": int(payload.get("wfh", 0)),
            "wfh_dates": payload.get("wfh_dates", ""),
            "half_day": int(payload.get("half_day", 0)),
            "half_day_dates": payload.get("half_day_dates", ""),
            "optional_holiday": int(payload.get("optional_holiday", 0)),
            "optional_holiday_dates": payload.get("optional_holiday_dates", ""),
            "punch_inout": int(payload.get("punch_inout", 0)),
            "punch_inout_dates": payload.get("punch_inout_dates", ""),
            "break": int(payload.get("break", 0)),
            "break_dates": payload.get("break_dates", ""),
            "supplementary": int(payload.get("supplementary", 0)),
            "supplementary_dates": payload.get("supplementary_dates", ""),
            "park": int(payload.get("park", 0)),
            "response_data": json.dumps(payload.get("response_data", {})),
            "response_data_app": json.dumps(payload.get("response_data_app", {})),
            "pay_slip_data": json.dumps(payload.get("pay_slip_data", {})),
            "incentive_html": payload.get("incentive_html", ""),
            "year_month": payload.get("year_month", "1970-01"),
            "created_by": int(created_by),
            "modified_by": int(created_by),
            "created_at": payload.get("created_at"),
            "modified_at": payload.get("modified_at"),
        }
        result = await db.execute(
            text(
                """
                INSERT INTO salary (
                    contact_id, from_date, to_date, working_days, present_days, absent_days, wo_days, par_day,
                    total_leaves, paid_leave, unpaid_leave, leave_balance_before, leave_balance_after,
                    tax_amount, tax_comment, advance_amount, advance_comment, fine_amount, fine_comment,
                    base_amount, incentive, incentive_comment, deduction, deduction_comment, addition, addition_comment,
                    extra_amount, extra_comment, allowance, pay_mode, sub_total, salary, paid, bid, comments,
                    paid_holidays, paid_holidays_dates, leaves, leaves_dates, wfh, wfh_dates, half_day, half_day_dates,
                    optional_holiday, optional_holiday_dates, punch_inout, punch_inout_dates, `break`, break_dates,
                    supplementary, supplementary_dates, park, response_data, response_data_app, pay_slip_data,
                    incentive_html, `year_month`, created_at, created_by, modified_at, modified_by
                ) VALUES (
                    :contact_id, :from_date, :to_date, :working_days, :present_days, :absent_days, :wo_days, :par_day,
                    :total_leaves, :paid_leave, :unpaid_leave, :leave_balance_before, :leave_balance_after,
                    :tax_amount, :tax_comment, :advance_amount, :advance_comment, :fine_amount, :fine_comment,
                    :base_amount, :incentive, :incentive_comment, :deduction, :deduction_comment, :addition, :addition_comment,
                    :extra_amount, :extra_comment, :allowance, :pay_mode, :sub_total, :salary, :paid, :bid, :comments,
                    :paid_holidays, :paid_holidays_dates, :leaves, :leaves_dates, :wfh, :wfh_dates, :half_day, :half_day_dates,
                    :optional_holiday, :optional_holiday_dates, :punch_inout, :punch_inout_dates, :break, :break_dates,
                    :supplementary, :supplementary_dates, :park, :response_data, :response_data_app, :pay_slip_data,
                    :incentive_html, :year_month, COALESCE(:created_at, CURRENT_TIMESTAMP), :created_by,
                    COALESCE(:modified_at, CURRENT_TIMESTAMP), :modified_by
                )
                """
            ),
            params,
        )
        inserted_id = getattr(result, "lastrowid", None)
        return int(inserted_id or 0)

    async def delete_payroll_record(
        self,
        db: AsyncSession,
        *,
        record_id: int,
    ) -> None:
        await db.execute(
            text(
                """
                DELETE FROM salary
                WHERE id = :record_id
                """
            ),
            {"record_id": int(record_id)},
        )

    async def count_salary_track(
        self,
        db: AsyncSession,
        *,
        employee_id: int | None,
    ) -> int:
        sql = "SELECT COUNT(*) AS total FROM employee e WHERE (e.park IS NULL OR e.park = 0)"
        params: dict[str, Any] = {}
        if employee_id is not None:
            sql += " AND e.id = :employee_id"
            params["employee_id"] = int(employee_id)
        result = await db.execute(text(sql), params)
        row = result.fetchone()
        return int(row._mapping["total"]) if row else 0

    async def list_salary_track(
        self,
        db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        limit: int,
        offset: int,
        position_map: dict[int, str] | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        salary_conditions: list[str] = []

        # Derive target year_month (YYYYMM) from from_date; fall back to to_date.
        # This avoids the "date overlap" trap where e.g. a March salary record
        # (to_date 2026-03-31) never satisfies `to_date >= 2026-04-01`.
        target_ym: str | None = None
        ref_date = from_date or to_date
        if ref_date:
            parts = ref_date.split("-")
            if len(parts) >= 2:
                target_ym = parts[0] + parts[1]  # "2026-03-01" → "202603"

        if target_ym:
            salary_conditions.append("s_inner.year_month = :target_ym")
            params["target_ym"] = target_ym

        salary_where = ("WHERE " + " AND ".join(salary_conditions)) if salary_conditions else ""

        sql = f"""
            SELECT
                e.id AS employee_id,
                e.contact_id,
                e.position_id,
                NULLIF(TRIM(CONCAT(COALESCE(c.fname, ''), ' ', COALESCE(c.lname, ''))), '') AS full_name,
                c.email,
                c.mobile,
                s.id AS salary_id,
                s.salary,
                s.paid,
                s.pay_mode,
                s.year_month,
                s.from_date,
                s.to_date,
                CASE
                    WHEN s.id IS NULL THEN 'not_generated'
                    WHEN s.paid > 0 THEN 'paid'
                    ELSE 'processing'
                END AS salary_status
            FROM employee e
            LEFT JOIN contact c ON c.id = e.contact_id
            LEFT JOIN (
                SELECT
                    s_inner.id,
                    s_inner.contact_id,
                    s_inner.salary,
                    s_inner.paid,
                    s_inner.pay_mode,
                    s_inner.year_month,
                    s_inner.from_date,
                    s_inner.to_date,
                    ROW_NUMBER() OVER (PARTITION BY s_inner.contact_id ORDER BY s_inner.id DESC) AS rn
                FROM salary s_inner
                {salary_where}
            ) s ON s.contact_id = e.contact_id AND s.rn = 1
            WHERE (e.park IS NULL OR e.park = 0)
        """
        if employee_id is not None:
            sql += " AND e.id = :employee_id"
            params["employee_id"] = int(employee_id)
        sql += " ORDER BY e.id DESC LIMIT :limit OFFSET :offset"
        params["limit"] = int(limit)
        params["offset"] = int(offset)

        result = await db.execute(text(sql), params)
        rows = result.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            m = dict(row._mapping)
            pos_id = m.get("position_id")
            position_name = (position_map or {}).get(int(pos_id)) if pos_id is not None else None
            out.append({
                "employee_id": m.get("employee_id"),
                "contact_id": m.get("contact_id"),
                "full_name": m.get("full_name"),
                "position": position_name,
                "email": m.get("email"),
                "mobile": m.get("mobile"),
                "salary_id": m.get("salary_id"),
                "salary": int(m["salary"]) if m.get("salary") is not None else None,
                "paid": int(m["paid"]) if m.get("paid") is not None else None,
                "pay_mode": m.get("pay_mode"),
                "year_month": str(m["year_month"]) if m.get("year_month") is not None else None,
                "from_date": str(m["from_date"]) if m.get("from_date") is not None else None,
                "to_date": str(m["to_date"]) if m.get("to_date") is not None else None,
                "salary_status": m.get("salary_status", "not_generated"),
            })
        return out

    async def _payroll_records_query(
        self,
        db: AsyncSession,
        *,
        employee_id: int | None,
        from_date: str | None,
        to_date: str | None,
        paid: int | None,
        park: int | None,
        select_sql: str,
        paid_nonzero: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        sql = f"""
            {select_sql}
            FROM salary s
            LEFT JOIN employee e ON e.contact_id = s.contact_id AND (e.park IS NULL OR e.park = 0)
            LEFT JOIN contact c ON c.id = s.contact_id
            WHERE 1 = 1
        """
        params: dict[str, Any] = {}
        if employee_id is not None:
            contact_id = await self.resolve_contact_id_for_employee(db, employee_id)
            if contact_id is None:
                sql += " AND 1 = 0"
                return sql, params
            sql += " AND s.contact_id = :contact_id"
            params["contact_id"] = int(contact_id)
        # Date range overlap semantics:
        # record [s.from_date, s.to_date] overlaps filter [from_date, to_date]
        if from_date and to_date:
            sql += " AND s.from_date <= :to_date AND s.to_date >= :from_date"
            params["from_date"] = from_date
            params["to_date"] = to_date
        elif from_date:
            sql += " AND s.to_date >= :from_date"
            params["from_date"] = from_date
        elif to_date:
            sql += " AND s.from_date <= :to_date"
            params["to_date"] = to_date
        if paid is not None:
            sql += " AND s.paid = :paid"
            params["paid"] = int(paid)
        elif paid_nonzero:
            sql += " AND s.paid > 0"
        if park is not None:
            sql += " AND s.park = :park"
            params["park"] = int(park)
        return sql, params

    @staticmethod
    def _first_matching_column(columns: set[str], candidates: list[str]) -> str | None:
        for candidate in candidates:
            if candidate in columns:
                return candidate
        return None

    # -------------------------------------------------------------------------
    # Employee CRUD helpers
    # -------------------------------------------------------------------------

    async def list_workshifts(self, db: AsyncSession) -> list[dict[str, Any]]:
        """Return workshift options from the workshift table.

        Tries multiple column-name variants since the schema differs across tenants.
        PHP confirmed the primary column is called ``workshift`` (not ``name``).
        """
        # Each query is tried in order; on a column-not-found error the session is
        # rolled back and the next variant is attempted.
        _queries = [
            # Variant 1: 'workshift' column + in_time/out_time
            """SELECT id, workshift AS name,
                      COALESCE(in_time, '') AS in_time,
                      COALESCE(out_time, '') AS out_time
               FROM workshift
               WHERE (park IS NULL OR park = 0)
               ORDER BY id ASC""",
            # Variant 2: 'workshift' column only (no in_time/out_time columns)
            """SELECT id, workshift AS name, '' AS in_time, '' AS out_time
               FROM workshift
               WHERE (park IS NULL OR park = 0)
               ORDER BY id ASC""",
            # Variant 3: 'name' column (alternate schema)
            """SELECT id, name, '' AS in_time, '' AS out_time
               FROM workshift
               WHERE (park IS NULL OR park = 0)
               ORDER BY id ASC""",
        ]
        for q in _queries:
            try:
                result = await db.execute(text(q))
                return [dict(row._mapping) for row in result.fetchall()]
            except Exception:
                try:
                    await db.rollback()
                except Exception:
                    pass
        return []

    async def list_document_types(self, db: AsyncSession) -> list[dict[str, Any]]:
        """Return document type options from the ``document_type`` table (pf_central DB)."""
        try:
            result = await db.execute(
                text(
                    """
                    SELECT id, name
                    FROM document_type
                    WHERE (park IS NULL OR park = 0)
                    ORDER BY id ASC
                    """
                )
            )
            return [dict(row._mapping) for row in result.fetchall()]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Parent-position (employee_parent table)
    # ------------------------------------------------------------------

    async def list_all_employees_simple(
        self, db: AsyncSession
    ) -> list[dict[str, Any]]:
        """Return id + full_name for all active, non-parked employees."""
        try:
            result = await db.execute(
                text(
                    """
                    SELECT
                        e.id,
                        TRIM(CONCAT_WS(' ',
                            NULLIF(TRIM(c.fname), ''),
                            NULLIF(TRIM(c.mname), ''),
                            NULLIF(TRIM(c.lname), '')
                        )) AS full_name
                    FROM employee e
                    JOIN contact c ON c.id = e.contact_id
                    WHERE (e.park IS NULL OR e.park = 0)
                      AND (c.park IS NULL OR c.park = 0)
                      AND e.status = 1
                    ORDER BY full_name ASC
                    """
                )
            )
            return [dict(row._mapping) for row in result.fetchall()]
        except Exception:
            return []

    async def get_employee_parent_ids(
        self, db: AsyncSession, employee_id: int
    ) -> list[int]:
        """Return the list of parent employee IDs for the given employee."""
        try:
            result = await db.execute(
                text(
                    """
                    SELECT parent_emp_id
                    FROM employee_parent
                    WHERE emp_id = :emp_id
                    """
                ),
                {"emp_id": employee_id},
            )
            return [int(row[0]) for row in result.fetchall() if row[0] is not None]
        except Exception:
            return []

    async def set_employee_parents(
        self, db: AsyncSession, employee_id: int, parent_ids: list[int]
    ) -> None:
        """Replace all parent-position rows for an employee atomically."""
        await db.execute(
            text("DELETE FROM employee_parent WHERE emp_id = :emp_id"),
            {"emp_id": employee_id},
        )
        for pid in parent_ids:
            await db.execute(
                text(
                    "INSERT INTO employee_parent (emp_id, parent_emp_id) VALUES (:emp_id, :pid)"
                ),
                {"emp_id": employee_id, "pid": int(pid)},
            )
        await db.flush()

    async def check_mobile_unique(
        self,
        db: AsyncSession,
        mobile: str,
        exclude_contact_id: int | None = None,
    ) -> bool:
        """Return True if mobile is available (not used by any active contact)."""
        sql = "SELECT id FROM contact WHERE mobile = :mobile AND (park IS NULL OR park = 0)"
        params: dict[str, Any] = {"mobile": mobile.strip()}
        if exclude_contact_id is not None:
            sql += " AND id != :exclude_id"
            params["exclude_id"] = int(exclude_contact_id)
        sql += " LIMIT 1"
        result = await db.execute(text(sql), params)
        row = result.fetchone()
        return row is None  # True = mobile is free

    async def get_employee_contact_group_id(self, db: AsyncSession) -> int | None:
        """Return the contact_group.id for the 'Employee' group."""
        result = await db.execute(
            text("SELECT id FROM contact_group WHERE LOWER(contact_group) = 'employee' LIMIT 1")
        )
        row = result.mappings().first()
        return int(row["id"]) if row else None

    async def list_branches(self, db: AsyncSession) -> list[dict[str, Any]]:
        """Return all active branch options (id + name)."""
        result = await db.execute(
            text(
                "SELECT id, branch AS name FROM branch "
                "WHERE park = 0 AND id NOT IN (86) ORDER BY branch ASC"
            )
        )
        return [dict(r) for r in result.mappings().all()]

    async def get_next_ecode(self, db: AsyncSession) -> int:
        """Return MAX(ecode) + 1 from the employee table."""
        result = await db.execute(text("SELECT MAX(ecode) AS max_ecode FROM employee"))
        row = result.mappings().first()
        max_val = row["max_ecode"] if row and row["max_ecode"] is not None else 0
        return int(max_val) + 1

    async def insert_employee_bid(self, db: AsyncSession, employee_id: int, bid: int) -> None:
        """Insert a row into employee_bid for the new employee."""
        await db.execute(
            text("INSERT INTO employee_bid (employee_id, bid) VALUES (:employee_id, :bid)"),
            {"employee_id": employee_id, "bid": bid},
        )

    async def get_client_db_id(self, central_db: AsyncSession, db_name: str) -> int | None:
        """Look up the client_db.id for the given DB name (pf_central.client_db)."""
        result = await central_db.execute(
            text("SELECT id FROM client_db WHERE db_name = :db_name LIMIT 1"),
            {"db_name": db_name},
        )
        row = result.mappings().first()
        return int(row["id"]) if row else None

    async def find_user_by_mobile(
        self, central_db: AsyncSession, mobile: str, user_type: str
    ) -> int | None:
        """Return user.id if an active (park=0) user exists for this mobile+type in pf_central."""
        result = await central_db.execute(
            text(
                "SELECT id FROM user "
                "WHERE mobile = :mobile AND type = :type AND park = '0' LIMIT 1"
            ),
            {"mobile": mobile, "type": user_type},
        )
        row = result.mappings().first()
        return int(row["id"]) if row else None

    async def find_user_by_mobile_any(
        self, central_db: AsyncSession, mobile: str, user_type: str
    ) -> int | None:
        """Return user.id for this mobile+type regardless of park status (parked or active)."""
        result = await central_db.execute(
            text(
                "SELECT id FROM user "
                "WHERE mobile = :mobile AND type = :type LIMIT 1"
            ),
            {"mobile": mobile, "type": user_type},
        )
        row = result.mappings().first()
        return int(row["id"]) if row else None

    async def park_central_user(self, central_db: AsyncSession, user_id: int) -> None:
        """Park user + user_social + user_device in pf_central (disable = 1→0)."""
        await central_db.execute(
            text("UPDATE user SET park = '1' WHERE id = :uid"),
            {"uid": user_id},
        )
        await central_db.execute(
            text("UPDATE user_social SET park = '1' WHERE user_id = :uid"),
            {"uid": user_id},
        )
        await central_db.execute(
            text("UPDATE user_device SET park = '1' WHERE user_id = :uid"),
            {"uid": user_id},
        )

    async def restore_central_user(self, central_db: AsyncSession, user_id: int) -> None:
        """Un-park user + user_social + user_device in pf_central (re-enable = 0→1)."""
        await central_db.execute(
            text("UPDATE user SET park = '0', password = '1234', mpin = '1234' WHERE id = :uid"),
            {"uid": user_id},
        )
        await central_db.execute(
            text("UPDATE user_social SET park = '0' WHERE user_id = :uid"),
            {"uid": user_id},
        )
        await central_db.execute(
            text("UPDATE user_device SET park = '0' WHERE user_id = :uid"),
            {"uid": user_id},
        )

    async def update_central_user_details(
        self,
        central_db: AsyncSession,
        *,
        contact_id: int,
        old_mobile: str,
        new_mobile: str,
        country_code: str | None,
        email: str | None,
        user_type: str,
    ) -> int:
        """Update mobile/email on the pf_central user row. Returns user_id (0 if not found)."""
        result = await central_db.execute(
            text(
                "SELECT id FROM user "
                "WHERE type = :type AND contact_id = :contact_id AND mobile = :old_mobile "
                "LIMIT 1"
            ),
            {"type": user_type, "contact_id": contact_id, "old_mobile": old_mobile},
        )
        row = result.mappings().first()
        if not row:
            return 0
        user_id = int(row["id"])

        set_parts = ["mobile = :new_mobile"]
        params: dict[str, Any] = {"new_mobile": new_mobile, "uid": user_id}
        if country_code:
            set_parts.append("country_code = :country_code")
            params["country_code"] = country_code
        await central_db.execute(
            text(f"UPDATE user SET {', '.join(set_parts)} WHERE id = :uid"),
            params,
        )

        # Sync email in user_social
        if email:
            social = await central_db.execute(
                text(
                    "SELECT id FROM user_social "
                    "WHERE user_id = :uid AND (social_type = '' OR social_type IS NULL) LIMIT 1"
                ),
                {"uid": user_id},
            )
            if social.fetchone():
                await central_db.execute(
                    text(
                        "UPDATE user_social SET email = :email "
                        "WHERE user_id = :uid AND (social_type = '' OR social_type IS NULL)"
                    ),
                    {"email": email, "uid": user_id},
                )
            else:
                await central_db.execute(
                    text("INSERT INTO user_social (email, user_id) VALUES (:email, :uid)"),
                    {"email": email, "uid": user_id},
                )
        else:
            await central_db.execute(
                text(
                    "DELETE FROM user_social "
                    "WHERE user_id = :uid AND (social_type = '' OR social_type IS NULL)"
                ),
                {"uid": user_id},
            )
        return user_id

    async def get_position_permissions(
        self, central_db: AsyncSession, position_id: int, client_id: int
    ) -> list[dict[str, Any]]:
        """Return permission rows from position_template joined with client_module."""
        result = await central_db.execute(
            text(
                """
                SELECT cm.id AS cm_id, cm.module_id, pt.permission
                FROM position_template AS pt
                INNER JOIN client_module AS cm ON pt.module_id = cm.module_id
                WHERE pt.epos_id = :position_id AND cm.client_id = :client_id
                """
            ),
            {"position_id": position_id, "client_id": client_id},
        )
        return [dict(r) for r in result.mappings().all()]

    async def create_central_user(
        self,
        central_db: AsyncSession,
        *,
        fname: str,
        lname: str,
        country_code: str,
        mobile: str,
        user_type: str,
        contact_id: int,
        client_id: int,
        email: str | None,
        bid: int,
        permissions: list[dict[str, Any]],
    ) -> int:
        """Create a user row in pf_central and set up user_bid, user_social, permissions."""
        from datetime import datetime as _dt
        now = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        result = await central_db.execute(
            text(
                """
                INSERT INTO user
                    (fname, lname, password, mpin, country_code, mobile, type,
                     contact_id, client_id, signup, created_at)
                VALUES
                    (:fname, :lname, '1234', '1234', :country_code, :mobile, :type,
                     :contact_id, :client_id, '1', :now)
                """
            ),
            {
                "fname": fname,
                "lname": lname or "",
                "country_code": country_code or "+91",
                "mobile": mobile,
                "type": user_type,
                "contact_id": contact_id,
                "client_id": client_id,
                "now": now,
            },
        )
        await central_db.flush()
        user_id = result.lastrowid
        if not user_id:
            raise RuntimeError("Failed to create user row in pf_central")
        user_id = int(user_id)

        await central_db.execute(
            text("INSERT INTO user_bid (user_id, bid) VALUES (:user_id, :bid)"),
            {"user_id": user_id, "bid": bid},
        )
        if email:
            await central_db.execute(
                text("INSERT INTO user_social (email, user_id) VALUES (:email, :user_id)"),
                {"email": email, "user_id": user_id},
            )
        for perm in permissions:
            await central_db.execute(
                text(
                    "INSERT INTO user_permission (user_id, cm_id, permission, bid) "
                    "VALUES (:user_id, :cm_id, :permission, :bid)"
                ),
                {
                    "user_id": user_id,
                    "cm_id": perm["cm_id"],
                    "permission": perm["permission"],
                    "bid": bid,
                },
            )
            if int(perm["permission"]) >= 2:
                await central_db.execute(
                    text(
                        "INSERT INTO notify_user (module_id, user_id) "
                        "VALUES (:module_id, :user_id)"
                    ),
                    {"module_id": perm["module_id"], "user_id": user_id},
                )
        return user_id

    async def insert_leave_bucket_entries(
        self,
        db: AsyncSession,
        *,
        contact_id: int,
        employee_id: int,
        ecode: int,
        doj: str,
        last_day: str,
    ) -> None:
        """Insert leave_bucket rows based on DOJ month (PHP parity: 1–3 rows)."""
        from datetime import datetime as _dt
        month = _dt.strptime(doj, "%Y-%m-%d").month
        count = 3 if month <= 4 else (2 if month <= 8 else 1)
        for _ in range(count):
            await db.execute(
                text(
                    """
                    INSERT INTO leave_bucket
                        (contact_id, emp_id, emp_code, doi, doe,
                         earned, category, day_code, consumed, expired, park)
                    VALUES
                        (:contact_id, :emp_id, :emp_code, :doj, :last_day,
                         '1.0', '2', '7', '0', '0', '0')
                    """
                ),
                {
                    "contact_id": contact_id,
                    "emp_id": employee_id,
                    "emp_code": ecode,
                    "doj": doj,
                    "last_day": last_day,
                },
            )

    async def upsert_demo_owner_names(
        self,
        db: AsyncSession,
        *,
        contact_id: int,
        name: str,
        mobile: str,
    ) -> None:
        """Insert or update demo_owner_names for the given contact_id."""
        exists_result = await db.execute(
            text("SELECT id FROM demo_owner_names WHERE contact_id = :contact_id LIMIT 1"),
            {"contact_id": contact_id},
        )
        if exists_result.fetchone():
            await db.execute(
                text(
                    "UPDATE demo_owner_names SET name = :name, mobile_number = :mobile "
                    "WHERE contact_id = :contact_id"
                ),
                {"contact_id": contact_id, "name": name, "mobile": mobile},
            )
        else:
            await db.execute(
                text(
                    "INSERT INTO demo_owner_names (contact_id, name, mobile_number) "
                    "VALUES (:contact_id, :name, :mobile)"
                ),
                {"contact_id": contact_id, "name": name, "mobile": mobile},
            )

    async def create_contact(
        self,
        db: AsyncSession,
        data: dict[str, Any],
    ) -> int:
        """Insert a new contact row and return the new contact_id."""
        fields = [
            "contact_group_id",
            "fname", "mname", "lname",
            "mobile", "country_code",
            "mobile2", "country_code_2", "phone_no",
            "email", "personal_email",
            "gender", "dob",
            "address", "city", "state", "country", "pincode",
            "parent_id", "relation",
            "document_type_id", "document_number", "document_image",
            "document_type_id_2", "document_number_2", "document_image_2",
            "document_type_id_3", "document_image_3",
            "bid", "created_by",
        ]
        cols = [f for f in fields if data.get(f) is not None]
        if not cols:
            raise ValueError("No contact fields provided")

        col_sql = ", ".join(cols)
        val_sql = ", ".join(f":{f}" for f in cols)
        result = await db.execute(
            text(f"INSERT INTO contact ({col_sql}) VALUES ({val_sql})"),
            {f: data[f] for f in cols},
        )
        await db.flush()
        contact_id = result.lastrowid
        if not contact_id:
            raise RuntimeError("Failed to insert contact row")
        return int(contact_id)

    async def update_contact(
        self,
        db: AsyncSession,
        contact_id: int,
        data: dict[str, Any],
    ) -> None:
        """Update writable contact fields by contact_id."""
        allowed = {
            "fname", "mname", "lname",
            "mobile", "country_code",
            "mobile2", "country_code_2", "phone_no",
            "email", "personal_email",
            "gender", "dob",
            "address", "city", "state", "country", "pincode",
            "parent_id", "relation",
            "document_type_id", "document_number", "document_image",
            "document_type_id_2", "document_number_2", "document_image_2",
            "document_type_id_3", "document_image_3",
        }
        to_set = {k: v for k, v in data.items() if k in allowed}
        if not to_set:
            return
        set_clause = ", ".join(f"{k} = :{k}" for k in to_set)
        params = {**to_set, "contact_id": int(contact_id)}
        await db.execute(
            text(f"UPDATE contact SET {set_clause} WHERE id = :contact_id"),
            params,
        )

    async def create_employee_record(
        self,
        db: AsyncSession,
        data: dict[str, Any],
    ) -> int:
        """Insert a new employee row and return the new employee_id."""
        fields = [
            "contact_id", "ecode", "department_id", "position_id",
            "doj", "doe", "exit_date",
            "workshift_id", "workshift_in_time", "workshift_out_time", "workshift_hours",
            "salary_type", "salary", "allowance",
            "type", "status", "grade",
            "user_account", "is_admin", "calculate_salary", "is_parent",
            "demo_owner", "cash_collector", "auto_assign_inq", "qualifier",
            "tds_type", "tds_percent", "rate_multiplier",
            "incentive_new", "incentive_renew", "p_incentive_c", "p_incentive_sc",
            "trainer_incentive", "mt_incentive",
            "created_by",
        ]
        cols = [f for f in fields if data.get(f) is not None]
        col_sql = ", ".join(cols)
        val_sql = ", ".join(f":{f}" for f in cols)
        result = await db.execute(
            text(f"INSERT INTO employee ({col_sql}) VALUES ({val_sql})"),
            {f: data[f] for f in cols},
        )
        await db.flush()
        employee_id = result.lastrowid
        if not employee_id:
            raise RuntimeError("Failed to insert employee row")
        return int(employee_id)

    async def update_employee_record(
        self,
        db: AsyncSession,
        employee_id: int,
        data: dict[str, Any],
    ) -> None:
        """Update writable employee fields by employee_id."""
        allowed = {
            "ecode", "department_id", "position_id",
            "doj", "doe", "exit_date",
            "workshift_id", "workshift_in_time", "workshift_out_time", "workshift_hours",
            "salary_type", "salary", "allowance",
            "type", "status", "grade",
            "user_account", "is_admin", "calculate_salary", "is_parent",
            "demo_owner", "cash_collector", "auto_assign_inq", "qualifier",
            "tds_type", "tds_percent", "rate_multiplier",
            "incentive_new", "incentive_renew", "p_incentive_c", "p_incentive_sc",
            "trainer_incentive", "mt_incentive",
        }
        to_set = {k: v for k, v in data.items() if k in allowed}
        if not to_set:
            return
        set_clause = ", ".join(f"{k} = :{k}" for k in to_set)
        params = {**to_set, "employee_id": int(employee_id)}
        await db.execute(
            text(f"UPDATE employee SET {set_clause} WHERE id = :employee_id"),
            params,
        )

    # -------------------------------------------------------------------------
    # Salary Excel view helpers
    # -------------------------------------------------------------------------

    def _salary_excel_where(
        self,
        *,
        from_date: str | None,
        to_date: str | None,
        months: list[str] | None,
        employee_names: list[str] | None,
        search: str | None,
        dept: str | None,
        paid_status: str | None,
    ) -> tuple[str, dict[str, Any]]:
        """Build WHERE clause and params for salary_excel_view queries."""
        conditions: list[str] = ["1 = 1"]
        params: dict[str, Any] = {}
        if months:
            month_placeholders: list[str] = []
            for idx, month in enumerate(months):
                key = f"month_{idx}"
                month_placeholders.append(f":{key}")
                params[key] = month
            conditions.append(f"DATE_FORMAT(`from_date`, '%Y-%m') IN ({', '.join(month_placeholders)})")
        elif from_date:
            conditions.append("`from_date` >= :from_date")
            params["from_date"] = from_date
        if not months and to_date:
            conditions.append("`from_date` <= :to_date")
            params["to_date"] = to_date
        if employee_names:
            employee_placeholders: list[str] = []
            for idx, employee_name in enumerate(employee_names):
                key = f"employee_name_{idx}"
                employee_placeholders.append(f":{key}")
                params[key] = employee_name
            conditions.append(f"`Name` IN ({', '.join(employee_placeholders)})")
        if search and search.strip():
            conditions.append("`Name` LIKE :search")
            params["search"] = f"%{search.strip()}%"
        if dept and dept.strip():
            conditions.append("`Dept` = :dept")
            params["dept"] = dept.strip()
        if paid_status == "paid":
            conditions.append("`Final Transfer Amt` > 0")
        elif paid_status == "unpaid":
            conditions.append("`Final Transfer Amt` = 0")
        return " AND ".join(conditions), params

    async def count_salary_excel(
        self,
        db: AsyncSession,
        *,
        from_date: str | None,
        to_date: str | None,
        months: list[str] | None,
        employee_names: list[str] | None,
        search: str | None,
        dept: str | None,
        paid_status: str | None,
    ) -> dict[str, int]:
        where, params = self._salary_excel_where(
            from_date=from_date,
            to_date=to_date,
            months=months,
            employee_names=employee_names,
            search=search,
            dept=dept,
            paid_status=paid_status,
        )
        sql = f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN `Final Transfer Amt` > 0 THEN 1 ELSE 0 END) AS paid_count,
                SUM(CASE WHEN `Final Transfer Amt` = 0 THEN 1 ELSE 0 END) AS unpaid_count
            FROM salary_excel_view
            WHERE {where}
        """
        result = await db.execute(text(sql), params)
        row = result.fetchone()
        if not row:
            return {"total": 0, "paid_count": 0, "unpaid_count": 0}
        m = dict(row._mapping)
        return {
            "total": int(m.get("total") or 0),
            "paid_count": int(m.get("paid_count") or 0),
            "unpaid_count": int(m.get("unpaid_count") or 0),
        }

    async def list_salary_excel(
        self,
        db: AsyncSession,
        *,
        from_date: str | None,
        to_date: str | None,
        months: list[str] | None,
        employee_names: list[str] | None,
        search: str | None,
        dept: str | None,
        paid_status: str | None,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        where, params = self._salary_excel_where(
            from_date=from_date,
            to_date=to_date,
            months=months,
            employee_names=employee_names,
            search=search,
            dept=dept,
            paid_status=paid_status,
        )
        sql = f"""
            SELECT
                `Month`,
                `from_date`,
                `Name`,
                `Dept`,
                `Employee Category`,
                `PAN`,
                `Gender`,
                `Base Salary`,
                `Total Working Days`,
                `Salary Per day`,
                `Actual Working Days`,
                `Base salary as per Attendance`,
                `Incentive`,
                `Fine/Advance/Deductions`,
                `Expense/Reimbursment/Sp. Bonus`,
                `Total Salary`,
                `TDS/PT`,
                `Deductions After TDS`,
                `Final Transfer Amt`
            FROM salary_excel_view
            WHERE {where}
            ORDER BY `from_date` DESC, `Name` ASC
            LIMIT :limit OFFSET :offset
        """
        params["limit"] = int(limit)
        params["offset"] = int(offset)
        result = await db.execute(text(sql), params)
        rows = result.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            m = dict(row._mapping)
            out.append({
                "month": str(m["Month"]) if m.get("Month") is not None else None,
                "from_date": str(m["from_date"]) if m.get("from_date") is not None else None,
                "name": m.get("Name"),
                "dept": m.get("Dept"),
                "employee_category": m.get("Employee Category"),
                "pan": m.get("PAN"),
                "gender": m.get("Gender"),
                "base_salary": int(m["Base Salary"]) if m.get("Base Salary") is not None else None,
                "total_working_days": int(m["Total Working Days"]) if m.get("Total Working Days") is not None else None,
                "salary_per_day": float(m["Salary Per day"]) if m.get("Salary Per day") is not None else None,
                "actual_working_days": int(m["Actual Working Days"]) if m.get("Actual Working Days") is not None else None,
                "base_salary_attendance": float(m["Base salary as per Attendance"]) if m.get("Base salary as per Attendance") is not None else None,
                "incentive": float(m["Incentive"]) if m.get("Incentive") is not None else None,
                "fine_advance_deductions": float(m["Fine/Advance/Deductions"]) if m.get("Fine/Advance/Deductions") is not None else None,
                "expense_reimbursement": float(m["Expense/Reimbursment/Sp. Bonus"]) if m.get("Expense/Reimbursment/Sp. Bonus") is not None else None,
                "total_salary": float(m["Total Salary"]) if m.get("Total Salary") is not None else None,
                "tds_pt": float(m["TDS/PT"]) if m.get("TDS/PT") is not None else None,
                "deductions_after_tds": m.get("Deductions After TDS"),
                "final_transfer_amt": float(m["Final Transfer Amt"]) if m.get("Final Transfer Amt") is not None else None,
            })
        return out

    async def list_salary_excel_depts(self, db: AsyncSession) -> list[str]:
        """Return distinct department names present in salary_excel_view."""
        result = await db.execute(
            text("SELECT DISTINCT `Dept` FROM salary_excel_view WHERE `Dept` IS NOT NULL ORDER BY `Dept` ASC")
        )
        return [str(row._mapping["Dept"]) for row in result.fetchall()]
