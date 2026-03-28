"""Service-level tests for Employee Events V1 workflows."""

from datetime import date, datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from controllers.employee_events_v1.dependencies import EmployeeEventsError
from controllers.employee_events_v1.services.event_service import EmployeeEventsService
from controllers.employee_events_v1.services.google_payload_builder import (
    build_google_event_payload,
)


class TestEmployeeEventsV1Service(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.event_repo = MagicMock()
        self.sync_repo = MagicMock()

        self.google_client = type("GoogleClient", (), {})()
        self.google_client.create_event = AsyncMock()
        self.google_client.update_event = AsyncMock()
        self.google_client.delete_event = AsyncMock()
        self.google_client.list_instances = AsyncMock()

        self.token_manager = MagicMock()
        self.token_manager.get_valid_access_token = AsyncMock(return_value="google-token")

        self.settings_patcher = patch(
            "controllers.employee_events_v1.services.event_service.get_settings",
            return_value=SimpleNamespace(
                EMP_EVENT_APPROVED_STATUS=1,
                EMP_EVENT_PARKED_VALUE=1,
                EMP_EVENT_ENABLE_GOOGLE_SYNC=True,
                EMP_EVENT_TIMEZONE="Asia/Kolkata",
                GOOGLE_CALENDAR_ID="primary",
            ),
        )
        self.settings_patcher.start()

        self.service = EmployeeEventsService(
            event_repository=self.event_repo,
            sync_repository=self.sync_repo,
            google_client=self.google_client,
            token_manager=self.token_manager,
        )

        self.event_row = {
            "id": 10,
            "category": "Meeting",
            "contact_id": 123,
            "branch": "Mumbai",
            "type": "Interview",
            "lease_type": "N/A",
            "amount": 1000,
            "deduction_amount": 50,
            "date": "2026-03-15",
            "start_time": "10:00:00",
            "end_time": "11:00:00",
            "allowance": 0,
            "status": 1,
            "park": 0,
        }

    def tearDown(self):
        self.settings_patcher.stop()

    def test_conflict_check_true_and_false(self):
        self.event_repo.check_conflict.return_value = [4, 8]
        conflict = self.service.check_conflict(
            date="2026-03-15",
            start_time="10:00:00",
            end_time="11:00:00",
            contact_id=123,
        )
        self.assertTrue(conflict["conflict"])
        self.assertEqual([4, 8], conflict["conflict_event_ids"])
        self.event_repo.check_conflict.assert_called_once_with(
            date="2026-03-15",
            start_time="10:00:00",
            end_time="11:00:00",
            contact_id=123,
            parked_value=1,
            exclude_event_id=None,
        )

        self.event_repo.check_conflict.return_value = []
        no_conflict = self.service.check_conflict(
            date="2026-03-16",
            start_time="12:00:00",
            end_time="13:00:00",
            contact_id=123,
        )
        self.assertFalse(no_conflict["conflict"])
        self.assertEqual([], no_conflict["conflict_event_ids"])

    def test_create_event_local_only_pending_approval(self):
        self.event_repo.create_event_with_allowances.return_value = 99
        self.sync_repo.upsert_pending.return_value = {"sync_status": "pending_approval"}

        result = self.service.create_event(
            payload={
                "category": "Meeting",
                "contact_id": 123,
                "branch": "Mumbai",
                "type": "Interview",
                "lease_type": "N/A",
                "amount": 1000,
                "deduction_amount": 0,
                "date": "2026-03-15",
                "start_time": "10:00:00",
                "end_time": "11:00:00",
                "allowance": 0,
                "allowance_items": [],
            },
            actor_user_id="42",
        )

        self.assertEqual(99, result["event_id"])
        self.assertEqual("pending_approval", result["sync_status"])
        self.sync_repo.upsert_pending.assert_called_once_with(99, "primary")
        self.google_client.create_event.assert_not_awaited()

    def test_list_events_returns_merged_payload(self):
        self.event_repo.list_events.return_value = [
            {
                "id": 10,
                "category": "Workshop",
                "contact_id": 123,
                "date": "2026-03-15",
                "start_time": "10:00:00",
                "end_time": "11:00:00",
                "contact_lookup_id": 123,
                "contact_fname": "Asha",
                "contact_mname": "",
                "contact_lname": "Verma",
                "contact_parent_name": "Parent",
                "contact_country_code": "+91",
                "contact_mobile": "9999999999",
                "contact_email": "asha@example.com",
            }
        ]
        self.event_repo.get_allowances_for_event_ids.return_value = {
            10: [{"event_id": 10, "name": "Travel", "amount": 200, "created_by": "42"}]
        }
        self.sync_repo.get_links_by_event_ids.return_value = {
            10: {
                "employee_event_id": 10,
                "google_event_id": "g_evt_1",
                "google_calendar_id": "primary",
                "sync_status": "active",
                "last_error_code": None,
                "last_error_message": None,
                "updated_at": "2026-03-01T00:00:00Z",
            }
        }

        result = self.service.list_events(from_date="2026-03-01", to_date="2026-03-31")

        self.assertEqual(1, result["count"])
        event = result["events"][0]
        self.assertEqual(10, event["id"])
        self.assertEqual("Asha Verma", event["contact"]["full_name"])
        self.assertEqual("active", event["sync"]["sync_status"])
        self.assertEqual(1, len(event["allowance_items"]))
        self.event_repo.list_events.assert_called_once()
        self.event_repo.get_allowances_for_event_ids.assert_called_once_with([10])
        self.sync_repo.get_links_by_event_ids.assert_called_once_with([10])

    def test_get_realtime_employee_data(self):
        self.event_repo.list_realtime_employees.return_value = [
            {
                "id": 1,
                "contact_id": 10,
                "fullname": "Sneha Trainer",
                "position_id": 2,
                "position": "Trainer",
                "bid": 5,
            }
        ]
        self.event_repo.list_active_branches.return_value = [
            {"id": 1, "branch": "Bandra", "type": "HQ"}
        ]

        result = self.service.get_realtime_employee_data()

        self.assertEqual(1, result["employee_count"])
        self.assertEqual(1, result["branch_count"])
        self.assertEqual("Sneha Trainer", result["employees"][0]["fullname"])
        self.assertEqual("Bandra", result["branches"][0]["branch"])
        self.event_repo.list_realtime_employees.assert_called_once()
        self.event_repo.list_active_branches.assert_called_once()

    def test_get_active_venues(self):
        self.event_repo.list_active_venues.return_value = [
            {"id": 10, "venue": "Andheri Center", "display_name": "Andheri Center"},
            {"id": 20, "venue": "Bandra Center", "display_name": "Bandra Center"},
        ]

        result = self.service.get_active_venues()

        self.assertEqual(2, result["total_count"])
        self.assertEqual("Andheri Center", result["venues"][0]["venue"])
        self.event_repo.list_active_venues.assert_called_once()

    def test_get_active_batches_by_venue_dedupes_ids_and_returns_rows(self):
        self.event_repo.list_active_batches_by_venue_ids.return_value = [
            {
                "id": 123,
                "batch": "Offline B87",
                "display_name": "Offline B87",
                "venue_id": 10,
                "venue": "Andheri Center",
                "parent_id": 0,
                "date": "2026-03-10",
                "start_date": "2026-03-10",
                "end_date": "2026-03-10",
                "start_time": "10:00:00",
                "end_time": "11:00:00",
                "title": "Offline B87",
                "timezone_id": "Asia/Kolkata",
                "branch": "Mumbai",
                "bid": 7,
                "parent_batch_name": None,
            }
        ]

        result = self.service.get_active_batches_by_venue([10, "10", 20, 20])

        self.assertEqual([10, 20], result["venue_ids"])
        self.assertEqual(1, result["total_count"])
        self.assertEqual(123, result["batches"][0]["batch_id"])
        self.assertEqual("Offline B87", result["batches"][0]["batch_name"])
        self.assertEqual("Offline B87", result["batches"][0]["summary"])
        self.assertEqual("Andheri Center", result["batches"][0]["location"])
        self.assertEqual("original", result["batches"][0]["batch_type"])
        self.assertEqual("2026-03-10 10:00:00", result["batches"][0]["event_start"])
        self.event_repo.list_active_batches_by_venue_ids.assert_called_once_with([10, 20])

    def test_get_active_batches_by_venue_rejects_invalid_ids(self):
        invalid_cases = [
            [],
            [0],
            [-1],
            [True],
            ["bad"],
            list(range(1, 27)),
        ]

        for venue_ids in invalid_cases:
            with self.subTest(venue_ids=venue_ids):
                with self.assertRaises(EmployeeEventsError) as ctx:
                    self.service.get_active_batches_by_venue(venue_ids)
                self.assertEqual("EMP_EVENT_INVALID_BATCH_QUERY", ctx.exception.code)

    def test_get_batch_kids_present_returns_window_and_rows(self):
        self.event_repo.list_batch_kids_present.return_value = [
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
            }
        ]

        with patch.object(
            self.service,
            "_current_date_in_workshift_timezone",
            return_value=date(2026, 3, 28),
        ):
            result = self.service.get_batch_kids_present(700)

        self.assertEqual(700, result["batch_id"])
        self.assertEqual("2026-03-20", result["from_date"])
        self.assertEqual("2026-06-26", result["to_date"])
        self.assertEqual(1, result["total_count"])
        self.assertEqual(11, result["kids"][0]["item_id"])
        self.event_repo.list_batch_kids_present.assert_called_once_with(
            batch_id=700,
            from_date="2026-03-20",
            to_date="2026-06-26",
        )

    def test_get_batch_kids_present_rejects_invalid_batch_id(self):
        for invalid_value in (0, -1, True, "bad"):
            with self.subTest(invalid_value=invalid_value):
                with self.assertRaises(EmployeeEventsError) as ctx:
                    self.service.get_batch_kids_present(invalid_value)
                self.assertEqual("EMP_EVENT_INVALID_BATCH_KIDS_QUERY", ctx.exception.code)

    def test_get_batch_kids_present_wraps_repository_errors(self):
        self.event_repo.list_batch_kids_present.side_effect = RuntimeError("db exploded")

        with patch.object(
            self.service,
            "_current_date_in_workshift_timezone",
            return_value=date(2026, 3, 28),
        ):
            with self.assertRaises(EmployeeEventsError) as ctx:
                self.service.get_batch_kids_present(700)

        self.assertEqual("EMP_EVENT_BATCH_KIDS_QUERY_FAILED", ctx.exception.code)

    def test_get_trainer_calendar_events_returns_unified_merged_events(self):
        self.event_repo.list_events.return_value = [
            {
                "id": 10,
                "category": "Workshop",
                "description": "Employee session",
                "contact_id": 123,
                "date": "2026-03-10",
                "start_time": "10:00:00",
                "end_time": "11:00:00",
                "park": 0,
                "status": 1,
                "contact_lookup_id": 123,
                "contact_fname": "Asha",
                "contact_mname": "",
                "contact_lname": "Verma",
                "contact_parent_name": "Parent",
                "contact_country_code": "+91",
                "contact_mobile": "9999999999",
                "contact_email": "asha@example.com",
            }
        ]
        self.event_repo.get_allowances_for_event_ids.return_value = {10: []}
        self.sync_repo.get_links_by_event_ids.return_value = {
            10: {
                "employee_event_id": 10,
                "google_event_id": "g_evt_1",
                "google_calendar_id": "primary",
                "sync_status": "active",
                "last_error_code": None,
                "last_error_message": None,
                "updated_at": "2026-03-01T00:00:00Z",
            }
        }
        self.event_repo.list_trainer_calendar_events.return_value = [
            {
                "id": 100,
                "batch": "Batch A",
                "display_name": "Batch A Display",
                "parent_id": None,
                "date": "2026-03-01",
                "start_date": "2026-03-01",
                "end_date": "2026-03-31",
                "start_time": "10:00:00",
                "end_time": "11:30:00",
                "day_code": "2",
                "title": "Morning A",
                "venue": "Centre A",
                "timezone_id": "Asia/Kolkata",
                "parent_batch_name": None,
            },
            {
                "id": 101,
                "batch": "Batch A Reschedule",
                "display_name": "Rescheduled A",
                "parent_id": 100,
                "date": "2026-03-12",
                "start_date": "2026-03-12",
                "end_date": "2026-03-12",
                "start_time": "14:00:00",
                "end_time": "15:30:00",
                "day_code": "",
                "title": "Reschedule A",
                "venue": "Centre B",
                "timezone_id": "Asia/Kolkata",
                "parent_batch_name": "Batch A",
            },
        ]

        result = self.service.get_trainer_calendar_events(
            contact_id=123,
            from_date="2026-03-10",
            to_date="2026-03-14",
        )

        self.assertEqual(3, result["total_count"])
        self.assertEqual("employee_event", result["events"][0]["source"])
        self.assertEqual("employee_10", result["events"][0]["source_event_id"])
        self.assertFalse(result["events"][0]["is_read_only"])
        self.assertEqual("2026-03-10 10:00:00", result["events"][0]["start"])
        self.assertEqual("2026-03-10 11:00:00", result["events"][0]["end"])
        self.assertEqual("trainer_batch", result["events"][1]["source"])
        self.assertEqual("trainer_100_20260310", result["events"][1]["source_event_id"])
        self.assertTrue(result["events"][1]["is_read_only"])
        self.assertEqual("2026-03-10 10:00:00", result["events"][1]["start"])
        self.assertEqual("2026-03-10 11:30:00", result["events"][1]["end"])
        self.assertEqual("original", result["events"][1]["raw"]["batch_type"])
        self.assertEqual(1, result["events"][1]["raw"]["is_recurring"])
        self.assertEqual("trainer_batch", result["events"][2]["source"])
        self.assertEqual("trainer_101_20260312", result["events"][2]["source_event_id"])
        self.assertEqual("prescheduled", result["events"][2]["raw"]["batch_type"])
        self.assertEqual(0, result["events"][2]["raw"]["is_recurring"])
        self.assertEqual("[]", result["events"][2]["raw"]["attendees"])
        self.event_repo.list_trainer_calendar_events.assert_called_once_with(
            contact_id=123,
            from_date="2026-03-10",
            to_date="2026-03-14",
        )
        self.event_repo.list_events.assert_called_once_with(
            from_date="2026-03-10",
            to_date="2026-03-14",
            contact_id=123,
            status=None,
            park=None,
            include_parked=False,
            parked_value=1,
        )

    def test_get_trainer_calendar_events_invalid_date_raises_400(self):
        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_trainer_calendar_events(
                contact_id=123,
                from_date="03-01-2026",
                to_date="2026-03-31",
            )

        self.assertEqual("EMP_EVENT_INVALID_CALENDAR_QUERY", ctx.exception.code)
        self.assertEqual(400, ctx.exception.status_code)

    def test_get_trainer_calendar_events_invalid_range_raises_400(self):
        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_trainer_calendar_events(
                contact_id=123,
                from_date="2026-03-31",
                to_date="2026-03-01",
            )

        self.assertEqual("EMP_EVENT_INVALID_CALENDAR_QUERY", ctx.exception.code)
        self.assertEqual(400, ctx.exception.status_code)

    def test_get_trainer_calendar_events_uses_bounded_default_range(self):
        self.service.list_events = MagicMock(return_value={"events": [], "count": 0})
        self.event_repo.list_trainer_calendar_events.return_value = []

        result = self.service.get_trainer_calendar_events(contact_id=123)

        self.assertEqual(0, result["total_count"])
        employee_call = self.service.list_events.call_args.kwargs
        trainer_call = self.event_repo.list_trainer_calendar_events.call_args.kwargs

        from_date_text = employee_call["from_date"]
        to_date_text = employee_call["to_date"]
        from_date_value = datetime.strptime(from_date_text, "%Y-%m-%d").date()
        to_date_value = datetime.strptime(to_date_text, "%Y-%m-%d").date()
        self.assertEqual(90, (to_date_value - from_date_value).days)
        self.assertEqual(from_date_text, trainer_call["from_date"])
        self.assertEqual(to_date_text, trainer_call["to_date"])

    def test_get_trainer_calendar_events_wraps_repository_errors(self):
        self.service.list_events = MagicMock(return_value={"events": [], "count": 0})
        self.event_repo.list_trainer_calendar_events.side_effect = EmployeeEventsError(
            code="EMP_EVENT_LEAVE_QUERY_FAILED",
            message="query failed",
            status_code=500,
        )

        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_trainer_calendar_events(contact_id=123)

        self.assertEqual("EMP_EVENT_CALENDAR_QUERY_FAILED", ctx.exception.code)
        self.assertEqual(500, ctx.exception.status_code)
        self.assertEqual("EMP_EVENT_LEAVE_QUERY_FAILED", ctx.exception.data.get("reason"))

    def test_get_trainer_calendar_events_parent_without_day_code_falls_back_to_single_date(self):
        self.service.list_events = MagicMock(return_value={"events": [], "count": 0})
        self.event_repo.list_trainer_calendar_events.return_value = [
            {
                "id": 1,
                "batch": "A",
                "display_name": "A Display",
                "parent_id": None,
                "date": "2026-03-10",
                "start_date": "2026-03-01",
                "end_date": "2026-03-31",
                "start_time": "13:30:00",
                "end_time": "14:30:00",
                "day_code": "",
                "title": "A Title",
                "venue": "",
                "timezone_id": "Asia/Kolkata",
                "parent_batch_name": None,
            },
        ]

        result = self.service.get_trainer_calendar_events(
            contact_id=123,
            from_date="2026-03-01",
            to_date="2026-03-12",
        )

        self.assertEqual(1, result["total_count"])
        self.assertEqual("trainer_batch", result["events"][0]["source"])
        self.assertEqual("trainer_1_20260310", result["events"][0]["source_event_id"])
        self.assertEqual("2026-03-10 13:30:00", result["events"][0]["start"])

    def test_get_employee_workshift_calendar_batch_standard_shift_with_week_offs(self):
        self.event_repo.get_employee_workshifts.return_value = [
            {
                "employee_id": 11,
                "employee_name": "Sneha Trainer",
                "workshift_id": 0,
                "workshift_in_time": "10:00",
                "workshift_out_time": "19:00:00",
                "week_off_code": "0,6",
            }
        ]

        result = self.service.get_employee_workshift_calendar_batch(
            employee_ids=[11],
            from_date="2026-03-01",
            to_date="2026-03-03",
        )

        self.assertEqual("Asia/Kolkata", result["timezone"])
        self.assertEqual(1, result["employee_count"])
        self.assertEqual(1, result["matched_count"])
        employee = result["employees"][0]
        self.assertEqual("configured", employee["result_status"])
        self.assertEqual(3, employee["day_count"])
        self.assertEqual([0, 6], employee["workshift"]["week_off_days"])
        self.assertIsNone(employee["calendar_days"][0]["shift_start"])
        self.assertFalse(employee["calendar_days"][0]["is_overnight"])
        self.assertTrue(employee["calendar_days"][1]["shift_start"].startswith("2026-03-02T10:00:00"))
        self.event_repo.get_employee_workshifts.assert_called_once_with([11])

    def test_get_employee_workshift_calendar_batch_overnight_shift(self):
        self.event_repo.get_employee_workshifts.return_value = [
            {
                "employee_id": 7,
                "employee_name": "Night Shift",
                "workshift_id": 0,
                "workshift_in_time": "22:00:00",
                "workshift_out_time": "06:00:00",
                "week_off_code": "",
            }
        ]

        result = self.service.get_employee_workshift_calendar_batch(
            employee_ids=[7],
            from_date="2026-03-02",
            to_date="2026-03-02",
        )

        day = result["employees"][0]["calendar_days"][0]
        self.assertTrue(day["is_overnight"])
        self.assertTrue(day["shift_end"].startswith("2026-03-03T06:00:00"))

    def test_get_employee_workshift_calendar_batch_unconfigured_for_invalid_times(self):
        self.event_repo.get_employee_workshifts.return_value = [
            {
                "employee_id": 8,
                "employee_name": "Broken Shift",
                "workshift_id": 0,
                "workshift_in_time": "25:99",
                "workshift_out_time": None,
                "week_off_code": "1 2",
            }
        ]

        result = self.service.get_employee_workshift_calendar_batch(
            employee_ids=[8],
            from_date="2026-03-02",
            to_date="2026-03-03",
        )

        employee = result["employees"][0]
        self.assertEqual("unconfigured", employee["result_status"])
        self.assertEqual(["invalid_in_time", "missing_out_time"], employee["workshift"]["configuration_issues"])
        self.assertEqual("25:99", employee["workshift"]["workshift_in_time"])
        self.assertIsNone(employee["workshift"]["workshift_out_time"])
        self.assertEqual([], employee["calendar_days"])

    def test_get_employee_workshift_calendar_batch_empty_week_off_code(self):
        self.event_repo.get_employee_workshifts.return_value = [
            {
                "employee_id": 9,
                "employee_name": "Weekday Shift",
                "workshift_id": 0,
                "workshift_in_time": "09:00:00",
                "workshift_out_time": "18:00:00",
                "week_off_code": None,
            }
        ]

        result = self.service.get_employee_workshift_calendar_batch(
            employee_ids=[9],
            from_date="2026-03-02",
            to_date="2026-03-02",
        )

        employee = result["employees"][0]
        self.assertEqual([], employee["warnings"])
        self.assertEqual([], employee["workshift"]["week_off_days"])
        self.assertEqual("configured", employee["result_status"])

    def test_get_employee_workshift_calendar_batch_invalid_week_off_tokens_warning(self):
        self.event_repo.get_employee_workshifts.return_value = [
            {
                "employee_id": 10,
                "employee_name": "Odd Shift",
                "workshift_id": 0,
                "workshift_in_time": "08:00",
                "workshift_out_time": "17:00",
                "week_off_code": "0, x, 9, x",
            }
        ]

        result = self.service.get_employee_workshift_calendar_batch(
            employee_ids=[10],
            from_date="2026-03-01",
            to_date="2026-03-01",
        )

        employee = result["employees"][0]
        self.assertEqual([0], employee["workshift"]["week_off_days"])
        self.assertEqual(
            ["invalid_week_off_token:x", "invalid_week_off_token:9"],
            employee["warnings"],
        )

    def test_get_employee_workshift_calendar_batch_mixed_statuses_and_order_after_dedupe(self):
        self.event_repo.get_employee_workshifts.return_value = [
            {
                "employee_id": 8,
                "employee_name": "Broken Shift",
                "workshift_id": 0,
                "workshift_in_time": "10:00:00",
                "workshift_out_time": "bad",
                "week_off_code": "",
            },
            {
                "employee_id": 7,
                "employee_name": "Configured Shift",
                "workshift_id": 0,
                "workshift_in_time": "10:00:00",
                "workshift_out_time": "19:00:00",
                "week_off_code": "",
            },
        ]

        result = self.service.get_employee_workshift_calendar_batch(
            employee_ids=[7, 7, 8, 9],
            from_date="2026-03-02",
            to_date="2026-03-03",
        )

        self.assertEqual(3, result["employee_count"])
        self.assertEqual(2, result["matched_count"])
        self.assertEqual([7, 8, 9], [item["employee_id"] for item in result["employees"]])
        self.assertEqual(
            ["configured", "unconfigured", "not_found"],
            [item["result_status"] for item in result["employees"]],
        )
        self.event_repo.get_employee_workshifts.assert_called_once_with([7, 8, 9])

    def test_get_employee_workshift_calendar_batch_invalid_timezone_raises(self):
        self.event_repo.get_employee_workshifts.return_value = []

        with patch(
            "controllers.employee_events_v1.services.event_service.get_settings",
            return_value=SimpleNamespace(
                EMP_EVENT_APPROVED_STATUS=1,
                EMP_EVENT_PARKED_VALUE=1,
                EMP_EVENT_ENABLE_GOOGLE_SYNC=True,
                EMP_EVENT_TIMEZONE="Mars/Phobos",
                GOOGLE_CALENDAR_ID="primary",
            ),
        ):
            service = EmployeeEventsService(
                event_repository=self.event_repo,
                sync_repository=self.sync_repo,
                google_client=self.google_client,
                token_manager=self.token_manager,
            )
            with self.assertRaises(EmployeeEventsError) as ctx:
                service.get_employee_workshift_calendar_batch(
                    employee_ids=[1],
                    from_date="2026-03-01",
                    to_date="2026-03-01",
                )

        self.assertEqual("EMP_EVENT_SERVICE_MISCONFIGURED", ctx.exception.code)

    def test_get_employee_leave_calendar_batch_preserves_deduped_order_and_mixed_statuses(self):
        self.event_repo.get_active_employees.return_value = [
            {"employee_id": 7, "employee_name": "Configured Shift", "department_id": 5},
            {"employee_id": 8, "employee_name": "No Leave", "department_id": 6},
        ]
        self.event_repo.get_employee_leave_requests.return_value = [
            {
                "leave_request_id": 101,
                "employee_id": 7,
                "employee_name": "Configured Shift",
                "department_id": 5,
                "start_date": "2026-03-01",
                "end_date": "2026-03-01",
                "status": 1,
                "request_type": 1,
            }
        ]

        result = self.service.get_employee_leave_calendar_batch(
            employee_ids=[7, 7, 8, 9],
            from_date="2026-03-01",
            to_date="2026-03-03",
        )

        self.assertEqual(3, result["employee_count"])
        self.assertEqual(2, result["matched_count"])
        self.assertEqual([7, 8, 9], [row["employee_id"] for row in result["employees"]])
        self.assertEqual(
            ["has_events", "no_events", "not_found"],
            [row["result_status"] for row in result["employees"]],
        )
        self.event_repo.get_active_employees.assert_called_once_with([7, 8, 9])
        self.event_repo.get_employee_leave_requests.assert_called_once_with(
            employee_ids=[7, 8],
            from_date="2026-03-01",
            to_date="2026-03-03",
            statuses=None,
            request_types=None,
            department_ids=None,
        )

    def test_get_employee_leave_calendar_batch_status_labels_and_unknown_warning(self):
        self.event_repo.get_active_employees.return_value = [
            {"employee_id": 11, "employee_name": "Status User", "department_id": 3},
        ]
        self.event_repo.get_employee_leave_requests.return_value = [
            {
                "leave_request_id": 1,
                "employee_id": 11,
                "employee_name": "Status User",
                "department_id": 3,
                "start_date": "2026-03-01",
                "end_date": "2026-03-01",
                "status": 0,
                "request_type": 1,
            },
            {
                "leave_request_id": 2,
                "employee_id": 11,
                "employee_name": "Status User",
                "department_id": 3,
                "start_date": "2026-03-02",
                "end_date": "2026-03-02",
                "status": 1,
                "request_type": 1,
            },
            {
                "leave_request_id": 3,
                "employee_id": 11,
                "employee_name": "Status User",
                "department_id": 3,
                "start_date": "2026-03-03",
                "end_date": "2026-03-03",
                "status": 2,
                "request_type": 1,
            },
            {
                "leave_request_id": 4,
                "employee_id": 11,
                "employee_name": "Status User",
                "department_id": 3,
                "start_date": "2026-03-04",
                "end_date": "2026-03-04",
                "status": 9,
                "request_type": 1,
            },
        ]

        result = self.service.get_employee_leave_calendar_batch(
            employee_ids=[11],
            from_date="2026-03-01",
            to_date="2026-03-04",
        )

        employee = result["employees"][0]
        self.assertEqual(
            ["Pending", "Approved", "Rejected", "Unknown(9)"],
            [event["status_label"] for event in employee["leave_events"]],
        )
        self.assertIn("unknown_status:9", employee["warnings"])

    def test_get_employee_leave_calendar_batch_request_type_color_rules(self):
        self.event_repo.get_active_employees.return_value = [
            {"employee_id": 12, "employee_name": "Type User", "department_id": 4},
        ]
        self.event_repo.get_employee_leave_requests.return_value = [
            {
                "leave_request_id": 10,
                "employee_id": 12,
                "employee_name": "Type User",
                "department_id": 4,
                "start_date": "2026-03-01",
                "end_date": "2026-03-01",
                "status": 1,
                "request_type": 3,
            },
            {
                "leave_request_id": 11,
                "employee_id": 12,
                "employee_name": "Type User",
                "department_id": 4,
                "start_date": "2026-03-02",
                "end_date": "2026-03-02",
                "status": 0,
                "request_type": 1,
            },
        ]

        result = self.service.get_employee_leave_calendar_batch(
            employee_ids=[12],
            from_date="2026-03-01",
            to_date="2026-03-02",
        )

        events = result["employees"][0]["leave_events"]
        self.assertEqual("Half Day", events[0]["request_type_name"])
        self.assertEqual("#FFCCBE", events[0]["color"])
        self.assertEqual("Leave", events[1]["request_type_name"])
        self.assertEqual("#C7203D", events[1]["color"])

    def test_get_employee_leave_calendar_batch_unknown_request_type_warning(self):
        self.event_repo.get_active_employees.return_value = [
            {"employee_id": 13, "employee_name": "Unknown Type", "department_id": 8},
        ]
        self.event_repo.get_employee_leave_requests.return_value = [
            {
                "leave_request_id": 15,
                "employee_id": 13,
                "employee_name": "Unknown Type",
                "department_id": 8,
                "start_date": "2026-03-01",
                "end_date": "2026-03-01",
                "status": 1,
                "request_type": 99,
            },
        ]

        result = self.service.get_employee_leave_calendar_batch(
            employee_ids=[13],
            from_date="2026-03-01",
            to_date="2026-03-01",
        )

        employee = result["employees"][0]
        event = employee["leave_events"][0]
        self.assertEqual("Unknown", event["request_type_name"])
        self.assertEqual("#9CA3AF", event["color"])
        self.assertIn("unknown_request_type:99", employee["warnings"])

    def test_get_employee_leave_calendar_batch_date_only_defaults(self):
        self.event_repo.get_active_employees.return_value = [
            {"employee_id": 14, "employee_name": "Date User", "department_id": 2},
        ]
        self.event_repo.get_employee_leave_requests.return_value = [
            {
                "leave_request_id": 20,
                "employee_id": 14,
                "employee_name": "Date User",
                "department_id": 2,
                "start_date": "2026-03-05",
                "end_date": "2026-03-05",
                "status": 1,
                "request_type": 1,
            },
        ]

        result = self.service.get_employee_leave_calendar_batch(
            employee_ids=[14],
            from_date="2026-03-05",
            to_date="2026-03-05",
        )

        event = result["employees"][0]["leave_events"][0]
        self.assertTrue(event["start"].startswith("2026-03-05T09:00:00"))
        self.assertTrue(event["end"].startswith("2026-03-05T17:00:00"))
        self.assertTrue(event["start"].endswith("+05:30"))
        self.assertTrue(event["end"].endswith("+05:30"))

    def test_get_employee_leave_calendar_batch_empty_filters_mean_no_filter(self):
        self.event_repo.get_active_employees.return_value = [
            {"employee_id": 15, "employee_name": "Filter User", "department_id": 2},
        ]
        self.event_repo.get_employee_leave_requests.return_value = []

        result = self.service.get_employee_leave_calendar_batch(
            employee_ids=[15],
            from_date="2026-03-01",
            to_date="2026-03-02",
            statuses=[],
            request_types=[],
            department_ids=[],
        )

        self.assertEqual([], result["filters_applied"]["statuses"])
        self.assertEqual([], result["filters_applied"]["request_types"])
        self.assertEqual([], result["filters_applied"]["department_ids"])
        self.event_repo.get_employee_leave_requests.assert_called_once_with(
            employee_ids=[15],
            from_date="2026-03-01",
            to_date="2026-03-02",
            statuses=None,
            request_types=None,
            department_ids=None,
        )

    def test_get_employee_leave_calendar_batch_warning_dedupe_and_invalid_datetime(self):
        self.event_repo.get_active_employees.return_value = [
            {"employee_id": 16, "employee_name": "Warn User", "department_id": 2},
        ]
        self.event_repo.get_employee_leave_requests.return_value = [
            {
                "leave_request_id": 30,
                "employee_id": 16,
                "employee_name": "Warn User",
                "department_id": 2,
                "start_date": None,
                "end_date": None,
                "status": 9,
                "request_type": 99,
            },
            {
                "leave_request_id": 31,
                "employee_id": 16,
                "employee_name": "Warn User",
                "department_id": 2,
                "start_date": "2026-03-02",
                "end_date": "2026-03-02",
                "status": 9,
                "request_type": 99,
            },
        ]

        result = self.service.get_employee_leave_calendar_batch(
            employee_ids=[16],
            from_date="2026-03-01",
            to_date="2026-03-03",
        )

        employee = result["employees"][0]
        self.assertEqual(1, employee["leave_event_count"])
        self.assertEqual(
            ["unknown_status:9", "unknown_request_type:99", "invalid_leave_datetime:30"],
            employee["warnings"],
        )

    def test_get_employee_leave_calendar_batch_events_are_sorted_by_start_then_id(self):
        self.event_repo.get_active_employees.return_value = [
            {"employee_id": 17, "employee_name": "Sort User", "department_id": 2},
        ]
        self.event_repo.get_employee_leave_requests.return_value = [
            {
                "leave_request_id": 5,
                "employee_id": 17,
                "employee_name": "Sort User",
                "department_id": 2,
                "start_date": "2026-03-02 10:00:00",
                "end_date": "2026-03-02 11:00:00",
                "status": 1,
                "request_type": 1,
            },
            {
                "leave_request_id": 4,
                "employee_id": 17,
                "employee_name": "Sort User",
                "department_id": 2,
                "start_date": "2026-03-01 10:00:00",
                "end_date": "2026-03-01 11:00:00",
                "status": 1,
                "request_type": 1,
            },
            {
                "leave_request_id": 3,
                "employee_id": 17,
                "employee_name": "Sort User",
                "department_id": 2,
                "start_date": "2026-03-01 10:00:00",
                "end_date": "2026-03-01 11:00:00",
                "status": 1,
                "request_type": 1,
            },
        ]

        result = self.service.get_employee_leave_calendar_batch(
            employee_ids=[17],
            from_date="2026-03-01",
            to_date="2026-03-02",
        )

        leave_ids = [event["leave_request_id"] for event in result["employees"][0]["leave_events"]]
        self.assertEqual([3, 4, 5], leave_ids)

    def test_get_employee_leave_calendar_batch_invalid_filter_values_raise(self):
        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_employee_leave_calendar_batch(
                employee_ids=[18],
                from_date="2026-03-01",
                to_date="2026-03-02",
                statuses=[-1],
            )

        self.assertEqual("EMP_EVENT_INVALID_LEAVE_QUERY", ctx.exception.code)

    def test_get_employee_leave_calendar_batch_db_unavailable_passthrough(self):
        self.event_repo.get_active_employees.side_effect = EmployeeEventsError(
            code="EMP_EVENT_DB_UNAVAILABLE",
            message="Main DB engine is not available",
            status_code=503,
        )

        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_employee_leave_calendar_batch(
                employee_ids=[19],
                from_date="2026-03-01",
                to_date="2026-03-02",
            )

        self.assertEqual("EMP_EVENT_DB_UNAVAILABLE", ctx.exception.code)

    def test_get_employee_leave_calendar_batch_unexpected_error_wrapped(self):
        self.event_repo.get_active_employees.side_effect = RuntimeError("boom")

        with self.assertRaises(EmployeeEventsError) as ctx:
            self.service.get_employee_leave_calendar_batch(
                employee_ids=[20],
                from_date="2026-03-01",
                to_date="2026-03-02",
            )

        self.assertEqual("EMP_EVENT_LEAVE_QUERY_FAILED", ctx.exception.code)

    async def test_approve_success_creates_google_event_and_marks_active(self):
        self.event_repo.get_event.return_value = dict(self.event_row, status=0, park=0)
        self.event_repo.get_allowances.return_value = []
        self.event_repo.get_contact.return_value = None
        self.sync_repo.upsert_pending.return_value = {"sync_status": "pending_approval"}
        self.google_client.create_event.return_value = (201, {"id": "g_evt_1"})

        with patch(
            "controllers.employee_events_v1.services.event_service.build_google_event_payload",
            return_value={"summary": "S"},
        ):
            result = await self.service.approve_event(event_id=10, requested_status=1)

        self.assertEqual("active", result["sync_status"])
        self.assertEqual("google_created", result["sync_action"])
        self.assertEqual("g_evt_1", result["google_event_id"])
        self.event_repo.set_status.assert_called_once_with(10, 1)
        self.sync_repo.mark_active.assert_called_once_with(10, "g_evt_1", "primary")
        self.google_client.create_event.assert_awaited_once_with(
            "primary", {"summary": "S"}, "google-token"
        )

    async def test_approve_is_idempotent_when_already_active(self):
        self.event_repo.get_event.return_value = dict(self.event_row, status=1, park=0)
        self.sync_repo.upsert_pending.return_value = {
            "sync_status": "active",
            "google_event_id": "g_evt_1",
        }

        result = await self.service.approve_event(event_id=10, requested_status=1)

        self.assertEqual("idempotent", result["sync_action"])
        self.assertEqual("g_evt_1", result["google_event_id"])
        self.google_client.create_event.assert_not_awaited()
        self.event_repo.set_status.assert_called_once_with(10, 1)

    async def test_approve_google_create_failure_does_not_set_status(self):
        self.event_repo.get_event.return_value = dict(self.event_row, status=0, park=0)
        self.event_repo.get_allowances.return_value = []
        self.event_repo.get_contact.return_value = None
        self.sync_repo.upsert_pending.return_value = {"sync_status": "pending_approval"}
        self.google_client.create_event.return_value = (
            400,
            {"error": {"code": 400, "message": "Invalid payload"}},
        )

        with patch(
            "controllers.employee_events_v1.services.event_service.build_google_event_payload",
            return_value={"summary": "S"},
        ):
            with self.assertRaises(EmployeeEventsError) as ctx:
                await self.service.approve_event(event_id=10, requested_status=1)

        self.assertEqual("EMP_EVENT_SYNC_FAILED", ctx.exception.code)
        self.event_repo.set_status.assert_not_called()
        self.sync_repo.mark_error.assert_called_once_with(
            10,
            "create_failed",
            "EMP_EVENT_SYNC_FAILED",
            "Invalid payload",
        )

    async def test_reject_after_approved_deletes_google_event(self):
        self.event_repo.get_event.return_value = dict(self.event_row, status=1, park=0)
        self.sync_repo.get_link.return_value = {"google_event_id": "g_evt_1"}
        self.google_client.delete_event.return_value = (204, {})

        result = await self.service.approve_event(event_id=10, requested_status=2)

        self.event_repo.set_status.assert_called_once_with(10, 2)
        self.google_client.delete_event.assert_awaited_once_with(
            "primary", "g_evt_1", "google-token"
        )
        self.sync_repo.mark_deleted.assert_called_once_with(10)
        self.assertEqual("google_deleted", result["sync_action"])
        self.assertEqual("deleted", result["sync_status"])
        self.assertEqual(2, result["status"])

    async def test_reject_after_approved_delete_failure_marks_sync_error(self):
        self.event_repo.get_event.return_value = dict(self.event_row, status=1, park=0)
        self.sync_repo.get_link.return_value = {"google_event_id": "g_evt_1"}
        self.google_client.delete_event.return_value = (
            500,
            {"error": {"code": 500, "message": "upstream down"}},
        )

        result = await self.service.approve_event(event_id=10, requested_status=2)

        self.event_repo.set_status.assert_called_once_with(10, 2)
        self.sync_repo.mark_error.assert_called_once_with(
            10,
            "delete_failed",
            "EMP_EVENT_SYNC_FAILED",
            "upstream down",
        )
        self.assertEqual("google_delete_failed", result["sync_action"])
        self.assertEqual("delete_failed", result["sync_status"])
        self.assertEqual(2, result["status"])

    async def test_update_approved_event_updates_google(self):
        self.event_repo.get_event.return_value = dict(self.event_row, status=1, park=0)
        self.event_repo.get_allowances.return_value = []
        self.event_repo.get_contact.return_value = None
        self.sync_repo.get_link.return_value = {"google_event_id": "g_evt_1"}
        self.google_client.update_event.return_value = (200, {"id": "g_evt_1"})

        with patch(
            "controllers.employee_events_v1.services.event_service.build_google_event_payload",
            return_value={"summary": "S"},
        ):
            result = await self.service.update_event(
                event_id=10,
                payload={"category": "Updated"},
                actor_user_id="42",
            )

        self.assertTrue(result["synced"])
        self.assertEqual("google_updated", result["sync_action"])
        self.google_client.update_event.assert_awaited_once_with(
            "primary", "g_evt_1", {"summary": "S"}, "google-token"
        )
        self.sync_repo.mark_active.assert_called_once_with(10, "g_evt_1", "primary")

    async def test_update_approved_missing_mapping_creates_google_event(self):
        self.event_repo.get_event.return_value = dict(self.event_row, status=1, park=0)
        self.event_repo.get_allowances.return_value = []
        self.event_repo.get_contact.return_value = None
        self.sync_repo.get_link.return_value = None
        self.google_client.create_event.return_value = (201, {"id": "g_new"})

        with patch(
            "controllers.employee_events_v1.services.event_service.build_google_event_payload",
            return_value={"summary": "S"},
        ):
            result = await self.service.update_event(
                event_id=10,
                payload={"category": "Updated"},
                actor_user_id="42",
            )

        self.assertTrue(result["synced"])
        self.assertEqual("google_created", result["sync_action"])
        self.sync_repo.mark_active.assert_called_once_with(10, "g_new", "primary")
        self.google_client.create_event.assert_awaited_once_with(
            "primary", {"summary": "S"}, "google-token"
        )

    async def test_park_sets_local_and_deletes_google_when_linked(self):
        self.sync_repo.get_link.return_value = {"google_event_id": "g_evt_1"}
        self.google_client.delete_event.return_value = (204, {})

        result = await self.service.park_event(event_id=10, park_value=1)

        self.event_repo.set_park.assert_called_once_with(10, 1)
        self.google_client.delete_event.assert_awaited_once_with(
            "primary", "g_evt_1", "google-token"
        )
        self.sync_repo.mark_deleted.assert_called_once_with(10)
        self.assertEqual("deleted", result["sync_status"])
        self.assertEqual("google_deleted", result["sync_action"])

    async def test_park_delete_failure_keeps_local_and_marks_error(self):
        self.sync_repo.get_link.return_value = {"google_event_id": "g_evt_1"}
        self.google_client.delete_event.return_value = (
            500,
            {"error": {"code": 500, "message": "upstream down"}},
        )

        result = await self.service.park_event(event_id=10, park_value=1)

        self.event_repo.set_park.assert_called_once_with(10, 1)
        self.assertEqual("delete_failed", result["sync_status"])
        self.assertEqual("google_delete_failed", result["sync_action"])
        self.sync_repo.mark_error.assert_called_once_with(
            10,
            "delete_failed",
            "EMP_EVENT_SYNC_FAILED",
            "upstream down",
        )


