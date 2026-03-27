"""Main DB repository for Employee Events V1."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from sqlalchemy import inspect, text

from core.database import engines

from ..dependencies import EmployeeEventsError


class EmployeeEventsRepository:
    """DB operations for employee event and allowance tables."""

    _SAFE_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not cls._SAFE_SQL_IDENTIFIER.match(candidate):
            raise EmployeeEventsError(
                code="EMP_EVENT_LEAVE_QUERY_FAILED",
                message="Unsafe SQL identifier detected for leave query",
                status_code=500,
                data={"identifier": candidate},
            )
        return candidate

    @staticmethod
    def _first_matching_column(
        available_columns: set[str],
        candidates: List[str],
    ) -> Optional[str]:
        for name in candidates:
            if name in available_columns:
                return name
        return None

    def _get_table_columns(self, table_name: str) -> set[str]:
        engine = self._get_main_engine()
        normalized_table_name = self._safe_identifier(table_name)
        columns: set[str] = set()

        try:
            inspector = inspect(engine)
            for column in inspector.get_columns(normalized_table_name):
                name = column.get("name")
                if name:
                    columns.add(str(name).strip())
            if columns:
                return columns
        except Exception:
            pass

        with engine.connect() as conn:
            try:
                rows = conn.execute(
                    text(
                        f"SHOW COLUMNS FROM {normalized_table_name}"
                    )
                ).mappings().all()
                for row in rows:
                    name = row.get("Field")
                    if name:
                        columns.add(str(name).strip())
            except Exception:
                return set()

        return columns

    @staticmethod
    def _get_main_engine():
        engine = engines.get("default")
        if engine is None:
            raise EmployeeEventsError(
                code="EMP_EVENT_DB_UNAVAILABLE",
                message="Main DB engine is not available",
                status_code=503,
            )
        return engine

    def _require_event_exists(self, conn, event_id: int) -> None:
        row = conn.execute(
            text("SELECT id FROM employee_schedule_events WHERE id = :event_id LIMIT 1"),
            {"event_id": int(event_id)},
        ).mappings().first()
        if row is None:
            raise EmployeeEventsError(
                code="EMP_EVENT_NOT_FOUND",
                message=f"Event id {event_id} not found",
                status_code=404,
            )

    def check_conflict(
        self,
        date: str,
        start_time: str,
        end_time: str,
        contact_id: int,
        parked_value: int,
        exclude_event_id: Optional[int] = None,
    ) -> List[int]:
        engine = self._get_main_engine()
        sql = """
        SELECT id
        FROM employee_schedule_events
        WHERE date = :date
          AND start_time < :end_time
          AND end_time > :start_time
          AND contact_id = :contact_id
          AND (park IS NULL OR park <> :parked_value)
        """
        params: Dict[str, Any] = {
            "date": date,
            "start_time": start_time,
            "end_time": end_time,
            "contact_id": int(contact_id),
            "parked_value": int(parked_value),
        }

        if exclude_event_id is not None:
            sql += " AND id <> :exclude_event_id"
            params["exclude_event_id"] = int(exclude_event_id)

        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()

        return [int(row["id"]) for row in rows]

    def create_event_with_allowances(
        self,
        payload: Dict[str, Any],
        created_by: str,
    ) -> int:
        engine = self._get_main_engine()

        insert_sql = text(
            """
            INSERT INTO employee_schedule_events (
                category,
                contact_id,
                branch,
                description,
                type,
                lease_type,
                amount,
                deduction_amount,
                date,
                start_time,
                end_time,
                allowance,
                created_by
            ) VALUES (
                :category,
                :contact_id,
                :branch,
                :description,
                :type,
                :lease_type,
                :amount,
                :deduction_amount,
                :date,
                :start_time,
                :end_time,
                :allowance,
                :created_by
            )
            """
        )
        allowance_insert_sql = text(
            """
            INSERT INTO employee_event_allowance (
                event_id,
                name,
                amount,
                created_by
            ) VALUES (
                :event_id,
                :name,
                :amount,
                :created_by
            )
            """
        )

        with engine.begin() as conn:
            result = conn.execute(
                insert_sql,
                {
                    "category": payload.get("category"),
                    "contact_id": int(payload.get("contact_id")),
                    "branch": payload.get("branch"),
                    "description": payload.get("description") or "",
                    "type": payload.get("type"),
                    "lease_type": payload.get("lease_type"),
                    "amount": payload.get("amount"),
                    "deduction_amount": payload.get("deduction_amount"),
                    "date": payload.get("date"),
                    "start_time": payload.get("start_time"),
                    "end_time": payload.get("end_time"),
                    "allowance": int(payload.get("allowance") or 0),
                    "created_by": created_by,
                },
            )
            event_id = int(result.lastrowid or 0)
            if event_id <= 0:
                row = conn.execute(text("SELECT LAST_INSERT_ID() AS id")).mappings().first()
                event_id = int((row or {}).get("id") or 0)

            if event_id <= 0:
                raise EmployeeEventsError(
                    code="EMP_EVENT_DB_WRITE_FAILED",
                    message="Could not create employee event",
                    status_code=500,
                )

            if int(payload.get("allowance") or 0) == 1:
                for item in payload.get("allowance_items", []) or []:
                    conn.execute(
                        allowance_insert_sql,
                        {
                            "event_id": event_id,
                            "name": item.get("name"),
                            "amount": item.get("amount"),
                            "created_by": created_by,
                        },
                    )

        return event_id

    def update_event_with_allowances(
        self,
        event_id: int,
        payload: Dict[str, Any],
        actor_user_id: str,
    ) -> None:
        engine = self._get_main_engine()

        update_sql = text(
            """
            UPDATE employee_schedule_events
            SET
                category = :category,
                branch = :branch,
                description = :description,
                contact_id = :contact_id,
                type = :type,
                lease_type = :lease_type,
                amount = :amount,
                deduction_amount = :deduction_amount,
                date = :date,
                start_time = :start_time,
                end_time = :end_time,
                allowance = :allowance
            WHERE id = :event_id
            """
        )
        allowance_delete_sql = text(
            "DELETE FROM employee_event_allowance WHERE event_id = :event_id"
        )
        allowance_insert_sql = text(
            """
            INSERT INTO employee_event_allowance (
                event_id,
                name,
                amount,
                created_by
            ) VALUES (
                :event_id,
                :name,
                :amount,
                :created_by
            )
            """
        )

        with engine.begin() as conn:
            self._require_event_exists(conn, event_id)
            conn.execute(
                update_sql,
                {
                    "event_id": int(event_id),
                    "category": payload.get("category"),
                    "branch": payload.get("branch"),
                    "description": payload.get("description") or "",
                    "contact_id": int(payload.get("contact_id")),
                    "type": payload.get("type"),
                    "lease_type": payload.get("lease_type"),
                    "amount": payload.get("amount"),
                    "deduction_amount": payload.get("deduction_amount"),
                    "date": payload.get("date"),
                    "start_time": payload.get("start_time"),
                    "end_time": payload.get("end_time"),
                    "allowance": int(payload.get("allowance") or 0),
                },
            )
            conn.execute(allowance_delete_sql, {"event_id": int(event_id)})

            if int(payload.get("allowance") or 0) == 1:
                for item in payload.get("allowance_items", []) or []:
                    conn.execute(
                        allowance_insert_sql,
                        {
                            "event_id": int(event_id),
                            "name": item.get("name"),
                            "amount": item.get("amount"),
                            "created_by": actor_user_id,
                        },
                    )

    def set_status(self, event_id: int, status: int) -> None:
        engine = self._get_main_engine()
        with engine.begin() as conn:
            self._require_event_exists(conn, event_id)
            conn.execute(
                text("UPDATE employee_schedule_events SET status = :status WHERE id = :event_id"),
                {"event_id": int(event_id), "status": int(status)},
            )

    def set_park(self, event_id: int, park_value: int) -> None:
        engine = self._get_main_engine()
        with engine.begin() as conn:
            self._require_event_exists(conn, event_id)
            conn.execute(
                text("UPDATE employee_schedule_events SET park = :park_value WHERE id = :event_id"),
                {"event_id": int(event_id), "park_value": int(park_value)},
            )

    def get_event(self, event_id: int) -> Dict[str, Any]:
        engine = self._get_main_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM employee_schedule_events WHERE id = :event_id LIMIT 1"),
                {"event_id": int(event_id)},
            ).mappings().first()

        if row is None:
            raise EmployeeEventsError(
                code="EMP_EVENT_NOT_FOUND",
                message=f"Event id {event_id} not found",
                status_code=404,
            )
        return dict(row)

    def get_allowances(self, event_id: int) -> List[Dict[str, Any]]:
        engine = self._get_main_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT event_id, name, amount, created_by
                    FROM employee_event_allowance
                    WHERE event_id = :event_id
                    ORDER BY id ASC
                    """
                ),
                {"event_id": int(event_id)},
            ).mappings().all()
        return [dict(row) for row in rows]

    def get_contact(self, contact_id: int) -> Optional[Dict[str, Any]]:
        engine = self._get_main_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, fname, mname, lname, parent_name, country_code, mobile, email
                    FROM contact
                    WHERE id = :contact_id
                    LIMIT 1
                    """
                ),
                {"contact_id": int(contact_id)},
            ).mappings().first()

        if row is None:
            return None
        return dict(row)

    def list_events(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        contact_id: Optional[int] = None,
        status: Optional[int] = None,
        park: Optional[int] = None,
        include_parked: bool = True,
        parked_value: int = 1,
    ) -> List[Dict[str, Any]]:
        engine = self._get_main_engine()

        sql = """
        SELECT
            e.*,
            c.id AS contact_lookup_id,
            c.fname AS contact_fname,
            c.mname AS contact_mname,
            c.lname AS contact_lname,
            c.parent_name AS contact_parent_name,
            c.country_code AS contact_country_code,
            c.mobile AS contact_mobile,
            c.email AS contact_email
        FROM employee_schedule_events e
        LEFT JOIN contact c ON c.id = e.contact_id
        WHERE 1=1
        """
        params: Dict[str, Any] = {}

        if from_date:
            sql += " AND e.date >= :from_date"
            params["from_date"] = from_date
        if to_date:
            sql += " AND e.date <= :to_date"
            params["to_date"] = to_date
        if contact_id is not None:
            sql += " AND e.contact_id = :contact_id"
            params["contact_id"] = int(contact_id)
        if status is not None:
            sql += " AND e.status = :status"
            params["status"] = int(status)
        if park is not None:
            sql += " AND e.park = :park"
            params["park"] = int(park)
        elif not include_parked:
            sql += " AND (e.park IS NULL OR e.park <> :parked_value)"
            params["parked_value"] = int(parked_value)

        sql += " ORDER BY e.date ASC, e.start_time ASC, e.id ASC"

        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        return [dict(row) for row in rows]

    def list_trainer_calendar_events(
        self,
        contact_id: int,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        engine = self._get_main_engine()
        view_columns = self._get_table_columns("batch_employee_time_view")

        sql = """
        SELECT
            b.id,
            b.batch,
            b.display_name,
            b.parent_id,
            b.date,
            b.start_date,
            b.end_date,
            b.start_time,
            b.end_time,
            b.day_code,
            b.title,
            b.venue,
            b.timezone_id,
            b.contact_id,
            b.code,
            b.category,
            b.branch,
            b.bid,
            b.employee_id,
            b.associate_fullname,
            b.modified_at,
            p.batch AS parent_batch_name
        FROM batch_employee_time_view b
        LEFT JOIN batch_employee_time_view p
            ON p.id = b.parent_id
        WHERE b.contact_id = :contact_id
          AND COALESCE(b.park, 0) = 0
          AND COALESCE(b.inactive, 0) = 0
          AND COALESCE(b.hide, 0) = 0
          AND COALESCE(b.cont_park, 0) = 0
        """
        params: Dict[str, Any] = {
            "contact_id": int(contact_id),
        }

        # Apply batch visibility flags when available in the view schema.
        if "demo_class" in view_columns:
            sql += " AND COALESCE(b.demo_class, 0) = 0"
        if "training_assign" in view_columns:
            sql += " AND COALESCE(b.training_assign, 0) = 0"

        if from_date:
            sql += " AND COALESCE(b.end_date, b.start_date, b.date) >= :from_date"
            params["from_date"] = from_date
        if to_date:
            sql += " AND COALESCE(b.start_date, b.date, b.end_date) <= :to_date"
            params["to_date"] = to_date

        sql += " ORDER BY b.date ASC, b.start_time ASC, b.id ASC"

        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        return [dict(row) for row in rows]

    def get_allowances_for_event_ids(self, event_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        if not event_ids:
            return {}

        engine = self._get_main_engine()
        placeholders = []
        params: Dict[str, Any] = {}
        for idx, event_id in enumerate(event_ids):
            key = f"event_id_{idx}"
            placeholders.append(f":{key}")
            params[key] = int(event_id)

        sql = text(
            f"""
            SELECT event_id, name, amount, created_by
            FROM employee_event_allowance
            WHERE event_id IN ({", ".join(placeholders)})
            ORDER BY event_id ASC, id ASC
            """
        )

        with engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().all()

        grouped: Dict[int, List[Dict[str, Any]]] = {int(eid): [] for eid in event_ids}
        for row in rows:
            event_id = int(row["event_id"])
            grouped.setdefault(event_id, []).append(dict(row))
        return grouped

    def list_realtime_employees(self) -> List[Dict[str, Any]]:
        """
        Fetch employee options from emp_cont_view using fixed business filters.

        Filters:
        - park = 0
        - status = 1
        - fullname != ''
        Group by:
        - contact_id
        """
        engine = self._get_main_engine()
        sql = text(
            """
            SELECT e.id, e.contact_id, e.fullname, e.position_id, e.position, e.bid
            FROM emp_cont_view e
            INNER JOIN (
                SELECT contact_id, MAX(id) AS latest_id
                FROM emp_cont_view
                WHERE park = :park
                  AND status = :status
                  AND fullname <> :empty_fullname
                GROUP BY contact_id
            ) grouped
                ON grouped.contact_id = e.contact_id
               AND grouped.latest_id = e.id
            ORDER BY e.fullname ASC
            """
        )
        with engine.connect() as conn:
            rows = conn.execute(
                sql,
                {"park": "0", "status": "1", "empty_fullname": ""},
            ).mappings().all()
        return [dict(row) for row in rows]

    def get_employee_workshifts(self, employee_ids: List[int]) -> List[Dict[str, Any]]:
        if not employee_ids:
            return []

        engine = self._get_main_engine()
        placeholders = []
        params: Dict[str, Any] = {"park": "0", "status": "1"}
        for idx, employee_id in enumerate(employee_ids):
            key = f"employee_id_{idx}"
            placeholders.append(f":{key}")
            params[key] = int(employee_id)

        sql = text(
            f"""
            SELECT
                e.id AS employee_id,
                e.fullname AS employee_name,
                e.workshift_id,
                e.workshift_in_time,
                e.workshift_out_time,
                e.week_off_code
            FROM emp_cont_view e
            WHERE e.id IN ({", ".join(placeholders)})
              AND e.park = :park
              AND e.status = :status
            ORDER BY e.id ASC
            """
        )

        with engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().all()
        return [dict(row) for row in rows]

    def get_workshift_day_rows(self, workshift_ids: List[int]) -> List[Dict[str, Any]]:
        if not workshift_ids:
            return []

        engine = self._get_main_engine()
        placeholders = []
        params: Dict[str, Any] = {}
        for idx, workshift_id in enumerate(workshift_ids):
            key = f"workshift_id_{idx}"
            placeholders.append(f":{key}")
            params[key] = int(workshift_id)

        sql = text(
            f"""
            SELECT
                workshift_id,
                day_code,
                start_time,
                end_time
            FROM workshift_day
            WHERE workshift_id IN ({", ".join(placeholders)})
            ORDER BY workshift_id ASC, day_code ASC
            """
        )

        with engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().all()
        return [dict(row) for row in rows]

    def get_active_employees(self, employee_ids: List[int]) -> List[Dict[str, Any]]:
        if not employee_ids:
            return []

        engine = self._get_main_engine()
        placeholders = []
        params: Dict[str, Any] = {"park": "0", "status": "1"}
        for idx, employee_id in enumerate(employee_ids):
            key = f"employee_id_{idx}"
            placeholders.append(f":{key}")
            params[key] = int(employee_id)

        sql = text(
            f"""
            SELECT
                e.id AS employee_id,
                e.fullname AS employee_name,
                e.department_id AS department_id
            FROM emp_cont_view e
            WHERE e.id IN ({", ".join(placeholders)})
              AND e.park = :park
              AND e.status = :status
            ORDER BY e.id ASC
            """
        )

        with engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().all()
        return [dict(row) for row in rows]

    def get_employee_leave_requests(
        self,
        employee_ids: List[int],
        from_date: str,
        to_date: str,
        statuses: Optional[List[int]] = None,
        request_types: Optional[List[int]] = None,
        department_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        if not employee_ids:
            return []

        engine = self._get_main_engine()
        leave_columns = self._get_table_columns("emp_att_request")
        employee_view_columns = self._get_table_columns("emp_cont_view")

        if not leave_columns:
            raise EmployeeEventsError(
                code="EMP_EVENT_LEAVE_QUERY_FAILED",
                message="Table emp_att_request is missing or has no readable columns",
                status_code=500,
            )

        leave_id_column = self._first_matching_column(
            leave_columns,
            ["id", "request_id", "att_request_id"],
        )
        leave_employee_ref_column = self._first_matching_column(
            leave_columns,
            ["employee_id", "emp_id", "contact_id"],
        )
        leave_start_column = self._first_matching_column(
            leave_columns,
            ["start_date", "from_date", "leave_from", "start_datetime", "start_time", "start"],
        )
        leave_end_column = self._first_matching_column(
            leave_columns,
            ["end_date", "to_date", "leave_to", "end_datetime", "end_time", "end"],
        )
        leave_status_column = self._first_matching_column(
            leave_columns,
            ["status", "request_status", "leave_status", "approval_status"],
        )
        leave_request_type_column = self._first_matching_column(
            leave_columns,
            ["request_type", "type", "req_type", "leave_type"],
        )

        missing_required = [
            name
            for name, value in (
                ("id", leave_id_column),
                ("employee_ref", leave_employee_ref_column),
                ("start", leave_start_column),
                ("end", leave_end_column),
            )
            if value is None
        ]
        if missing_required:
            raise EmployeeEventsError(
                code="EMP_EVENT_LEAVE_QUERY_FAILED",
                message="emp_att_request does not have the required columns for leave query",
                status_code=500,
                data={"missing_columns": missing_required},
            )

        leave_id_column = self._safe_identifier(leave_id_column)
        leave_employee_ref_column = self._safe_identifier(leave_employee_ref_column)
        leave_start_column = self._safe_identifier(leave_start_column)
        leave_end_column = self._safe_identifier(leave_end_column)
        if leave_status_column is not None:
            leave_status_column = self._safe_identifier(leave_status_column)
        if leave_request_type_column is not None:
            leave_request_type_column = self._safe_identifier(leave_request_type_column)

        if leave_employee_ref_column == "contact_id":
            if "contact_id" not in employee_view_columns:
                raise EmployeeEventsError(
                    code="EMP_EVENT_LEAVE_QUERY_FAILED",
                    message="emp_cont_view.contact_id is required for leave query join",
                    status_code=500,
                )
            join_condition = "e.contact_id = l.contact_id"
        else:
            join_condition = f"e.id = l.{leave_employee_ref_column}"

        employee_placeholders = []
        params: Dict[str, Any] = {
            "park": "0",
            "status": "1",
            "from_date": from_date,
            "to_date": to_date,
        }
        for idx, employee_id in enumerate(employee_ids):
            key = f"employee_id_{idx}"
            employee_placeholders.append(f":{key}")
            params[key] = int(employee_id)

        status_select = (
            f"l.{leave_status_column} AS status"
            if leave_status_column is not None
            else "NULL AS status"
        )
        request_type_select = (
            f"l.{leave_request_type_column} AS request_type"
            if leave_request_type_column is not None
            else "NULL AS request_type"
        )

        sql = f"""
        SELECT
            l.{leave_id_column} AS leave_request_id,
            e.id AS employee_id,
            e.fullname AS employee_name,
            e.department_id AS department_id,
            l.{leave_start_column} AS start_date,
            l.{leave_end_column} AS end_date,
            {status_select},
            {request_type_select}
        FROM emp_att_request l
        INNER JOIN emp_cont_view e
            ON {join_condition}
        WHERE e.id IN ({", ".join(employee_placeholders)})
          AND e.park = :park
          AND e.status = :status
          AND l.{leave_start_column} <= :to_date
          AND l.{leave_end_column} >= :from_date
        """

        if statuses:
            if leave_status_column is None:
                return []
            status_placeholders = []
            for idx, status_value in enumerate(statuses):
                key = f"status_{idx}"
                status_placeholders.append(f":{key}")
                params[key] = int(status_value)
            sql += f" AND l.{leave_status_column} IN ({', '.join(status_placeholders)})"

        if request_types:
            if leave_request_type_column is None:
                return []
            request_type_placeholders = []
            for idx, request_type in enumerate(request_types):
                key = f"request_type_{idx}"
                request_type_placeholders.append(f":{key}")
                params[key] = int(request_type)
            sql += f" AND l.{leave_request_type_column} IN ({', '.join(request_type_placeholders)})"

        if department_ids:
            department_placeholders = []
            for idx, department_id in enumerate(department_ids):
                key = f"department_id_{idx}"
                department_placeholders.append(f":{key}")
                params[key] = int(department_id)
            sql += f" AND e.department_id IN ({', '.join(department_placeholders)})"

        sql += f" ORDER BY l.{leave_start_column} ASC, l.{leave_id_column} ASC"

        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        return [dict(row) for row in rows]

    def get_approved_leave_for_employee(
        self,
        employee_id: int,
        from_date: str,
        to_date: str,
    ) -> List[Dict[str, Any]]:
        """
        Fetch approved leave requests for a single employee overlapping a date range.
        
        Used for availability calculations - returns only approved (status=1) leave requests
        that overlap with the specified date range.
        
        Args:
            employee_id: The employee ID (from emp_cont_view.id)
            from_date: Start date (YYYY-MM-DD) - inclusive
            to_date: End date (YYYY-MM-DD) - inclusive
        
        Returns:
            List of leave request records with fields: id, employee_id, start_date, end_date,
            status, request_type, and any other available columns from emp_att_request
        """
        engine = self._get_main_engine()
        
        sql = text("""
            SELECT
                l.id,
                l.employee_id,
                l.start_date,
                l.end_date,
                l.status,
                l.request_type,
                l.remarks
            FROM emp_att_request l
            WHERE l.employee_id = :employee_id
              AND l.status = 1
              AND l.start_date <= :to_date
              AND l.end_date >= :from_date
            ORDER BY l.start_date ASC, l.id ASC
        """)
        
        with engine.connect() as conn:
            rows = conn.execute(sql, {
                "employee_id": int(employee_id),
                "from_date": from_date,
                "to_date": to_date,
            }).mappings().all()
        
        return [dict(row) for row in rows]

    def list_active_branches(self) -> List[Dict[str, Any]]:
        """
        Fetch branch options from branch table using fixed business filters.

        Filters:
        - id NOT IN (86)
        - park = 0
        """
        engine = self._get_main_engine()
        sql = text(
            """
            SELECT id, branch, type
            FROM branch
            WHERE id NOT IN (86)
              AND park = :park
            ORDER BY branch ASC
            """
        )
        with engine.connect() as conn:
            rows = conn.execute(
                sql,
                {"park": "0"},
            ).mappings().all()
        return [dict(row) for row in rows]

    def get_demo_events(
        self,
        employee_ids: List[int],
        from_date: str,
        to_date: str,
        statuses: Optional[List[int]] = None,
        types: Optional[List[int]] = None,
        venue_ids: Optional[List[int]] = None,
        batch_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        if not employee_ids:
            return []

        engine = self._get_main_engine()

        employee_placeholders = []
        params: Dict[str, Any] = {
            "from_date": from_date,
            "to_date": to_date,
        }
        for idx, employee_id in enumerate(employee_ids):
            key = f"employee_id_{idx}"
            employee_placeholders.append(f":{key}")
            params[key] = int(employee_id)

        sql = f"""
        SELECT
            id,
            demo_type,
            otp,
            hashed_otp,
            demo_link,
            demo_venue_link,
            name,
            demoApproval,
            date,
            start_date,
            end_date,
            demo_status,
            start_time,
            start_time_hour,
            end_time,
            end_time_hour,
            include_time,
            existing_batch,
            batch_id,
            venue_id,
            amount,
            currency,
            infant,
            ad_hoc,
            upi_id,
            venue,
            storytelling_location,
            workshop_location,
            demo_information,
            select_paid_demo,
            paid_demo,
            select_free_demo,
            free_demo,
            city,
            venue_display_name,
            venue_address,
            venue_short_address,
            venue_addr_for_post,
            venue_address_link,
            timezone_abbr_offset,
            timezone_gmt_offset_name,
            timezone_zone_name,
            host_employee_id,
            host_contact_id,
            zone_time_str,
            zone_time_details,
            host_name,
            sc_employee_id,
            sc_contact_id,
            sc_host_name,
            sc_host_fullname,
            sc_host_email,
            so_employee_id,
            so_host_name,
            so_contact_id,
            so_host_fullname,
            so_host_email,
            comment,
            stop_response,
            response_count,
            response_limit_flag,
            response_limit,
            seats_left,
            ads,
            ads_comment,
            all_fields_filled,
            demo_ad_status,
            bid,
            branch,
            type,
            owner_id,
            owner_name,
            owner_contact_id,
            owner_email,
            mobile_country_code,
            mobile_number,
            optional_mobile_country_code,
            optional_mobile_number,
            park,
            demo_date_string,
            day_code,
            demo_day,
            day,
            created_at,
            created_by,
            modified_at,
            modified_by,
            modified_by_fname,
            hybrid
        FROM demo_link_view
        WHERE (
              host_contact_id IN ({", ".join(employee_placeholders)})
              OR sc_contact_id IN ({", ".join(employee_placeholders)})
              OR so_contact_id IN ({", ".join(employee_placeholders)})
              OR owner_contact_id IN ({", ".join(employee_placeholders)})
          )
          AND start_date >= :from_date
          AND start_date <= :to_date
          AND park = 0
          AND demoApproval = 1
        """

        if statuses:
            status_placeholders = []
            for idx, status_value in enumerate(statuses):
                key = f"status_{idx}"
                status_placeholders.append(f":{key}")
                params[key] = int(status_value)
            sql += f" AND demo_status IN ({', '.join(status_placeholders)})"

        if types:
            type_placeholders = []
            for idx, type_value in enumerate(types):
                key = f"type_{idx}"
                type_placeholders.append(f":{key}")
                params[key] = int(type_value)
            sql += f" AND demo_type IN ({', '.join(type_placeholders)})"

        if venue_ids:
            venue_placeholders = []
            for idx, venue_id in enumerate(venue_ids):
                key = f"venue_id_{idx}"
                venue_placeholders.append(f":{key}")
                params[key] = int(venue_id)
            sql += f" AND venue_id IN ({', '.join(venue_placeholders)})"

        if batch_ids:
            batch_placeholders = []
            for idx, batch_id in enumerate(batch_ids):
                key = f"batch_id_{idx}"
                batch_placeholders.append(f":{key}")
                params[key] = int(batch_id)
            sql += f" AND batch_id IN ({', '.join(batch_placeholders)})"

        sql += " ORDER BY start_date ASC, id ASC"

        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        return [dict(row) for row in rows]
