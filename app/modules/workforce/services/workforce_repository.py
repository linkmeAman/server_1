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
        limit: int = 25,
        offset: int = 0,
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

    async def list_scheduled_events(
        self,
        db: AsyncSession,
        *,
        employee_id: int,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT
                    id,
                    category,
                    description,
                    date,
                    start_time,
                    end_time,
                    status
                FROM employee_schedule_events
                WHERE employee_id = :employee_id
                  AND date >= :from_date
                  AND date <= :to_date
                  AND (park IS NULL OR park = 0)
                ORDER BY date ASC, start_time ASC, id ASC
                """
            ),
            {
                "employee_id": int(employee_id),
                "from_date": from_date,
                "to_date": to_date,
            },
        )
        return [dict(row._mapping) for row in result.fetchall()]

    async def list_leave_requests(
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
                l.{id_column} AS leave_request_id,
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

    async def count_scheduled_events(
        self,
        db: AsyncSession,
        *,
        from_date: str,
        to_date: str,
        department_id: int | None = None,
    ) -> int:
        sql = """
            SELECT COUNT(*) AS total
            FROM employee_schedule_events ese
            INNER JOIN employee e ON e.id = ese.employee_id
            WHERE ese.date >= :from_date
              AND ese.date <= :to_date
              AND (ese.park IS NULL OR ese.park = 0)
              AND (e.park IS NULL OR e.park = 0)
        """
        params: dict[str, Any] = {"from_date": from_date, "to_date": to_date}
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