class TestEmployeeEventsPayloadBuilder(unittest.TestCase):
    def test_attendee_is_set_when_contact_email_exists(self):
        with patch(
            "controllers.employee_events_v1.services.google_payload_builder.get_settings",
            return_value=SimpleNamespace(EMP_EVENT_TIMEZONE="Asia/Kolkata"),
        ):
            payload = build_google_event_payload(
                event_row={
                    "category": "Meeting",
                    "type": "Interview",
                    "contact_id": 123,
                    "branch": "Mumbai",
                    "lease_type": "N/A",
                    "amount": 1000,
                    "deduction_amount": 0,
                    "allowance": 1,
                    "date": "2026-03-15",
                    "start_time": "10:00:00",
                    "end_time": "11:00:00",
                },
                allowances=[{"name": "Travel", "amount": 400}],
                contact={
                    "id": 123,
                    "fname": "Asha",
                    "mname": "",
                    "lname": "Verma",
                    "country_code": "+91",
                    "mobile": "9999999999",
                    "email": "asha@example.com",
                },
            )

        self.assertEqual("Mumbai | Interview | Meeting | Asha Verma", payload["summary"])
        self.assertEqual("Mumbai", payload["location"])
        self.assertIn("attendees", payload)
        self.assertEqual([{"email": "asha@example.com"}], payload["attendees"])
        self.assertEqual("", payload["description"])


    def test_get_employee_workshift_calendar_batch_per_day_schedule_configured(self):
        """workshift_id > 0: uses per-day schedule from workshift_day table."""
        self.event_repo.get_employee_workshifts.return_value = [
            {
                "employee_id": 20,
                "employee_name": "Per Day Employee",
                "workshift_id": 10,
                "workshift_in_time": "09:00:00",
                "workshift_out_time": "18:00:00",
                "week_off_code": "0,6",
            }
        ]
        # Mon(1)=10-19, Tue(2)=10-19, Wed(3)=10-19; Sun(0) and Sat(6) absent = week-off
        self.event_repo.get_workshift_day_rows.return_value = [
            {"workshift_id": 10, "day_code": 1, "start_time": "10:00:00", "end_time": "19:00:00"},
            {"workshift_id": 10, "day_code": 2, "start_time": "10:00:00", "end_time": "19:00:00"},
            {"workshift_id": 10, "day_code": 3, "start_time": "10:00:00", "end_time": "19:00:00"},
        ]

        # 2026-03-01 = Sun(0), 2026-03-02 = Mon(1), 2026-03-03 = Tue(2)
        result = self.service.get_employee_workshift_calendar_batch(
            employee_ids=[20],
            from_date="2026-03-01",
            to_date="2026-03-03",
        )

        employee = result["employees"][0]
        self.assertEqual("configured", employee["result_status"])
        self.assertEqual(3, employee["day_count"])
        # workshift object uses new per-day shape
        workshift = employee["workshift"]
        self.assertEqual(10, workshift["workshift_id"])
        self.assertIsNone(workshift["workshift_in_time"])
        self.assertIsNone(workshift["workshift_out_time"])
        self.assertIsNone(workshift["week_off_code"])
        self.assertEqual([0, 4, 5, 6], workshift["week_off_days"])  # days w/o rows
        self.assertEqual(3, len(workshift["day_schedule"]))
        self.assertEqual(1, workshift["day_schedule"][0]["day_code"])
        # Sun(0) has no row -> week-off
        self.assertTrue(employee["calendar_days"][0]["is_week_off"])
        self.assertIsNone(employee["calendar_days"][0]["shift_start"])
        # Mon(1) has a row -> working
        self.assertFalse(employee["calendar_days"][1]["is_week_off"])
        self.assertTrue(employee["calendar_days"][1]["shift_start"].startswith("2026-03-02T10:00:00"))
        self.event_repo.get_workshift_day_rows.assert_called_once_with([10])

    def test_get_employee_workshift_calendar_batch_per_day_overnight_shift(self):
        """workshift_id > 0 with overnight shift on a specific day."""
        self.event_repo.get_employee_workshifts.return_value = [
            {
                "employee_id": 21,
                "employee_name": "Night Worker",
                "workshift_id": 11,
                "workshift_in_time": None,
                "workshift_out_time": None,
                "week_off_code": None,
            }
        ]
        # Wed(3) = overnight 22:00 -> 06:00
        self.event_repo.get_workshift_day_rows.return_value = [
            {"workshift_id": 11, "day_code": 3, "start_time": "22:00:00", "end_time": "06:00:00"},
        ]

        # 2026-03-04 = Wed(3)
        result = self.service.get_employee_workshift_calendar_batch(
            employee_ids=[21],
            from_date="2026-03-04",
            to_date="2026-03-04",
        )

        day = result["employees"][0]["calendar_days"][0]
        self.assertFalse(day["is_week_off"])
        self.assertTrue(day["is_overnight"])
        self.assertTrue(day["shift_start"].startswith("2026-03-04T22:00:00"))
        self.assertTrue(day["shift_end"].startswith("2026-03-05T06:00:00"))

    def test_get_employee_workshift_calendar_batch_per_day_no_rows_unconfigured(self):
        """workshift_id > 0 but no rows in workshift_day -> unconfigured."""
        self.event_repo.get_employee_workshifts.return_value = [
            {
                "employee_id": 22,
                "employee_name": "Empty Schedule",
                "workshift_id": 12,
                "workshift_in_time": "09:00:00",
                "workshift_out_time": "18:00:00",
                "week_off_code": "0",
            }
        ]
        self.event_repo.get_workshift_day_rows.return_value = []

        result = self.service.get_employee_workshift_calendar_batch(
            employee_ids=[22],
            from_date="2026-03-02",
            to_date="2026-03-02",
        )

        employee = result["employees"][0]
        self.assertEqual("unconfigured", employee["result_status"])
        self.assertEqual(["no_days_configured"], employee["workshift"]["configuration_issues"])
        self.assertFalse(employee["workshift"]["is_configured"])
        self.assertEqual([], employee["calendar_days"])
        self.assertEqual([], employee["workshift"]["day_schedule"])

    def test_get_employee_workshift_calendar_batch_workshift_id_zero_uses_legacy_path(self):
        """workshift_id = 0 always uses the old single in/out time method."""
        self.event_repo.get_employee_workshifts.return_value = [
            {
                "employee_id": 23,
                "employee_name": "Legacy Worker",
                "workshift_id": 0,
                "workshift_in_time": "09:00:00",
                "workshift_out_time": "18:00:00",
                "week_off_code": "0",
            }
        ]

        result = self.service.get_employee_workshift_calendar_batch(
            employee_ids=[23],
            from_date="2026-03-02",
            to_date="2026-03-02",
        )

        # get_workshift_day_rows must NOT be called for workshift_id=0
        self.event_repo.get_workshift_day_rows.assert_not_called()
        employee = result["employees"][0]
        self.assertEqual("configured", employee["result_status"])
        self.assertEqual(0, employee["workshift"]["workshift_id"])
        self.assertEqual("09:00:00", employee["workshift"]["workshift_in_time"])
        self.assertEqual("18:00:00", employee["workshift"]["workshift_out_time"])
        self.assertFalse(employee["calendar_days"][0]["is_week_off"])  # Mon(1), not in week_off [0]

    def test_get_employee_workshift_calendar_batch_per_day_batch_fetch_for_distinct_ids(self):
        """get_workshift_day_rows is called once with all distinct workshift_ids > 0."""
        self.event_repo.get_employee_workshifts.return_value = [
            {
                "employee_id": 30,
                "employee_name": "Worker A",
                "workshift_id": 15,
                "workshift_in_time": None,
                "workshift_out_time": None,
                "week_off_code": None,
            },
            {
                "employee_id": 31,
                "employee_name": "Worker B",
                "workshift_id": 16,
                "workshift_in_time": None,
                "workshift_out_time": None,
                "week_off_code": None,
            },
        ]
        self.event_repo.get_workshift_day_rows.return_value = [
            {"workshift_id": 15, "day_code": 1, "start_time": "08:00:00", "end_time": "17:00:00"},
            {"workshift_id": 16, "day_code": 2, "start_time": "09:00:00", "end_time": "18:00:00"},
        ]

        self.service.get_employee_workshift_calendar_batch(
            employee_ids=[30, 31],
            from_date="2026-03-02",
            to_date="2026-03-03",
        )

        call_args = self.event_repo.get_workshift_day_rows.call_args[0][0]
        self.assertEqual(sorted(call_args), [15, 16])
        self.event_repo.get_workshift_day_rows.assert_called_once()

    def test_get_employee_workshift_calendar_batch_per_day_invalid_times_skipped(self):
        """workshift_day rows with invalid start/end times are skipped gracefully."""
        self.event_repo.get_employee_workshifts.return_value = [
            {
                "employee_id": 32,
                "employee_name": "Partial Schedule",
                "workshift_id": 17,
                "workshift_in_time": None,
                "workshift_out_time": None,
                "week_off_code": None,
            }
        ]
        self.event_repo.get_workshift_day_rows.return_value = [
            {"workshift_id": 17, "day_code": 1, "start_time": "not_a_time", "end_time": "17:00:00"},
            {"workshift_id": 17, "day_code": 2, "start_time": "09:00:00", "end_time": "18:00:00"},
        ]

        result = self.service.get_employee_workshift_calendar_batch(
            employee_ids=[32],
            from_date="2026-03-02",
            to_date="2026-03-03",
        )

        employee = result["employees"][0]
        # Only the valid row (day_code=2, Tue) is kept; invalid row (day_code=1, Mon) is skipped
        self.assertEqual("configured", employee["result_status"])
        day_codes_in_schedule = [e["day_code"] for e in employee["workshift"]["day_schedule"]]
        self.assertEqual([2], day_codes_in_schedule)
        # Mon(1) has no valid row -> week-off
        mon_day = next(d for d in employee["calendar_days"] if d["weekday"] == 1)
        self.assertTrue(mon_day["is_week_off"])
        # Tue(2) has valid row -> working
        tue_day = next(d for d in employee["calendar_days"] if d["weekday"] == 2)
        self.assertFalse(tue_day["is_week_off"])


if __name__ == "__main__":
    unittest.main()
