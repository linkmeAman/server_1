"""Repository tests for Employee Events V1 batch lookups."""

import unittest

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, event
from sqlalchemy.pool import StaticPool

from controllers.employee_events_v1.services.event_repository import EmployeeEventsRepository
from core.database import engines


class TestEmployeeEventsV1Repository(unittest.TestCase):
    def setUp(self):
        self._original_engines = dict(engines)
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        metadata = MetaData()
        employee_source = Table(
            "employee_source",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("fullname", String(255)),
            Column("department_id", Integer, nullable=True),
            Column("workshift_id", Integer, nullable=True),
            Column("workshift_in_time", String(32), nullable=True),
            Column("workshift_out_time", String(32), nullable=True),
            Column("week_off_code", String(64), nullable=True),
            Column("park", String(8), nullable=False),
            Column("status", String(8), nullable=False),
        )
        leave_request = Table(
            "emp_att_request",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("employee_id", Integer, nullable=False),
            Column("start_date", String(32), nullable=True),
            Column("end_date", String(32), nullable=True),
            Column("status", Integer, nullable=True),
            Column("request_type", Integer, nullable=True),
        )
        workshift_day = Table(
            "workshift_day",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("workshift_id", Integer, nullable=False),
            Column("day_code", Integer, nullable=False),
            Column("start_time", String(32), nullable=True),
            Column("end_time", String(32), nullable=True),
        )
        venue = Table(
            "venue",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("venue", String(255), nullable=True),
            Column("display_name", String(255), nullable=True),
            Column("park", String(8), nullable=False),
            Column("status", String(8), nullable=False),
        )
        invoice_invoiceitem_source = Table(
            "invoice_invoiceitem_source",
            metadata,
            Column("invoice_id", Integer, nullable=False),
            Column("item_id", Integer, primary_key=True),
            Column("invoice", String(255), nullable=True),
            Column("code_name", String(255), nullable=True),
            Column("sessions", Integer, nullable=True),
            Column("sessions_used", Integer, nullable=True),
            Column("dob", String(32), nullable=True),
            Column("counsellor_name", String(255), nullable=True),
            Column("balance", Integer, nullable=True),
            Column("dropout", String(8), nullable=False),
            Column("freeze", String(8), nullable=False),
            Column("date", String(32), nullable=True),
            Column("start_date", String(32), nullable=True),
            Column("end_date", String(32), nullable=True),
            Column("batch_id", Integer, nullable=False),
            Column("park", String(8), nullable=False),
            Column("renew", String(8), nullable=False),
        )
        metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            conn.execute(
                employee_source.insert(),
                [
                    {
                        "id": 1,
                        "fullname": "Sneha Trainer",
                        "department_id": 10,
                        "workshift_id": 5,
                        "workshift_in_time": "10:00:00",
                        "workshift_out_time": "19:00:00",
                        "week_off_code": "0,6",
                        "park": "0",
                        "status": "1",
                    },
                    {
                        "id": 2,
                        "fullname": "Parked Employee",
                        "department_id": 11,
                        "workshift_id": 7,
                        "workshift_in_time": "09:00:00",
                        "workshift_out_time": "18:00:00",
                        "week_off_code": "",
                        "park": "1",
                        "status": "1",
                    },
                    {
                        "id": 3,
                        "fullname": "Inactive Employee",
                        "department_id": 12,
                        "workshift_id": 8,
                        "workshift_in_time": "08:00:00",
                        "workshift_out_time": "17:00:00",
                        "week_off_code": "",
                        "park": "0",
                        "status": "0",
                    },
                    {
                        "id": 4,
                        "fullname": "Aakash Kharat",
                        "department_id": 11,
                        "workshift_id": 9,
                        "workshift_in_time": "11:00:00",
                        "workshift_out_time": "20:00:00",
                        "week_off_code": "1 2",
                        "park": "0",
                        "status": "1",
                    },
                ],
            )
            conn.execute(
                leave_request.insert(),
                [
                    {
                        "id": 101,
                        "employee_id": 1,
                        "start_date": "2026-03-01",
                        "end_date": "2026-03-03",
                        "status": 1,
                        "request_type": 1,
                    },
                    {
                        "id": 102,
                        "employee_id": 1,
                        "start_date": "2026-02-20",
                        "end_date": "2026-03-01",
                        "status": 0,
                        "request_type": 3,
                    },
                    {
                        "id": 103,
                        "employee_id": 1,
                        "start_date": "2026-04-10",
                        "end_date": "2026-04-11",
                        "status": 1,
                        "request_type": 1,
                    },
                    {
                        "id": 104,
                        "employee_id": 4,
                        "start_date": "2026-03-02",
                        "end_date": "2026-03-02",
                        "status": 2,
                        "request_type": 2,
                    },
                    {
                        "id": 105,
                        "employee_id": 2,
                        "start_date": "2026-03-02",
                        "end_date": "2026-03-02",
                        "status": 1,
                        "request_type": 1,
                    },
                    {
                        "id": 106,
                        "employee_id": 3,
                        "start_date": "2026-03-02",
                        "end_date": "2026-03-02",
                        "status": 1,
                        "request_type": 1,
                    },
                ],
            )
            conn.execute(
                workshift_day.insert(),
                [
                    # workshift_id=5: Mon–Fri (1–5), no Sat/Sun
                    {"id": 1, "workshift_id": 5, "day_code": 1, "start_time": "10:00:00", "end_time": "19:00:00"},
                    {"id": 2, "workshift_id": 5, "day_code": 2, "start_time": "10:00:00", "end_time": "19:00:00"},
                    {"id": 3, "workshift_id": 5, "day_code": 3, "start_time": "10:00:00", "end_time": "19:00:00"},
                    {"id": 4, "workshift_id": 5, "day_code": 4, "start_time": "10:00:00", "end_time": "19:00:00"},
                    {"id": 5, "workshift_id": 5, "day_code": 5, "start_time": "10:00:00", "end_time": "19:00:00"},
                    # workshift_id=9: overnight shift on Wednesday only
                    {"id": 6, "workshift_id": 9, "day_code": 3, "start_time": "22:00:00", "end_time": "06:00:00"},
                ],
            )
            conn.execute(
                venue.insert(),
                [
                    {
                        "id": 10,
                        "venue": "Andheri Center",
                        "display_name": "Andheri Center",
                        "park": "0",
                        "status": "0",
                    },
                    {
                        "id": 20,
                        "venue": "Bandra Center",
                        "display_name": "Bandra Center",
                        "park": "1",
                        "status": "0",
                    },
                    {
                        "id": 30,
                        "venue": "Closed Center",
                        "display_name": "Closed Center",
                        "park": "0",
                        "status": "1",
                    },
                ],
            )
            conn.execute(
                invoice_invoiceitem_source.insert(),
                [
                    {
                        "invoice_id": 1001,
                        "item_id": 11,
                        "invoice": "INV-1001",
                        "code_name": "A001 - Aarav",
                        "sessions": 16,
                        "sessions_used": 3,
                        "dob": "2018-01-01",
                        "counsellor_name": "Counsellor A",
                        "balance": 10,
                        "dropout": "0",
                        "freeze": "0",
                        "date": "2026-03-01",
                        "start_date": "2026-03-01",
                        "end_date": "2026-06-30",
                        "batch_id": 700,
                        "park": "0",
                        "renew": "0",
                    },
                    {
                        "invoice_id": 1002,
                        "item_id": 12,
                        "invoice": "INV-1002",
                        "code_name": "A002 - Diya",
                        "sessions": 12,
                        "sessions_used": 4,
                        "dob": "2017-05-03",
                        "counsellor_name": "Counsellor B",
                        "balance": 8,
                        "dropout": "0",
                        "freeze": "0",
                        "date": "2026-03-02",
                        "start_date": "2026-03-10",
                        "end_date": "2026-07-15",
                        "batch_id": 700,
                        "park": "0",
                        "renew": "0",
                    },
                    {
                        "invoice_id": 1003,
                        "item_id": 13,
                        "invoice": "INV-1003",
                        "code_name": "Filtered Park",
                        "sessions": 12,
                        "sessions_used": 1,
                        "dob": "2017-06-03",
                        "counsellor_name": "Counsellor B",
                        "balance": 11,
                        "dropout": "0",
                        "freeze": "0",
                        "date": "2026-03-02",
                        "start_date": "2026-03-10",
                        "end_date": "2026-07-15",
                        "batch_id": 700,
                        "park": "1",
                        "renew": "0",
                    },
                    {
                        "invoice_id": 1004,
                        "item_id": 14,
                        "invoice": "INV-1004",
                        "code_name": "Filtered Renew",
                        "sessions": 12,
                        "sessions_used": 1,
                        "dob": "2017-07-03",
                        "counsellor_name": "Counsellor C",
                        "balance": 11,
                        "dropout": "0",
                        "freeze": "0",
                        "date": "2026-03-02",
                        "start_date": "2026-03-10",
                        "end_date": "2026-07-15",
                        "batch_id": 700,
                        "park": "0",
                        "renew": "1",
                    },
                    {
                        "invoice_id": 1005,
                        "item_id": 15,
                        "invoice": "INV-1005",
                        "code_name": "Filtered Dropout",
                        "sessions": 12,
                        "sessions_used": 1,
                        "dob": "2017-08-03",
                        "counsellor_name": "Counsellor C",
                        "balance": 11,
                        "dropout": "1",
                        "freeze": "0",
                        "date": "2026-03-02",
                        "start_date": "2026-03-10",
                        "end_date": "2026-07-15",
                        "batch_id": 700,
                        "park": "0",
                        "renew": "0",
                    },
                    {
                        "invoice_id": 1006,
                        "item_id": 16,
                        "invoice": "INV-1006",
                        "code_name": "Filtered Freeze",
                        "sessions": 12,
                        "sessions_used": 1,
                        "dob": "2017-09-03",
                        "counsellor_name": "Counsellor C",
                        "balance": 11,
                        "dropout": "0",
                        "freeze": "1",
                        "date": "2026-03-02",
                        "start_date": "2026-03-10",
                        "end_date": "2026-07-15",
                        "batch_id": 700,
                        "park": "0",
                        "renew": "0",
                    },
                    {
                        "invoice_id": 1007,
                        "item_id": 17,
                        "invoice": "INV-1007",
                        "code_name": "Filtered Batch",
                        "sessions": 12,
                        "sessions_used": 1,
                        "dob": "2017-10-03",
                        "counsellor_name": "Counsellor D",
                        "balance": 11,
                        "dropout": "0",
                        "freeze": "0",
                        "date": "2026-03-02",
                        "start_date": "2026-03-10",
                        "end_date": "2026-07-15",
                        "batch_id": 701,
                        "park": "0",
                        "renew": "0",
                    },
                    {
                        "invoice_id": 1008,
                        "item_id": 18,
                        "invoice": "INV-1008",
                        "code_name": "Filtered Overlap",
                        "sessions": 12,
                        "sessions_used": 1,
                        "dob": "2017-11-03",
                        "counsellor_name": "Counsellor E",
                        "balance": 11,
                        "dropout": "0",
                        "freeze": "0",
                        "date": "2026-01-01",
                        "start_date": "2026-01-01",
                        "end_date": "2026-01-15",
                        "batch_id": 700,
                        "park": "0",
                        "renew": "0",
                    },
                ],
            )
            conn.exec_driver_sql(
                """
                CREATE VIEW emp_cont_view AS
                SELECT
                    id,
                    fullname,
                    department_id,
                    workshift_id,
                    workshift_in_time,
                    workshift_out_time,
                    week_off_code,
                    park,
                    status
                FROM employee_source
                """
            )
            conn.exec_driver_sql(
                """
                CREATE VIEW invoice_invoiceitem_view AS
                SELECT
                    invoice_id,
                    item_id,
                    invoice,
                    code_name,
                    sessions,
                    sessions_used,
                    dob,
                    counsellor_name,
                    balance,
                    dropout,
                    freeze,
                    date,
                    start_date,
                    end_date,
                    batch_id,
                    park,
                    renew
                FROM invoice_invoiceitem_source
                """
            )

        engines.clear()
        engines["default"] = self.engine
        self.repository = EmployeeEventsRepository()

    def tearDown(self):
        engines.clear()
        engines.update(self._original_engines)

    def test_get_employee_workshifts_returns_only_active_rows(self):
        result = self.repository.get_employee_workshifts([1, 2, 3, 4, 99])

        self.assertEqual([1, 4], [row["employee_id"] for row in result])

    def test_get_employee_workshifts_returns_public_employee_id_and_name(self):
        result = self.repository.get_employee_workshifts([1])

        self.assertEqual(1, len(result))
        self.assertEqual(1, result[0]["employee_id"])
        self.assertEqual("Sneha Trainer", result[0]["employee_name"])
        self.assertEqual(5, result[0]["workshift_id"])

    def test_get_employee_workshifts_uses_single_select_for_batch(self):
        statement_count = {"select": 0}

        def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statement_count["select"] += 1

        event.listen(self.engine, "before_cursor_execute", _before_cursor_execute)
        try:
            result = self.repository.get_employee_workshifts([1, 4])
        finally:
            event.remove(self.engine, "before_cursor_execute", _before_cursor_execute)

        self.assertEqual(2, len(result))
        self.assertEqual(1, statement_count["select"])

    def test_get_active_employees_returns_only_active_rows(self):
        result = self.repository.get_active_employees([1, 2, 3, 4, 99])

        self.assertEqual([1, 4], [row["employee_id"] for row in result])
        self.assertEqual([10, 11], [row["department_id"] for row in result])

    def test_list_active_venues_applies_active_filters(self):
        result = self.repository.list_active_venues()

        self.assertEqual(
            [{"id": 10, "venue": "Andheri Center", "display_name": "Andheri Center"}],
            result,
        )

    def test_list_batch_kids_present_applies_filters_and_overlap(self):
        result = self.repository.list_batch_kids_present(
            batch_id=700,
            from_date="2026-03-20",
            to_date="2026-06-26",
        )

        self.assertEqual([11, 12], [row["item_id"] for row in result])
        self.assertTrue(all(row["dropout"] == "0" for row in result))
        self.assertTrue(all(row["freeze"] == "0" for row in result))

    def test_list_batch_kids_present_returns_only_curated_columns_in_order(self):
        result = self.repository.list_batch_kids_present(
            batch_id=700,
            from_date="2026-03-20",
            to_date="2026-06-26",
        )

        self.assertEqual(
            [
                "invoice_id",
                "item_id",
                "invoice",
                "code_name",
                "sessions",
                "sessions_used",
                "dob",
                "counsellor_name",
                "balance",
                "dropout",
                "freeze",
                "date",
            ],
            list(result[0].keys()),
        )
        self.assertEqual([11, 12], [row["item_id"] for row in result])

    def test_get_employee_leave_requests_applies_overlap_and_active_filters(self):
        result = self.repository.get_employee_leave_requests(
            employee_ids=[1, 2, 3, 4],
            from_date="2026-03-01",
            to_date="2026-03-03",
            statuses=None,
            request_types=None,
            department_ids=None,
        )

        self.assertEqual([102, 101, 104], [row["leave_request_id"] for row in result])
        self.assertEqual([1, 1, 4], [row["employee_id"] for row in result])

    def test_get_employee_leave_requests_applies_optional_filters(self):
        result = self.repository.get_employee_leave_requests(
            employee_ids=[1, 4],
            from_date="2026-03-01",
            to_date="2026-03-03",
            statuses=[1],
            request_types=[1],
            department_ids=[10],
        )

        self.assertEqual(1, len(result))
        self.assertEqual(101, result[0]["leave_request_id"])
        self.assertEqual(1, result[0]["employee_id"])

    def test_get_employee_leave_requests_uses_single_select_for_batch(self):
        statement_count = {"select": 0}

        def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statement_count["select"] += 1

        event.listen(self.engine, "before_cursor_execute", _before_cursor_execute)
        try:
            result = self.repository.get_employee_leave_requests(
                employee_ids=[1, 4],
                from_date="2026-03-01",
                to_date="2026-03-03",
                statuses=None,
                request_types=None,
                department_ids=None,
            )
        finally:
            event.remove(self.engine, "before_cursor_execute", _before_cursor_execute)

        self.assertEqual(3, len(result))
        self.assertEqual(1, statement_count["select"])


