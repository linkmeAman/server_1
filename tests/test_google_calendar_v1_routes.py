"""Route tests for Google Calendar V1 endpoints."""

import unittest
from queue import Queue
from threading import Thread
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from controllers.google_calendar_v1.dependencies import GoogleCalendarError
import main
from core.settings import get_settings


def _middleware_headers():
    settings = get_settings()
    if settings.API_KEY_ENABLED and settings.API_KEYS:
        return {"X-API-Key": settings.API_KEYS[0]}
    return {}


def _testclient_requests_work() -> bool:
    probe_app = FastAPI()

    @probe_app.get("/__probe")
    def _probe():
        return {"ok": True}

    result = Queue(maxsize=1)

    def _run_probe():
        try:
            client = TestClient(probe_app)
            try:
                response = client.get("/__probe")
            finally:
                client.close()
            result.put(response.status_code == 200)
        except Exception:
            result.put(False)

    thread = Thread(target=_run_probe, daemon=True)
    thread.start()
    thread.join(timeout=2.0)
    if thread.is_alive() or result.empty():
        return False
    return bool(result.get())


class TestGoogleCalendarV1Routes(unittest.TestCase):
    def setUp(self):
        if not _testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")
        self.client = TestClient(main.app)
        self.base_create_payload = {
            "actor_name": "Admin",
            "actor_email": "admin@example.com",
            "event": {"summary": "Demo"},
        }

    def tearDown(self):
        self.client.close()

    def test_create_missing_app_token_returns_401(self):
        headers = {**_middleware_headers()}
        response = self.client.post(
            "/api/google-calendar/v1/events",
            headers=headers,
            json=self.base_create_payload,
        )
        self.assertEqual(401, response.status_code)
        body = response.json()
        self.assertEqual("GCAL_UNAUTHORIZED", body["error"])

    def test_create_invalid_app_token_returns_401(self):
        with patch(
            "controllers.google_calendar_v1.dependencies.validate_token",
            side_effect=ValueError("expired"),
        ):
            headers = {
                **_middleware_headers(),
                "Authorization": "Bearer invalid",
            }
            response = self.client.post(
                "/api/google-calendar/v1/events",
                headers=headers,
                json=self.base_create_payload,
            )

        self.assertEqual(401, response.status_code)
        body = response.json()
        self.assertEqual("GCAL_UNAUTHORIZED", body["error"])

    def test_create_token_error_returns_503(self):
        with patch(
            "controllers.google_calendar_v1.dependencies.validate_token",
            return_value={"sub": "123", "typ": "access"},
        ), patch(
            "controllers.google_calendar_v1.router.event_service.create_event",
            new=AsyncMock(
                side_effect=GoogleCalendarError(
                    code="GCAL_TOKEN_UNAVAILABLE",
                    message="Token missing",
                    status_code=503,
                )
            ),
        ):
            headers = {
                **_middleware_headers(),
                "Authorization": "Bearer app-token",
            }
            response = self.client.post(
                "/api/google-calendar/v1/events",
                headers=headers,
                json=self.base_create_payload,
            )

        self.assertEqual(503, response.status_code)
        body = response.json()
        self.assertEqual("GCAL_TOKEN_UNAVAILABLE", body["error"])

    def test_create_success_response_shape(self):
        with patch(
            "controllers.google_calendar_v1.dependencies.validate_token",
            return_value={"sub": "123", "typ": "access"},
        ), patch(
            "controllers.google_calendar_v1.router.event_service.create_event",
            new=AsyncMock(
                return_value={
                    "google_event": {"id": "evt_1", "summary": "Demo"},
                    "log_status": "create_logged",
                }
            ),
        ):
            headers = {
                **_middleware_headers(),
                "Authorization": "Bearer app-token",
            }
            response = self.client.post(
                "/api/google-calendar/v1/events",
                headers=headers,
                json=self.base_create_payload,
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual("Calendar event created successfully", body["message"])
        self.assertEqual("evt_1", body["data"]["google_event"]["id"])

    def test_delete_next_instance_forwards_mode(self):
        with patch(
            "controllers.google_calendar_v1.dependencies.validate_token",
            return_value={"sub": "123", "typ": "access"},
        ), patch(
            "controllers.google_calendar_v1.router.event_service.delete_event",
            new=AsyncMock(
                return_value={
                    "delete_mode": "next_instance",
                    "deleted_event_id": "inst_1",
                    "already_deleted": False,
                    "google_status": 204,
                }
            ),
        ) as mocked_delete:
            headers = {
                **_middleware_headers(),
                "Authorization": "Bearer app-token",
            }
            response = self.client.delete(
                "/api/google-calendar/v1/events/evt_1?delete_mode=next_instance",
                headers=headers,
            )

        self.assertEqual(200, response.status_code)
        mocked_delete.assert_awaited_once()
        called = mocked_delete.await_args.kwargs
        self.assertEqual("next_instance", called["delete_mode"])


if __name__ == "__main__":
    unittest.main()
