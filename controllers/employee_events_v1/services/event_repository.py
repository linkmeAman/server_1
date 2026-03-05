"""Main DB repository for Employee Events V1."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import text

from core.database import engines

from ..dependencies import EmployeeEventsError


class EmployeeEventsRepository:
    """DB operations for employee event and allowance tables."""

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
