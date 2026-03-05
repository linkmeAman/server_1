"""Service-level tests for Employee Events V1 workflows."""

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


if __name__ == "__main__":
    unittest.main()