class TestEmployeeEventsV1RepositorySchemaVariants(unittest.TestCase):
    def setUp(self):
        self._original_engines = dict(engines)
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        metadata = MetaData()
        employee_source = Table(
            "employee_source",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("contact_id", Integer, nullable=False),
            Column("fullname", String(255)),
            Column("department_id", Integer, nullable=True),
            Column("park", String(8), nullable=False),
            Column("status", String(8), nullable=False),
        )
        leave_request_alt = Table(
            "emp_att_request",
            metadata,
            Column("request_id", Integer, primary_key=True),
            Column("contact_id", Integer, nullable=False),
            Column("from_date", String(32), nullable=True),
            Column("to_date", String(32), nullable=True),
            Column("request_status", Integer, nullable=True),
            Column("type", Integer, nullable=True),
        )
        metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            conn.execute(
                employee_source.insert(),
                [
                    {
                        "id": 10,
                        "contact_id": 110,
                        "fullname": "Variant Employee",
                        "department_id": 5,
                        "park": "0",
                        "status": "1",
                    }
                ],
            )
            conn.execute(
                leave_request_alt.insert(),
                [
                    {
                        "request_id": 9001,
                        "contact_id": 110,
                        "from_date": "2026-03-01",
                        "to_date": "2026-03-01",
                        "request_status": 1,
                        "type": 3,
                    }
                ],
            )
            conn.exec_driver_sql(
                """
                CREATE VIEW emp_cont_view AS
                SELECT
                    id,
                    contact_id,
                    fullname,
                    department_id,
                    park,
                    status
                FROM employee_source
                """
            )

        engines.clear()
        engines["default"] = self.engine
        self.repository = EmployeeEventsRepository()

    def tearDown(self):
        engines.clear()
        engines.update(self._original_engines)

    def test_get_employee_leave_requests_supports_alternate_column_names(self):
        rows = self.repository.get_employee_leave_requests(
            employee_ids=[10],
            from_date="2026-03-01",
            to_date="2026-03-01",
            statuses=[1],
            request_types=[3],
            department_ids=[5],
        )

        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(9001, row["leave_request_id"])
        self.assertEqual(10, row["employee_id"])
        self.assertEqual("Variant Employee", row["employee_name"])
        self.assertEqual("2026-03-01", row["start_date"])
        self.assertEqual("2026-03-01", row["end_date"])
        self.assertEqual(1, row["status"])
        self.assertEqual(3, row["request_type"])


