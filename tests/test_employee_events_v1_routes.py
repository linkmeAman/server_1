"""Route tests for Employee Events V1 endpoints."""

import unittest
from queue import Queue
from threading import Thread
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from controllers.employee_events_v1.dependencies import EmployeeEventsError
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


class TestEmployeeEventsV1Routes(unittest.TestCase):
    def setUp(self):
        if not _testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()

    def test_create_missing_app_token_returns_401(self):
        response = self.client.post(
            "/api/employee-events/v1/events",
            headers={**_middleware_headers()},
            json={
                "category": "Meeting",
                "contact_id": 1,
                "branch": "Mumbai",
                "type": "Interview",
                "lease_type": "N/A",
                "amount": 10,
                "deduction_amount": 0,
                "date": "2026-03-15",
                "start_time": "10:00:00",
                "end_time": "11:00:00",
                "allowance": 0,
                "allowance_items": [],
            },
        )
        self.assertEqual(401, response.status_code)
        body = response.json()
        self.assertEqual("EMP_EVENT_UNAUTHORIZED", body["error"])

    def test_realtime_data_missing_app_token_returns_401(self):
        response = self.client.get(
            "/api/employee-events/v1/employees/realtime-data",
            headers={**_middleware_headers()},
        )
        self.assertEqual(401, response.status_code)
        self.assertEqual("EMP_EVENT_UNAUTHORIZED", response.json()["error"])

    def test_list_missing_app_token_returns_401(self):
        response = self.client.get(
            "/api/employee-events/v1/events",
            headers={**_middleware_headers()},
        )
        self.assertEqual(401, response.status_code)
        self.assertEqual("EMP_EVENT_UNAUTHORIZED", response.json()["error"])

    def test_invalid_app_token_returns_401(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            side_effect=ValueError("expired"),
        ):
            response = self.client.post(
                "/api/employee-events/v1/events/check-conflict",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer invalid",
                },
                json={
                    "date": "2026-03-15",
                    "start_time": "10:00:00",
                    "end_time": "11:00:00",
                    "contact_id": 123,
                },
            )
        self.assertEqual(401, response.status_code)
        self.assertEqual("EMP_EVENT_UNAUTHORIZED", response.json()["error"])

    def test_conflict_check_success(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.router.employee_events_service.check_conflict",
            new=MagicMock(return_value={"conflict": False, "conflict_event_ids": []}),
        ) as mocked_check:
            response = self.client.post(
                "/api/employee-events/v1/events/check-conflict",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
                json={
                    "date": "2026-03-15",
                    "start_time": "10:00:00",
                    "end_time": "11:00:00",
                    "contact_id": 123,
                },
            )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["success"])
        mocked_check.assert_called_once()

    def test_create_forwards_description(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.router.employee_events_service.create_event",
            new=MagicMock(return_value={"event_id": 55, "sync_status": "pending_approval"}),
        ) as mocked_create:
            response = self.client.post(
                "/api/employee-events/v1/events",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
                json={
                    "category": "Meeting",
                    "contact_id": 1,
                    "branch": "Mumbai",
                    "description": "Frontend description text",
                    "type": "Interview",
                    "lease_type": "N/A",
                    "amount": 10,
                    "deduction_amount": 0,
                    "date": "2026-03-15",
                    "start_time": "10:00:00",
                    "end_time": "11:00:00",
                    "allowance": 0,
                    "allowance_items": [],
                },
            )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["success"])
        mocked_create.assert_called_once()
        payload = mocked_create.call_args.kwargs["payload"]
        self.assertEqual("Frontend description text", payload["description"])

    def test_list_events_success(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.router.employee_events_service.list_events",
            new=MagicMock(
                return_value={
                    "events": [
                        {
                            "id": 10,
                            "category": "Workshop",
                            "date": "2026-03-15",
                            "start_time": "10:00:00",
                            "end_time": "11:00:00",
                            "sync": {"sync_status": "pending_approval"},
                            "allowance_items": [],
                        }
                    ],
                    "count": 1,
                }
            ),
        ) as mocked_list:
            response = self.client.get(
                "/api/employee-events/v1/events?from_date=2026-03-01&to_date=2026-03-31",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(1, body["data"]["count"])
        mocked_list.assert_called_once()

    def test_realtime_data_success(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.router.employee_events_service.get_realtime_employee_data",
            new=MagicMock(
                return_value={
                    "employees": [
                        {
                            "id": 1,
                            "contact_id": 10,
                            "fullname": "Sneha Trainer",
                            "position_id": 2,
                            "position": "Trainer",
                            "bid": 5,
                        }
                    ],
                    "branches": [{"id": 1, "branch": "Bandra", "type": "HQ"}],
                    "employee_count": 1,
                    "branch_count": 1,
                }
            ),
        ) as mocked_realtime:
            response = self.client.get(
                "/api/employee-events/v1/employees/realtime-data",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(1, body["data"]["employee_count"])
        self.assertEqual(1, body["data"]["branch_count"])
        mocked_realtime.assert_called_once()

    def test_approve_forwards_status(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.router.employee_events_service.approve_event",
            new=AsyncMock(
                return_value={
                    "event_id": 10,
                    "status": 1,
                    "sync_status": "active",
                    "synced": True,
                    "sync_action": "google_created",
                }
            ),
        ) as mocked_approve:
            response = self.client.post(
                "/api/employee-events/v1/events/10/approve",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
                json={"status": 1},
            )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["success"])
        mocked_approve.assert_awaited_once()
        self.assertEqual(1, mocked_approve.await_args.kwargs["requested_status"])

    def test_park_sync_error_returns_success_with_status(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.router.employee_events_service.park_event",
            new=AsyncMock(
                return_value={
                    "event_id": 10,
                    "park_value": 1,
                    "sync_status": "delete_failed",
                    "synced": False,
                    "sync_action": "google_delete_failed",
                    "error_code": "EMP_EVENT_SYNC_FAILED",
                }
            ),
        ):
            response = self.client.patch(
                "/api/employee-events/v1/events/10/park",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
                json={"park_value": 1},
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual("delete_failed", body["data"]["sync_status"])

    def test_domain_error_passthrough(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.router.employee_events_service.approve_event",
            new=AsyncMock(
                side_effect=EmployeeEventsError(
                    code="EMP_EVENT_INVALID_STATE",
                    message="Cannot approve a parked event",
                    status_code=409,
                )
            ),
        ):
            response = self.client.post(
                "/api/employee-events/v1/events/10/approve",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
                json={"status": 1},
            )

        self.assertEqual(409, response.status_code)
        body = response.json()
        self.assertEqual("EMP_EVENT_INVALID_STATE", body["error"])


if __name__ == "__main__":
    unittest.main()
