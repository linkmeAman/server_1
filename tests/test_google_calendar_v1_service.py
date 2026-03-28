"""Service and normalization tests for Google Calendar V1."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from controllers.google_calendar_v1.dependencies import GoogleCalendarError
from controllers.google_calendar_v1.services.datetime_utils import normalize_google_event_for_log
from controllers.google_calendar_v1.services.event_log_repository import LogPersistenceError
from controllers.google_calendar_v1.services.event_service import GoogleCalendarEventService
from controllers.google_calendar_v1.services.token_manager import GoogleCalendarTokenManager


class TestGoogleCalendarV1Service(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = type("Client", (), {})()
        self.client.create_event = AsyncMock()
        self.client.update_event = AsyncMock()
        self.client.delete_event = AsyncMock()
        self.client.list_instances = AsyncMock()

        self.repo = MagicMock()
        self.token_manager = MagicMock()
        self.token_manager.get_valid_access_token = AsyncMock(return_value="google-token")

        self.settings_patcher = patch(
            "controllers.google_calendar_v1.services.event_service.get_settings",
            return_value=SimpleNamespace(
                GOOGLE_CALENDAR_ID="primary",
                GOOGLE_CALENDAR_COMPARE_TIMEZONE="Asia/Kolkata",
            ),
        )
        self.settings_patcher.start()

        self.service = GoogleCalendarEventService(
            client=self.client,
            log_repository=self.repo,
            token_manager=self.token_manager,
        )

    def tearDown(self):
        self.settings_patcher.stop()

    async def test_create_success_logs_and_returns_payload(self):
        self.client.create_event.return_value = (
            201,
            {
                "id": "evt_1",
                "summary": "Demo",
                "start": {"dateTime": "2026-03-10T10:00:00+05:30", "timeZone": "Asia/Kolkata"},
                "end": {"dateTime": "2026-03-10T11:00:00+05:30", "timeZone": "Asia/Kolkata"},
                "attendees": [{"email": "a@example.com"}],
                "guestsCanModify": True,
            },
        )

        result = await self.service.create_event(
            event={"summary": "Demo"},
            actor_name="Admin",
            actor_email="admin@example.com",
        )

        self.assertEqual("evt_1", result["google_event"]["id"])
        self.token_manager.get_valid_access_token.assert_awaited_once()
        self.client.create_event.assert_awaited_once_with("primary", {"summary": "Demo"}, "google-token")
        self.repo.insert_create_success_log.assert_called_once()
        normalized_event = self.repo.insert_create_success_log.call_args.args[2]
        self.assertEqual("evt_1", normalized_event["event_id"])
        self.assertEqual("Asia/Kolkata", normalized_event["event_timezone"])
        self.assertTrue(normalized_event["event_start"].endswith("+00:00"))

    async def test_create_upstream_error_logs_error_and_raises(self):
        self.client.create_event.return_value = (
            400,
            {"error": {"code": 400, "message": "Invalid payload"}},
        )

        with self.assertRaises(GoogleCalendarError) as ctx:
            await self.service.create_event(
                event={},
                actor_name="Admin",
                actor_email="admin@example.com",
            )

        self.assertEqual("GCAL_UPSTREAM_ERROR", ctx.exception.code)
        self.repo.insert_create_error_log.assert_called_once_with(
            actor_name="Admin",
            actor_email="admin@example.com",
            response_code=400,
            response_message="Invalid payload",
        )

    async def test_update_success_logs(self):
        self.client.update_event.return_value = (
            200,
            {
                "id": "evt_1",
                "summary": "Updated",
                "start": {"dateTime": "2026-03-10T12:00:00+05:30"},
                "end": {"dateTime": "2026-03-10T13:00:00+05:30"},
            },
        )

        result = await self.service.update_event(
            event_id="evt_1",
            event={"summary": "Updated"},
            actor_name="Admin",
            log_row_id=101,
        )

        self.assertEqual("evt_1", result["google_event"]["id"])
        self.client.update_event.assert_awaited_once_with(
            "primary",
            "evt_1",
            {"summary": "Updated"},
            "google-token",
        )
        self.repo.update_event_log.assert_called_once()
        kwargs = self.repo.update_event_log.call_args.kwargs
        self.assertEqual(101, kwargs["log_row_id"])

    async def test_update_non_200_raises_without_log_update(self):
        self.client.update_event.return_value = (
            404,
            {"error": {"code": 404, "message": "Not found"}},
        )

        with self.assertRaises(GoogleCalendarError) as ctx:
            await self.service.update_event(
                event_id="evt_404",
                event={"summary": "Updated"},
                actor_name="Admin",
            )

        self.assertEqual("GCAL_UPSTREAM_ERROR", ctx.exception.code)
        self.repo.update_event_log.assert_not_called()

    async def test_delete_full_204_marks_park(self):
        self.client.delete_event.return_value = (204, {})

        result = await self.service.delete_event(
            event_id="evt_1",
            delete_mode="full",
        )

        self.assertFalse(result["already_deleted"])
        self.assertEqual("evt_1", result["deleted_event_id"])
        self.repo.mark_event_deleted.assert_called_once_with("evt_1")

    async def test_delete_full_410_treated_as_success(self):
        self.client.delete_event.return_value = (410, {"error": {"code": 410, "message": "Gone"}})

        result = await self.service.delete_event(
            event_id="evt_1",
            delete_mode="full",
        )

        self.assertTrue(result["already_deleted"])
        self.repo.mark_event_deleted.assert_called_once_with("evt_1")

    async def test_delete_next_instance_picks_upcoming(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        future_1 = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat().replace("+00:00", "Z")
        future_2 = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace("+00:00", "Z")

        self.client.list_instances.return_value = (
            200,
            {
                "items": [
                    {"id": "inst_past", "start": {"dateTime": past}},
                    {"id": "inst_future_2", "start": {"dateTime": future_2}},
                    {"id": "inst_future_1", "start": {"dateTime": future_1}},
                ]
            },
        )
        self.client.delete_event.return_value = (204, {})

        result = await self.service.delete_event(
            event_id="series_1",
            delete_mode="next_instance",
        )

        self.assertEqual("inst_future_1", result["deleted_event_id"])
        self.client.delete_event.assert_awaited_once_with("primary", "inst_future_1", "google-token")
        self.repo.mark_event_deleted.assert_called_once_with("series_1")

    async def test_delete_next_instance_not_found(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        self.client.list_instances.return_value = (
            200,
            {"items": [{"id": "inst_old", "start": {"dateTime": past}}]},
        )

        with self.assertRaises(GoogleCalendarError) as ctx:
            await self.service.delete_event(
                event_id="series_1",
                delete_mode="next_instance",
            )

        self.assertEqual("GCAL_INSTANCE_NOT_FOUND", ctx.exception.code)
        self.client.delete_event.assert_not_awaited()

    async def test_log_persistence_error_mapped(self):
        self.client.create_event.return_value = (
            200,
            {
                "id": "evt_1",
                "start": {"dateTime": "2026-03-10T10:00:00+05:30"},
                "end": {"dateTime": "2026-03-10T11:00:00+05:30"},
            },
        )
        self.repo.insert_create_success_log.side_effect = LogPersistenceError("db down")

        with self.assertRaises(GoogleCalendarError) as ctx:
            await self.service.create_event(
                event={"summary": "Demo"},
                actor_name="Admin",
                actor_email="admin@example.com",
            )

        self.assertEqual("GCAL_LOG_PERSISTENCE_FAILED", ctx.exception.code)

    async def test_missing_calendar_id_configuration_raises(self):
        with patch(
            "controllers.google_calendar_v1.services.event_service.get_settings",
            return_value=SimpleNamespace(
                GOOGLE_CALENDAR_ID="",
                GOOGLE_CALENDAR_COMPARE_TIMEZONE="Asia/Kolkata",
            ),
        ):
            with self.assertRaises(GoogleCalendarError) as ctx:
                await self.service.create_event(
                    event={"summary": "Demo"},
                    actor_name="Admin",
                    actor_email="admin@example.com",
                )
        self.assertEqual("GCAL_CONFIG_ERROR", ctx.exception.code)


class TestGoogleCalendarDatetimeUtils(unittest.TestCase):
    def test_normalize_google_event_for_log_timezone_fallback_and_raw_preservation(self):
        normalized = normalize_google_event_for_log(
            {
                "id": "evt_1",
                "summary": "Demo",
                "start": {"dateTime": "not-a-datetime"},
                "end": {"date": "2026-03-10"},
                "attendees": [{"email": "a@example.com"}],
            },
            fallback_timezone="Asia/Kolkata",
        )

        self.assertEqual("evt_1", normalized["event_id"])
        self.assertEqual("Asia/Kolkata", normalized["event_timezone"])
        self.assertEqual("not-a-datetime", normalized["event_start"])
        self.assertTrue(normalized["event_end"].endswith("+00:00"))
        self.assertIn("a@example.com", normalized["attendees"])


class TestGoogleCalendarTokenManager(unittest.IsolatedAsyncioTestCase):
    async def test_valid_token_returns_without_refresh(self):
        manager = GoogleCalendarTokenManager()
        manager._fetch_token_row = MagicMock(
            return_value={
                "access_token": "cached-token",
                "expires_in": 3600,
                "created": datetime.now(timezone.utc),
            }
        )
        manager._refresh_access_token = AsyncMock()
        manager._persist_refreshed_token = MagicMock()

        token = await manager.get_valid_access_token()

        self.assertEqual("cached-token", token)
        manager._refresh_access_token.assert_not_awaited()
        manager._persist_refreshed_token.assert_not_called()

    async def test_expired_token_refreshes_and_persists(self):
        manager = GoogleCalendarTokenManager()
        manager._fetch_token_row = MagicMock(
            return_value={
                "access_token": "old-token",
                "expires_in": 300,
                "created": datetime.now(timezone.utc) - timedelta(hours=2),
                "refresh_token": "r",
                "client_id": "c",
                "client_secret": "s",
                "token_type": "Bearer",
            }
        )
        manager._refresh_access_token = AsyncMock(
            return_value={
                "access_token": "new-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        )
        manager._persist_refreshed_token = MagicMock()

        token = await manager.get_valid_access_token()

        self.assertEqual("new-token", token)
        manager._refresh_access_token.assert_awaited_once()
        manager._persist_refreshed_token.assert_called_once()


if __name__ == "__main__":
    unittest.main()