class TestEmployeeEventsV1RepositoryTrainerCalendarEvents(unittest.TestCase):
    def setUp(self):
        self._original_engines = dict(engines)
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        metadata = MetaData()
        batch_employee_time_source = Table(
            "batch_employee_time_source",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("batch", String(255), nullable=True),
            Column("display_name", String(255), nullable=True),
            Column("venue_id", Integer, nullable=True),
            Column("parent_id", Integer, nullable=True),
            Column("date", String(32), nullable=True),
            Column("start_date", String(32), nullable=True),
            Column("end_date", String(32), nullable=True),
            Column("start_time", String(32), nullable=True),
            Column("end_time", String(32), nullable=True),
            Column("day_code", String(64), nullable=True),
            Column("title", String(255), nullable=True),
            Column("venue", String(255), nullable=True),
            Column("timezone_id", String(64), nullable=True),
            Column("contact_id", Integer, nullable=False),
            Column("code", String(64), nullable=True),
            Column("category", String(64), nullable=True),
            Column("branch", String(128), nullable=True),
            Column("bid", Integer, nullable=True),
            Column("employee_id", Integer, nullable=True),
            Column("associate_fullname", String(255), nullable=True),
            Column("modified_at", String(64), nullable=True),
            Column("park", Integer, nullable=True),
            Column("inactive", Integer, nullable=True),
            Column("hide", Integer, nullable=True),
            Column("cont_park", Integer, nullable=True),
            Column("demo_class", Integer, nullable=True),
            Column("training_assign", Integer, nullable=True),
        )
        metadata.create_all(self.engine)

        with self.engine.begin() as conn:
            conn.execute(
                batch_employee_time_source.insert(),
                [
                    {
                        "id": 100,
                        "batch": "Batch A",
                        "display_name": "Batch A Display",
                        "venue_id": 10,
                        "parent_id": None,
                        "date": "2026-03-01",
                        "start_date": "2026-03-01",
                        "end_date": "2026-03-31",
                        "start_time": "10:00:00",
                        "end_time": "11:00:00",
                        "day_code": "1,3",
                        "title": "Morning Batch A",
                        "venue": "Centre A",
                        "timezone_id": "Asia/Kolkata",
                        "contact_id": 100,
                        "code": "A-100",
                        "category": "Offline",
                        "branch": "Main",
                        "bid": 1,
                        "employee_id": 501,
                        "associate_fullname": "Trainer One",
                        "modified_at": "2026-03-01 09:00:00",
                        "park": 0,
                        "inactive": 0,
                        "hide": 0,
                        "cont_park": 0,
                        "demo_class": 0,
                        "training_assign": 0,
                    },
                    {
                        "id": 101,
                        "batch": "Batch A Reschedule",
                        "display_name": "Rescheduled A",
                        "venue_id": 10,
                        "parent_id": 100,
                        "date": "2026-03-12",
                        "start_date": "2026-03-12",
                        "end_date": "2026-03-12",
                        "start_time": "14:00:00",
                        "end_time": "15:00:00",
                        "day_code": "",
                        "title": "Rescheduled Session",
                        "venue": "Centre B",
                        "timezone_id": "Asia/Kolkata",
                        "contact_id": 100,
                        "code": "A-101",
                        "category": "Offline",
                        "branch": "Main",
                        "bid": 1,
                        "employee_id": 501,
                        "associate_fullname": "Trainer One",
                        "modified_at": "2026-03-12 09:00:00",
                        "park": 0,
                        "inactive": 0,
                        "hide": 0,
                        "cont_park": 0,
                        "demo_class": 0,
                        "training_assign": 0,
                    },
                    {
                        "id": 200,
                        "batch": "Other Trainer Batch",
                        "display_name": "Other Trainer",
                        "venue_id": 20,
                        "parent_id": None,
                        "date": "2026-03-05",
                        "start_date": "2026-03-01",
                        "end_date": "2026-03-31",
                        "start_time": "09:00:00",
                        "end_time": "10:00:00",
                        "day_code": "2,4",
                        "title": "Other Session",
                        "venue": "Centre C",
                        "timezone_id": "Asia/Kolkata",
                        "contact_id": 200,
                        "code": "B-200",
                        "category": "Online",
                        "branch": "West",
                        "bid": 2,
                        "employee_id": 601,
                        "associate_fullname": "Trainer Two",
                        "modified_at": "2026-03-01 09:00:00",
                        "park": 0,
                        "inactive": 0,
                        "hide": 0,
                        "cont_park": 0,
                        "demo_class": 0,
                        "training_assign": 0,
                    },
                    {
                        "id": 300,
                        "batch": "Hidden Batch",
                        "display_name": "Hidden",
                        "venue_id": 10,
                        "parent_id": None,
                        "date": "2026-03-10",
                        "start_date": "2026-03-10",
                        "end_date": "2026-03-10",
                        "start_time": "10:00:00",
                        "end_time": "11:00:00",
                        "day_code": "2",
                        "title": "Hidden",
                        "venue": "Centre D",
                        "timezone_id": "Asia/Kolkata",
                        "contact_id": 100,
                        "code": "H-300",
                        "category": "Offline",
                        "branch": "Main",
                        "bid": 3,
                        "employee_id": 701,
                        "associate_fullname": "Trainer Hidden",
                        "modified_at": "2026-03-10 09:00:00",
                        "park": 0,
                        "inactive": 0,
                        "hide": 1,
                        "cont_park": 0,
                        "demo_class": 0,
                        "training_assign": 0,
                    },
                    {
                        "id": 400,
                        "batch": "Inactive Batch",
                        "display_name": "Inactive Batch",
                        "venue_id": 10,
                        "parent_id": None,
                        "date": "2026-03-10",
                        "start_date": "2026-03-10",
                        "end_date": "2026-03-10",
                        "start_time": "10:00:00",
                        "end_time": "11:00:00",
                        "day_code": "2",
                        "title": "Inactive",
                        "venue": "Centre A",
                        "timezone_id": "Asia/Kolkata",
                        "contact_id": 100,
                        "code": "I-400",
                        "category": "Offline",
                        "branch": "Main",
                        "bid": 3,
                        "employee_id": 701,
                        "associate_fullname": "Trainer Hidden",
                        "modified_at": "2026-03-10 09:00:00",
                        "park": 0,
                        "inactive": 1,
                        "hide": 0,
                        "cont_park": 0,
                        "demo_class": 0,
                        "training_assign": 0,
                    },
                    {
                        "id": 500,
                        "batch": "Demo Class Batch",
                        "display_name": "Demo Class Batch",
                        "venue_id": 10,
                        "parent_id": None,
                        "date": "2026-03-10",
                        "start_date": "2026-03-10",
                        "end_date": "2026-03-10",
                        "start_time": "10:00:00",
                        "end_time": "11:00:00",
                        "day_code": "2",
                        "title": "Demo Class",
                        "venue": "Centre A",
                        "timezone_id": "Asia/Kolkata",
                        "contact_id": 100,
                        "code": "D-500",
                        "category": "Offline",
                        "branch": "Main",
                        "bid": 3,
                        "employee_id": 701,
                        "associate_fullname": "Trainer Hidden",
                        "modified_at": "2026-03-10 09:00:00",
                        "park": 0,
                        "inactive": 0,
                        "hide": 0,
                        "cont_park": 0,
                        "demo_class": 1,
                        "training_assign": 0,
                    },
                    {
                        "id": 600,
                        "batch": "Training Assign Batch",
                        "display_name": "Training Assign Batch",
                        "venue_id": 20,
                        "parent_id": None,
                        "date": "2026-03-10",
                        "start_date": "2026-03-10",
                        "end_date": "2026-03-10",
                        "start_time": "10:00:00",
                        "end_time": "11:00:00",
                        "day_code": "2",
                        "title": "Training Assign",
                        "venue": "Centre C",
                        "timezone_id": "Asia/Kolkata",
                        "contact_id": 200,
                        "code": "T-600",
                        "category": "Offline",
                        "branch": "West",
                        "bid": 2,
                        "employee_id": 601,
                        "associate_fullname": "Trainer Two",
                        "modified_at": "2026-03-10 09:00:00",
                        "park": 0,
                        "inactive": 0,
                        "hide": 0,
                        "cont_park": 0,
                        "demo_class": 0,
                        "training_assign": 1,
                    },
                ],
            )
            conn.exec_driver_sql(
                """
                CREATE VIEW batch_employee_time_view AS
                SELECT *
                FROM batch_employee_time_source
                """
            )

        engines.clear()
        engines["default"] = self.engine
        self.repository = EmployeeEventsRepository()

    def tearDown(self):
        engines.clear()
        engines.update(self._original_engines)

    def test_list_trainer_calendar_events_filters_by_contact_and_active_flags(self):
        rows = self.repository.list_trainer_calendar_events(contact_id=100)

        self.assertEqual([100, 101], [row["id"] for row in rows])
        self.assertEqual(["Batch A", "Batch A Reschedule"], [row["batch"] for row in rows])
        self.assertEqual([None, "Batch A"], [row["parent_batch_name"] for row in rows])

    def test_list_trainer_calendar_events_applies_date_bounds(self):
        rows = self.repository.list_trainer_calendar_events(
            contact_id=100,
            from_date="2026-03-11",
            to_date="2026-03-15",
        )

        self.assertEqual([100, 101], [row["id"] for row in rows])

    def test_list_trainer_calendar_events_orders_by_date_time_then_id(self):
        rows = self.repository.list_trainer_calendar_events(contact_id=100)
        ordering = [(row["date"], row["start_time"], row["id"]) for row in rows]

        self.assertEqual(
            [
                ("2026-03-01", "10:00:00", 100),
                ("2026-03-12", "14:00:00", 101),
            ],
            ordering,
        )

    def test_list_active_batches_by_venue_ids_filters_by_venue_and_active_flags(self):
        rows = self.repository.list_active_batches_by_venue_ids([10])

        self.assertEqual([100, 101], [row["id"] for row in rows])
        self.assertTrue(all(row["venue_id"] == 10 for row in rows))

    def test_list_active_batches_by_venue_ids_supports_multiple_venues(self):
        rows = self.repository.list_active_batches_by_venue_ids([20, 10])

        self.assertEqual([100, 101, 200], [row["id"] for row in rows])
        self.assertEqual([10, 10, 20], [row["venue_id"] for row in rows])


    def test_get_workshift_day_rows_returns_empty_for_empty_input(self):
        result = self.repository.get_workshift_day_rows([])

        self.assertEqual([], result)

    def test_get_workshift_day_rows_single_workshift_id(self):
        result = self.repository.get_workshift_day_rows([5])

        self.assertEqual(5, len(result))
        self.assertTrue(all(row["workshift_id"] == 5 for row in result))
        day_codes = [row["day_code"] for row in result]
        self.assertEqual([1, 2, 3, 4, 5], day_codes)
        self.assertEqual("10:00:00", result[0]["start_time"])
        self.assertEqual("19:00:00", result[0]["end_time"])

    def test_get_workshift_day_rows_multiple_workshift_ids(self):
        result = self.repository.get_workshift_day_rows([5, 9])

        ws_ids = sorted({row["workshift_id"] for row in result})
        self.assertEqual([5, 9], ws_ids)
        ws9_rows = [row for row in result if row["workshift_id"] == 9]
        self.assertEqual(1, len(ws9_rows))
        self.assertEqual(3, ws9_rows[0]["day_code"])
        self.assertEqual("22:00:00", ws9_rows[0]["start_time"])
        self.assertEqual("06:00:00", ws9_rows[0]["end_time"])

    def test_get_workshift_day_rows_unknown_id_returns_empty(self):
        result = self.repository.get_workshift_day_rows([999])

        self.assertEqual([], result)

    def test_get_workshift_day_rows_uses_single_select_for_batch(self):
        statement_count = {"select": 0}

        def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statement_count["select"] += 1

        event.listen(self.engine, "before_cursor_execute", _before_cursor_execute)
        try:
            self.repository.get_workshift_day_rows([5, 9])
        finally:
            event.remove(self.engine, "before_cursor_execute", _before_cursor_execute)

        self.assertEqual(1, statement_count["select"])


if __name__ == "__main__":
    unittest.main()
