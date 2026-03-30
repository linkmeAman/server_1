"""Service-level tests for Employee Events V1 demo events batch query."""

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app.modules.employee_events_v1.dependencies import EmployeeEventsError
from app.modules.employee_events_v1.services.event_service import EmployeeEventsService


class TestDemoEventsBatchService(unittest.TestCase):
    def setUp(self):
        self.event_repo = MagicMock()
        self.sync_repo = MagicMock()

        self.settings_patcher = patch(
            "app.modules.employee_events_v1.services.event_service.get_settings",
            return_value=SimpleNamespace(
                EMP_EVENT_APPROVED_STATUS=1,
                EMP_EVENT_PARKED_VALUE=1,
                EMP_EVENT_ENABLE_GOOGLE_SYNC=False,
                EMP_EVENT_TIMEZONE="Asia/Kolkata",
                GOOGLE_CALENDAR_ID="primary",
            ),
        )
        self.settings_patcher.start()

        self.service = EmployeeEventsService(
            event_repository=self.event_repo,
            sync_repository=self.sync_repo,
        )

    def tearDown(self):
        self.settings_patcher.stop()

    def test_normalize_demo_employee_ids_rejects_empty(self):
        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_demo_events_batch(
                employee_ids=[],
                from_date="2026-03-01",
                to_date="2026-03-31",
            )
        self.assertEqual("EMP_EVENT_INVALID_DEMO_QUERY", ctx.exception.code)

    def test_normalize_demo_employee_ids_rejects_non_integers(self):
        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_demo_events_batch(
                employee_ids=["abc"],
                from_date="2026-03-01",
                to_date="2026-03-31",
            )
        self.assertEqual("EMP_EVENT_INVALID_DEMO_QUERY", ctx.exception.code)

    def test_normalize_demo_employee_ids_rejects_over_25(self):
        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_demo_events_batch(
                employee_ids=list(range(1, 27)),
                from_date="2026-03-01",
                to_date="2026-03-31",
            )
        self.assertEqual("EMP_EVENT_INVALID_DEMO_QUERY", ctx.exception.code)

    def test_normalize_demo_employee_ids_rejects_zero_and_negative(self):
        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_demo_events_batch(
                employee_ids=[0],
                from_date="2026-03-01",
                to_date="2026-03-31",
            )
        self.assertEqual("EMP_EVENT_INVALID_DEMO_QUERY", ctx.exception.code)

    def test_normalize_demo_employee_ids_rejects_boolean(self):
        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_demo_events_batch(
                employee_ids=[True],
                from_date="2026-03-01",
                to_date="2026-03-31",
            )
        self.assertEqual("EMP_EVENT_INVALID_DEMO_QUERY", ctx.exception.code)

    def test_parse_demo_date_rejects_invalid_format(self):
        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_demo_events_batch(
                employee_ids=[1],
                from_date="03-01-2026",
                to_date="2026-03-31",
            )
        self.assertEqual("EMP_EVENT_INVALID_DEMO_QUERY", ctx.exception.code)

    def test_rejects_from_after_to(self):
        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_demo_events_batch(
                employee_ids=[1],
                from_date="2026-03-31",
                to_date="2026-03-01",
            )
        self.assertEqual("EMP_EVENT_INVALID_DEMO_QUERY", ctx.exception.code)

    def test_rejects_range_over_62_days(self):
        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_demo_events_batch(
                employee_ids=[1],
                from_date="2026-01-01",
                to_date="2026-03-31",
            )
        self.assertEqual("EMP_EVENT_INVALID_DEMO_QUERY", ctx.exception.code)

    def test_valid_batch_groups_by_employee(self):
        self.event_repo.get_demo_events.return_value = [
            {"id": 10, "host_contact_id": 1, "sc_contact_id": 30, "so_contact_id": 40, "owner_contact_id": 500, "name": "Demo A", "demo_status": 1},
            {"id": 11, "host_contact_id": 1, "sc_contact_id": 31, "so_contact_id": 41, "owner_contact_id": 501, "name": "Demo B", "demo_status": 2},
            {"id": 12, "host_contact_id": 2, "sc_contact_id": 32, "so_contact_id": 42, "owner_contact_id": 502, "name": "Demo C", "demo_status": 1},
        ]

        result = self.service.get_demo_events_batch(
            employee_ids=[1, 2, 3],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )

        self.assertEqual("2026-03-01", result["from_date"])
        self.assertEqual("2026-03-31", result["to_date"])
        self.assertEqual(31, result["range_day_count"])
        self.assertEqual(3, result["employee_count"])
        self.assertEqual(2, result["matched_count"])
        self.assertEqual(3, result["total_demos"])
        self.assertEqual(3, len(result["employees"]))

        emp1 = result["employees"][0]
        self.assertEqual(1, emp1["employee_id"])
        self.assertEqual(2, emp1["demo_count"])

        emp2 = result["employees"][1]
        self.assertEqual(2, emp2["employee_id"])
        self.assertEqual(1, emp2["demo_count"])

        emp3 = result["employees"][2]
        self.assertEqual(3, emp3["employee_id"])
        self.assertEqual(0, emp3["demo_count"])

    def test_valid_batch_groups_by_sc_contact_id(self):
        self.event_repo.get_demo_events.return_value = [
            {"id": 10, "host_contact_id": 99, "sc_contact_id": 5, "so_contact_id": 88, "owner_contact_id": 500, "name": "Demo SC", "demo_status": 1},
        ]

        result = self.service.get_demo_events_batch(
            employee_ids=[5],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )

        self.assertEqual(1, result["matched_count"])
        self.assertEqual(1, result["employees"][0]["demo_count"])

    def test_valid_batch_groups_by_so_contact_id(self):
        self.event_repo.get_demo_events.return_value = [
            {"id": 10, "host_contact_id": 99, "sc_contact_id": 88, "so_contact_id": 7, "owner_contact_id": 500, "name": "Demo SO", "demo_status": 1},
        ]

        result = self.service.get_demo_events_batch(
            employee_ids=[7],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )

        self.assertEqual(1, result["matched_count"])
        self.assertEqual(1, result["employees"][0]["demo_count"])

    def test_valid_batch_groups_by_owner_contact_id(self):
        self.event_repo.get_demo_events.return_value = [
            {"id": 10, "host_contact_id": 99, "sc_contact_id": 88, "so_contact_id": 77, "owner_contact_id": 15, "name": "Demo Owner", "demo_status": 1},
        ]

        result = self.service.get_demo_events_batch(
            employee_ids=[15],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )

        self.assertEqual(1, result["matched_count"])
        self.assertEqual(1, result["employees"][0]["demo_count"])

    def test_valid_batch_demo_appears_under_multiple_employees(self):
        """Same demo matched by different columns appears under each employee."""
        self.event_repo.get_demo_events.return_value = [
            {"id": 10, "host_contact_id": 1, "sc_contact_id": 2, "so_contact_id": 3, "owner_contact_id": 4, "name": "Multi Match", "demo_status": 1},
        ]

        result = self.service.get_demo_events_batch(
            employee_ids=[1, 2, 3, 4],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )

        self.assertEqual(4, result["matched_count"])
        for emp in result["employees"]:
            self.assertEqual(1, emp["demo_count"], f"employee {emp['employee_id']} should have 1 demo")

    def test_valid_batch_deduplicates_employee_ids(self):
        self.event_repo.get_demo_events.return_value = []

        result = self.service.get_demo_events_batch(
            employee_ids=[1, 1, 2, 2],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )

        self.assertEqual(2, result["employee_count"])
        self.assertEqual(2, len(result["employees"]))

    def test_optional_filters_pass_through_to_repository(self):
        self.event_repo.get_demo_events.return_value = []

        self.service.get_demo_events_batch(
            employee_ids=[1],
            from_date="2026-03-01",
            to_date="2026-03-31",
            statuses=[1, 2],
            types=[3],
            venue_ids=[10],
            batch_ids=[5],
        )

        call_kwargs = self.event_repo.get_demo_events.call_args[1]
        self.assertEqual([1], call_kwargs["employee_ids"])
        self.assertEqual("2026-03-01", call_kwargs["from_date"])
        self.assertEqual("2026-03-31", call_kwargs["to_date"])
        self.assertEqual([1, 2], call_kwargs["statuses"])
        self.assertEqual([3], call_kwargs["types"])
        self.assertEqual([10], call_kwargs["venue_ids"])
        self.assertEqual([5], call_kwargs["batch_ids"])

    def test_demo_filter_rejects_invalid_statuses(self):
        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_demo_events_batch(
                employee_ids=[1],
                from_date="2026-03-01",
                to_date="2026-03-31",
                statuses=[-1],
            )
        self.assertEqual("EMP_EVENT_INVALID_DEMO_QUERY", ctx.exception.code)

    def test_demo_filter_rejects_invalid_venue_ids(self):
        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_demo_events_batch(
                employee_ids=[1],
                from_date="2026-03-01",
                to_date="2026-03-31",
                venue_ids=[0],
            )
        self.assertEqual("EMP_EVENT_INVALID_DEMO_QUERY", ctx.exception.code)

    def test_demo_filter_rejects_invalid_batch_ids(self):
        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_demo_events_batch(
                employee_ids=[1],
                from_date="2026-03-01",
                to_date="2026-03-31",
                batch_ids=[0],
            )
        self.assertEqual("EMP_EVENT_INVALID_DEMO_QUERY", ctx.exception.code)

    def test_unexpected_repository_error_wraps_as_500(self):
        self.event_repo.get_demo_events.side_effect = RuntimeError("db down")

        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_demo_events_batch(
                employee_ids=[1],
                from_date="2026-03-01",
                to_date="2026-03-31",
            )
        self.assertEqual("EMP_EVENT_DEMO_QUERY_FAILED", ctx.exception.code)
        self.assertEqual(500, ctx.exception.status_code)


if __name__ == "__main__":
    unittest.main()
