"""Repository tests for Employee Events V1 demo events lookups."""

import unittest

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, event
from sqlalchemy.pool import StaticPool

from controllers.employee_events_v1.services.event_repository import EmployeeEventsRepository
from core.database import engines


class TestDemoEventsRepository(unittest.TestCase):
    def setUp(self):
        self._original_engines = dict(engines)
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        metadata = MetaData()
        Table(
            "demo_link_view",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("demo_type", Integer, nullable=True),
            Column("otp", String(32), nullable=True),
            Column("hashed_otp", String(255), nullable=True),
            Column("demo_link", String(255), nullable=True),
            Column("demo_venue_link", String(255), nullable=True),
            Column("name", String(255), nullable=True),
            Column("demoApproval", Integer, nullable=True),
            Column("date", String(32), nullable=True),
            Column("start_date", String(32), nullable=True),
            Column("end_date", String(32), nullable=True),
            Column("demo_status", Integer, nullable=True),
            Column("start_time", String(32), nullable=True),
            Column("start_time_hour", String(32), nullable=True),
            Column("end_time", String(32), nullable=True),
            Column("end_time_hour", String(32), nullable=True),
            Column("include_time", Integer, nullable=True),
            Column("existing_batch", Integer, nullable=True),
            Column("batch_id", Integer, nullable=True),
            Column("venue_id", Integer, nullable=True),
            Column("amount", Integer, nullable=True),
            Column("currency", String(16), nullable=True),
            Column("infant", Integer, nullable=True),
            Column("ad_hoc", Integer, nullable=True),
            Column("upi_id", String(255), nullable=True),
            Column("venue", String(255), nullable=True),
            Column("storytelling_location", String(255), nullable=True),
            Column("workshop_location", String(255), nullable=True),
            Column("demo_information", String(1000), nullable=True),
            Column("select_paid_demo", Integer, nullable=True),
            Column("paid_demo", Integer, nullable=True),
            Column("select_free_demo", Integer, nullable=True),
            Column("free_demo", Integer, nullable=True),
            Column("city", String(255), nullable=True),
            Column("venue_display_name", String(255), nullable=True),
            Column("venue_address", String(500), nullable=True),
            Column("venue_short_address", String(255), nullable=True),
            Column("venue_addr_for_post", String(500), nullable=True),
            Column("venue_address_link", String(500), nullable=True),
            Column("timezone_abbr_offset", String(32), nullable=True),
            Column("timezone_gmt_offset_name", String(32), nullable=True),
            Column("timezone_zone_name", String(64), nullable=True),
            Column("host_employee_id", Integer, nullable=True),
            Column("host_contact_id", Integer, nullable=True),
            Column("zone_time_str", String(255), nullable=True),
            Column("zone_time_details", String(1000), nullable=True),
            Column("host_name", String(255), nullable=True),
            Column("sc_employee_id", Integer, nullable=True),
            Column("sc_contact_id", Integer, nullable=True),
            Column("sc_host_name", String(255), nullable=True),
            Column("sc_host_fullname", String(255), nullable=True),
            Column("sc_host_email", String(255), nullable=True),
            Column("so_employee_id", Integer, nullable=True),
            Column("so_host_name", String(255), nullable=True),
            Column("so_contact_id", Integer, nullable=True),
            Column("so_host_fullname", String(255), nullable=True),
            Column("so_host_email", String(255), nullable=True),
            Column("comment", String(1000), nullable=True),
            Column("stop_response", Integer, nullable=True),
            Column("response_count", Integer, nullable=True),
            Column("response_limit_flag", Integer, nullable=True),
            Column("response_limit", Integer, nullable=True),
            Column("seats_left", Integer, nullable=True),
            Column("ads", Integer, nullable=True),
            Column("ads_comment", String(1000), nullable=True),
            Column("all_fields_filled", Integer, nullable=True),
            Column("demo_ad_status", Integer, nullable=True),
            Column("bid", Integer, nullable=True),
            Column("branch", String(255), nullable=True),
            Column("type", Integer, nullable=True),
            Column("owner_id", Integer, nullable=True),
            Column("owner_name", String(255), nullable=True),
            Column("owner_contact_id", Integer, nullable=True),
            Column("owner_email", String(255), nullable=True),
            Column("mobile_country_code", String(8), nullable=True),
            Column("mobile_number", String(32), nullable=True),
            Column("optional_mobile_country_code", String(8), nullable=True),
            Column("optional_mobile_number", String(32), nullable=True),
            Column("park", Integer, nullable=True),
            Column("demo_date_string", String(64), nullable=True),
            Column("day_code", Integer, nullable=True),
            Column("demo_day", String(32), nullable=True),
            Column("day", String(32), nullable=True),
            Column("created_at", String(64), nullable=True),
            Column("created_by", String(255), nullable=True),
            Column("modified_at", String(64), nullable=True),
            Column("modified_by", String(255), nullable=True),
            Column("modified_by_fname", String(255), nullable=True),
            Column("hybrid", Integer, nullable=True),
        )
        metadata.create_all(self.engine)

        view_table = metadata.tables["demo_link_view"]
        with self.engine.begin() as conn:
            conn.execute(
                view_table.insert(),
                [
                    {
                        "id": 1, "demo_type": 1, "demo_status": 1, "demoApproval": 1,
                        "name": "Demo One", "date": "2026-03-10",
                        "start_date": "2026-03-10", "end_date": "2026-03-10",
                        "start_time": "10:00", "end_time": "11:00",
                        "host_employee_id": 1, "host_contact_id": 100,
                        "sc_employee_id": 30, "sc_contact_id": 300,
                        "so_employee_id": 40, "so_contact_id": 400,
                        "batch_id": 5, "venue_id": 10,
                        "owner_id": 50, "owner_name": "Owner A",
                        "owner_contact_id": 500, "park": 0,
                    },
                    {
                        "id": 2, "demo_type": 2, "demo_status": 2, "demoApproval": 1,
                        "name": "Demo Two", "date": "2026-03-15",
                        "start_date": "2026-03-15", "end_date": "2026-03-15",
                        "start_time": "14:00", "end_time": "15:00",
                        "host_employee_id": 1, "host_contact_id": 100,
                        "sc_employee_id": 31, "sc_contact_id": 310,
                        "so_employee_id": 41, "so_contact_id": 410,
                        "batch_id": 6, "venue_id": 11,
                        "owner_id": 51, "owner_name": "Owner B",
                        "owner_contact_id": 510, "park": 0,
                    },
                    {
                        "id": 3, "demo_type": 1, "demo_status": 1, "demoApproval": 1,
                        "name": "Demo Three", "date": "2026-03-20",
                        "start_date": "2026-03-20", "end_date": "2026-03-20",
                        "start_time": "09:00", "end_time": "10:00",
                        "host_employee_id": 2, "host_contact_id": 200,
                        "sc_employee_id": 32, "sc_contact_id": 320,
                        "so_employee_id": 42, "so_contact_id": 420,
                        "batch_id": 5, "venue_id": 10,
                        "owner_id": 50, "owner_name": "Owner A",
                        "owner_contact_id": 500, "park": 0,
                    },
                    {
                        "id": 4, "demo_type": 1, "demo_status": 1, "demoApproval": 1,
                        "name": "Demo Out of Range", "date": "2026-04-10",
                        "start_date": "2026-04-10", "end_date": "2026-04-10",
                        "start_time": "09:00", "end_time": "10:00",
                        "host_employee_id": 1, "host_contact_id": 100,
                        "sc_employee_id": 30, "sc_contact_id": 300,
                        "so_employee_id": 40, "so_contact_id": 400,
                        "batch_id": 5, "venue_id": 10,
                        "owner_id": 50, "owner_name": "Owner A",
                        "owner_contact_id": 500, "park": 0,
                    },
                    {
                        "id": 5, "demo_type": 1, "demo_status": 1, "demoApproval": 1,
                        "name": "Demo SC Only", "date": "2026-03-12",
                        "start_date": "2026-03-12", "end_date": "2026-03-12",
                        "start_time": "11:00", "end_time": "12:00",
                        "host_employee_id": 70, "host_contact_id": 700,
                        "sc_employee_id": 31, "sc_contact_id": 310,
                        "so_employee_id": 71, "so_contact_id": 710,
                        "batch_id": 5, "venue_id": 10,
                        "owner_id": 52, "owner_name": "Owner C",
                        "owner_contact_id": 520, "park": 0,
                    },
                    {
                        "id": 6, "demo_type": 1, "demo_status": 1, "demoApproval": 1,
                        "name": "Demo SO Only", "date": "2026-03-14",
                        "start_date": "2026-03-14", "end_date": "2026-03-14",
                        "start_time": "13:00", "end_time": "14:00",
                        "host_employee_id": 72, "host_contact_id": 720,
                        "sc_employee_id": 73, "sc_contact_id": 730,
                        "so_employee_id": 42, "so_contact_id": 420,
                        "batch_id": 5, "venue_id": 10,
                        "owner_id": 52, "owner_name": "Owner C",
                        "owner_contact_id": 520, "park": 0,
                    },
                    {
                        "id": 7, "demo_type": 1, "demo_status": 1, "demoApproval": 1,
                        "name": "Demo Owner Only", "date": "2026-03-16",
                        "start_date": "2026-03-16", "end_date": "2026-03-16",
                        "start_time": "15:00", "end_time": "16:00",
                        "host_employee_id": 74, "host_contact_id": 740,
                        "sc_employee_id": 75, "sc_contact_id": 750,
                        "so_employee_id": 76, "so_contact_id": 760,
                        "batch_id": 5, "venue_id": 10,
                        "owner_id": 51, "owner_name": "Owner B",
                        "owner_contact_id": 510, "park": 0,
                    },
                    {
                        "id": 8, "demo_type": 1, "demo_status": 1, "demoApproval": 0,
                        "name": "Demo Excluded demoApproval", "date": "2026-03-18",
                        "start_date": "2026-03-18", "end_date": "2026-03-18",
                        "start_time": "10:00", "end_time": "11:00",
                        "host_employee_id": 1, "host_contact_id": 100,
                        "sc_employee_id": 30, "sc_contact_id": 300,
                        "so_employee_id": 40, "so_contact_id": 400,
                        "batch_id": 5, "venue_id": 10,
                        "owner_id": 50, "owner_name": "Owner A",
                        "owner_contact_id": 500, "park": 0,
                    },
                    {
                        "id": 9, "demo_type": 1, "demo_status": 1, "demoApproval": 1,
                        "name": "Demo Excluded park", "date": "2026-03-19",
                        "start_date": "2026-03-19", "end_date": "2026-03-19",
                        "start_time": "10:00", "end_time": "11:00",
                        "host_employee_id": 1, "host_contact_id": 100,
                        "sc_employee_id": 30, "sc_contact_id": 300,
                        "so_employee_id": 40, "so_contact_id": 400,
                        "batch_id": 5, "venue_id": 10,
                        "owner_id": 50, "owner_name": "Owner A",
                        "owner_contact_id": 500, "park": 1,
                    },
                ],
            )

        engines.clear()
        engines["default"] = self.engine
        self.repository = EmployeeEventsRepository()

    def tearDown(self):
        engines.clear()
        engines.update(self._original_engines)

    def test_get_demo_events_returns_matching_rows_in_date_range(self):
        result = self.repository.get_demo_events(
            employee_ids=[100, 200],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )
        ids = [row["id"] for row in result]
        self.assertIn(1, ids)
        self.assertIn(2, ids)
        self.assertIn(3, ids)
        self.assertNotIn(4, ids)

    def test_get_demo_events_matches_via_host_contact_id(self):
        result = self.repository.get_demo_events(
            employee_ids=[100],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )
        ids = [row["id"] for row in result]
        self.assertIn(1, ids)
        self.assertIn(2, ids)

    def test_get_demo_events_matches_via_sc_contact_id(self):
        result = self.repository.get_demo_events(
            employee_ids=[310],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )
        ids = [row["id"] for row in result]
        self.assertIn(2, ids, "Demo Two has sc_contact_id=310")
        self.assertIn(5, ids, "Demo SC Only has sc_contact_id=310")

    def test_get_demo_events_matches_via_so_contact_id(self):
        result = self.repository.get_demo_events(
            employee_ids=[420],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )
        ids = [row["id"] for row in result]
        self.assertIn(3, ids, "Demo Three has so_contact_id=420")
        self.assertIn(6, ids, "Demo SO Only has so_contact_id=420")

    def test_get_demo_events_matches_via_owner_contact_id(self):
        result = self.repository.get_demo_events(
            employee_ids=[510],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )
        ids = [row["id"] for row in result]
        self.assertIn(2, ids, "Demo Two owner_contact_id=510")
        self.assertIn(7, ids, "Demo Owner Only owner_contact_id=510")

    def test_get_demo_events_or_matching_returns_union(self):
        """A contact_id matching multiple columns still returns each demo once."""
        result = self.repository.get_demo_events(
            employee_ids=[420, 510],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )
        ids = [row["id"] for row in result]
        self.assertIn(2, ids, "owner_contact_id=510 match")
        self.assertIn(3, ids, "so_contact_id=420 match")
        self.assertIn(6, ids, "so_contact_id=420 match")
        self.assertIn(7, ids, "owner_contact_id=510 match")

    def test_get_demo_events_returns_owner_columns(self):
        result = self.repository.get_demo_events(
            employee_ids=[100],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )
        self.assertEqual(2, len(result))
        self.assertEqual("Owner A", result[0]["owner_name"])
        self.assertEqual(500, result[0]["owner_contact_id"])
        self.assertEqual("Owner B", result[1]["owner_name"])

    def test_get_demo_events_filters_by_contact_ids(self):
        result = self.repository.get_demo_events(
            employee_ids=[200],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )
        ids = [row["id"] for row in result]
        self.assertIn(3, ids)

    def test_get_demo_events_excludes_out_of_range(self):
        result = self.repository.get_demo_events(
            employee_ids=[100],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )
        ids = [row["id"] for row in result]
        self.assertNotIn(4, ids)

    def test_get_demo_events_filter_by_status(self):
        result = self.repository.get_demo_events(
            employee_ids=[100],
            from_date="2026-03-01",
            to_date="2026-03-31",
            statuses=[1],
        )
        ids = [row["id"] for row in result]
        self.assertIn(1, ids)
        self.assertNotIn(2, ids, "Demo Two has demo_status=2")

    def test_get_demo_events_filter_by_type(self):
        result = self.repository.get_demo_events(
            employee_ids=[100],
            from_date="2026-03-01",
            to_date="2026-03-31",
            types=[2],
        )
        ids = [row["id"] for row in result]
        self.assertIn(2, ids)
        self.assertNotIn(1, ids, "Demo One has demo_type=1")

    def test_get_demo_events_static_filter_excludes_demo_approval_zero(self):
        """Rows with demoApproval=0 are excluded by the static filter."""
        result = self.repository.get_demo_events(
            employee_ids=[100],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )
        ids = [row["id"] for row in result]
        self.assertNotIn(8, ids, "id=8 has demoApproval=0, should be excluded")
        self.assertIn(1, ids, "id=1 has demoApproval=1, should be included")

    def test_get_demo_events_filter_by_venue_id(self):
        result = self.repository.get_demo_events(
            employee_ids=[100],
            from_date="2026-03-01",
            to_date="2026-03-31",
            venue_ids=[11],
        )
        ids = [row["id"] for row in result]
        self.assertIn(2, ids)
        self.assertNotIn(1, ids, "Demo One has venue_id=10")

    def test_get_demo_events_filter_by_batch_id(self):
        result = self.repository.get_demo_events(
            employee_ids=[100, 200],
            from_date="2026-03-01",
            to_date="2026-03-31",
            batch_ids=[6],
        )
        ids = [row["id"] for row in result]
        self.assertIn(2, ids)
        self.assertEqual(1, len(ids), "Only Demo Two has batch_id=6")

    def test_get_demo_events_static_filter_excludes_park_one(self):
        """Rows with park=1 are excluded by the static filter."""
        result = self.repository.get_demo_events(
            employee_ids=[100],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )
        ids = [row["id"] for row in result]
        self.assertNotIn(9, ids, "id=9 has park=1, should be excluded")
        self.assertIn(1, ids, "id=1 has park=0, should be included")

    def test_get_demo_events_combined_filters(self):
        result = self.repository.get_demo_events(
            employee_ids=[100, 200],
            from_date="2026-03-01",
            to_date="2026-03-31",
            statuses=[1],
            types=[1],
            venue_ids=[10],
            batch_ids=[5],
        )
        ids = [row["id"] for row in result]
        self.assertIn(1, ids)

    def test_get_demo_events_empty_employee_ids_returns_empty(self):
        result = self.repository.get_demo_events(
            employee_ids=[],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )
        self.assertEqual([], result)

    def test_get_demo_events_uses_single_select_for_batch(self):
        statement_count = {"select": 0}

        def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statement_count["select"] += 1

        event.listen(self.engine, "before_cursor_execute", _before_cursor_execute)
        try:
            result = self.repository.get_demo_events(
                employee_ids=[100, 200],
                from_date="2026-03-01",
                to_date="2026-03-31",
            )
        finally:
            event.remove(self.engine, "before_cursor_execute", _before_cursor_execute)

        self.assertGreaterEqual(len(result), 3)
        self.assertEqual(1, statement_count["select"])


if __name__ == "__main__":
    unittest.main()
