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

    def test_active_venues_missing_app_token_returns_401(self):
        response = self.client.get(
            "/api/employee-events/v1/venues",
            headers={**_middleware_headers()},
        )
        self.assertEqual(401, response.status_code)
        self.assertEqual("EMP_EVENT_UNAUTHORIZED", response.json()["error"])

    def test_trainer_calendar_events_missing_app_token_returns_401(self):
        response = self.client.get(
            "/api/employee-events/v1/calendar/events?contact_id=10",
            headers={**_middleware_headers()},
        )
        self.assertEqual(401, response.status_code)
        self.assertEqual("EMP_EVENT_UNAUTHORIZED", response.json()["error"])

    def test_workshift_calendar_query_missing_app_token_returns_401(self):
        response = self.client.post(
            "/api/employee-events/v1/employees/workshift-calendar/query",
            headers={**_middleware_headers()},
            json={
                "employee_ids": [1, 2],
                "from_date": "2026-03-01",
                "to_date": "2026-03-31",
            },
        )
        self.assertEqual(401, response.status_code)
        self.assertEqual("EMP_EVENT_UNAUTHORIZED", response.json()["error"])

    def test_leave_calendar_query_missing_app_token_returns_401(self):
        response = self.client.post(
            "/api/employee-events/v1/employees/leave-calendar/query",
            headers={**_middleware_headers()},
            json={
                "employee_ids": [1, 2],
                "from_date": "2026-03-01",
                "to_date": "2026-03-31",
            },
        )
        self.assertEqual(401, response.status_code)
        self.assertEqual("EMP_EVENT_UNAUTHORIZED", response.json()["error"])

    def test_batch_query_missing_app_token_returns_401(self):
        response = self.client.post(
            "/api/employee-events/v1/batches/query",
            headers={**_middleware_headers()},
            json={"venue_ids": [10]},
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

    def test_workshift_calendar_query_invalid_json_returns_400(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ):
            response = self.client.post(
                "/api/employee-events/v1/employees/workshift-calendar/query",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                    "Content-Type": "application/json",
                },
                data="{not-json",
            )

        self.assertEqual(400, response.status_code)
        body = response.json()
        self.assertEqual("EMP_EVENT_INVALID_WORKSHIFT_QUERY", body["error"])

    def test_workshift_calendar_query_invalid_payload_returns_400(self):
        cases = [
            [],
            {"employee_ids": [], "from_date": "2026-03-01", "to_date": "2026-03-31"},
            {"employee_ids": ["x"], "from_date": "2026-03-01", "to_date": "2026-03-31"},
            {"employee_ids": [1], "from_date": "03-01-2026", "to_date": "2026-03-31"},
            {"employee_ids": [1], "from_date": "2026-03-31", "to_date": "2026-03-01"},
            {"employee_ids": [1], "from_date": "2026-01-01", "to_date": "2026-03-31"},
            {
                "employee_ids": list(range(1, 27)),
                "from_date": "2026-03-01",
                "to_date": "2026-03-31",
            },
        ]

        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ):
            for payload in cases:
                with self.subTest(payload=payload):
                    response = self.client.post(
                        "/api/employee-events/v1/employees/workshift-calendar/query",
                        headers={
                            **_middleware_headers(),
                            "Authorization": "Bearer ok",
                        },
                        json=payload,
                    )
                    self.assertEqual(400, response.status_code)
                    self.assertEqual(
                        "EMP_EVENT_INVALID_WORKSHIFT_QUERY",
                        response.json()["error"],
                    )

    def test_leave_calendar_query_invalid_json_returns_400(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ):
            response = self.client.post(
                "/api/employee-events/v1/employees/leave-calendar/query",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                    "Content-Type": "application/json",
                },
                data="{not-json",
            )

        self.assertEqual(400, response.status_code)
        body = response.json()
        self.assertEqual("EMP_EVENT_INVALID_LEAVE_QUERY", body["error"])

    def test_batch_query_invalid_json_returns_400(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ):
            response = self.client.post(
                "/api/employee-events/v1/batches/query",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                    "Content-Type": "application/json",
                },
                data="{not-json",
            )

        self.assertEqual(400, response.status_code)
        self.assertEqual("EMP_EVENT_INVALID_BATCH_QUERY", response.json()["error"])

    def test_leave_calendar_query_invalid_payload_returns_400(self):
        cases = [
            [],
            {"employee_ids": [1], "from_date": "2026-03-01"},
            {
                "employee_ids": [1],
                "from_date": "2026-03-01",
                "to_date": "2026-03-31",
                "statuses": "0",
            },
        ]

        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ):
            for payload in cases:
                with self.subTest(payload=payload):
                    response = self.client.post(
                        "/api/employee-events/v1/employees/leave-calendar/query",
                        headers={
                            **_middleware_headers(),
                            "Authorization": "Bearer ok",
                        },
                        json=payload,
                    )
                    self.assertEqual(400, response.status_code)
                    self.assertEqual(
                        "EMP_EVENT_INVALID_LEAVE_QUERY",
                        response.json()["error"],
                    )

    def test_batch_query_invalid_payload_returns_400(self):
        cases = [
            [],
            {},
            {"venue_ids": []},
            {"venue_ids": [0]},
            {"venue_ids": [-1]},
            {"venue_ids": [True]},
            {"venue_ids": ["abc"]},
            {"venue_ids": list(range(1, 27))},
        ]

        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ):
            for payload in cases:
                with self.subTest(payload=payload):
                    response = self.client.post(
                        "/api/employee-events/v1/batches/query",
                        headers={
                            **_middleware_headers(),
                            "Authorization": "Bearer ok",
                        },
                        json=payload,
                    )
                    self.assertEqual(400, response.status_code)
                    self.assertEqual(
                        "EMP_EVENT_INVALID_BATCH_QUERY",
                        response.json()["error"],
                    )

    def test_leave_calendar_query_semantic_failures_return_400(self):
        cases = [
            {"employee_ids": [], "from_date": "2026-03-01", "to_date": "2026-03-31"},
            {
                "employee_ids": list(range(1, 27)),
                "from_date": "2026-03-01",
                "to_date": "2026-03-31",
            },
            {"employee_ids": [1], "from_date": "03-01-2026", "to_date": "2026-03-31"},
            {"employee_ids": [1], "from_date": "2026-03-31", "to_date": "2026-03-01"},
            {"employee_ids": [1], "from_date": "2026-01-01", "to_date": "2026-03-31"},
            {
                "employee_ids": [1],
                "from_date": "2026-03-01",
                "to_date": "2026-03-31",
                "statuses": [-1],
            },
            {
                "employee_ids": [1],
                "from_date": "2026-03-01",
                "to_date": "2026-03-31",
                "request_types": [0],
            },
            {
                "employee_ids": [1],
                "from_date": "2026-03-01",
                "to_date": "2026-03-31",
                "department_ids": [0],
            },
        ]

        def _semantic_error(**kwargs):
            raise EmployeeEventsError(
                code="EMP_EVENT_INVALID_WORKSHIFT_QUERY",
                message="invalid query",
                status_code=400,
                data={"kwargs": kwargs},
            )

        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.get_employee_leave_calendar_batch",
            new=MagicMock(side_effect=_semantic_error),
        ):
            for payload in cases:
                with self.subTest(payload=payload):
                    response = self.client.post(
                        "/api/employee-events/v1/employees/leave-calendar/query",
                        headers={
                            **_middleware_headers(),
                            "Authorization": "Bearer ok",
                        },
                        json=payload,
                    )
                    self.assertEqual(400, response.status_code)
                    self.assertEqual("EMP_EVENT_INVALID_LEAVE_QUERY", response.json()["error"])

    def test_invalid_app_token_returns_401(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            side_effect=ValueError("expired"),
        ), patch(
            "controllers.employee_events_v1.dependencies.verify_v2_access_token",
            side_effect=ValueError("invalid_v2"),
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
        body = response.json()
        self.assertEqual("EMP_EVENT_UNAUTHORIZED", body["error"])
        self.assertIn("request_id", body["data"])
        self.assertIn("details", body["data"])
        self.assertEqual("expired", body["data"]["details"].get("legacy_reason"))
        self.assertEqual("invalid_v2", body["data"]["details"].get("auth_v2_reason"))

    def test_v2_access_token_is_accepted_when_legacy_validation_fails(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            side_effect=ValueError("legacy_rejected"),
        ), patch(
            "controllers.employee_events_v1.dependencies.verify_v2_access_token",
            return_value={
                "sub": "200",
                "user_id": 200,
                "contact_id": 10,
                "employee_id": 99,
                "roles": ["ops"],
                "mobile": "9990001111",
                "jti": "j",
                "iat": 1,
                "exp": 9999999999,
                "iss": "issuer",
                "aud": "aud",
                "auth_ver": 2,
                "typ": "access",
            },
        ), patch(
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.list_events",
            new=MagicMock(return_value={"events": [], "count": 0}),
        ) as mocked_list:
            response = self.client.get(
                "/api/employee-events/v1/events?from_date=2026-03-01&to_date=2026-03-31",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer v2-token",
                },
            )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["success"])
        mocked_list.assert_called_once()

    def test_conflict_check_success(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.check_conflict",
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
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.create_event",
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
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.list_events",
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
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.get_realtime_employee_data",
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

    def test_active_venues_success(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.get_active_venues",
            new=MagicMock(
                return_value={
                    "venues": [
                        {"id": 10, "venue": "Andheri Center", "display_name": "Andheri Center"}
                    ],
                    "total_count": 1,
                }
            ),
        ) as mocked_venues:
            response = self.client.get(
                "/api/employee-events/v1/venues",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(1, body["data"]["total_count"])
        mocked_venues.assert_called_once()

    def test_batch_query_success(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.get_active_batches_by_venue",
            new=MagicMock(
                return_value={
                    "venue_ids": [10, 20],
                    "total_count": 1,
                    "batches": [
                        {
                            "id": 123,
                            "batch": "Offline B87",
                            "display_name": "Offline B87",
                            "venue_id": 10,
                            "venue": "Andheri Center",
                            "parent_id": 0,
                            "branch": "Mumbai",
                            "bid": 7,
                        }
                    ],
                }
            ),
        ) as mocked_batches:
            response = self.client.post(
                "/api/employee-events/v1/batches/query",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
                json={"venue_ids": [10, 20]},
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(1, body["data"]["total_count"])
        mocked_batches.assert_called_once_with(venue_ids=[10, 20])

    def test_trainer_calendar_events_success(self):
        response_data = {
            "events": [
                {
                    "source": "employee_event",
                    "source_event_id": "employee_55",
                    "title": "Meeting",
                    "start": "2026-03-10 10:00:00",
                    "end": "2026-03-10 11:00:00",
                    "is_read_only": False,
                    "raw": {"id": 55, "category": "Meeting"},
                },
                {
                    "source": "trainer_batch",
                    "source_event_id": "trainer_123",
                    "title": "Offline B87",
                    "start": "2026-03-10 12:00:00",
                    "end": "2026-03-10 13:30:00",
                    "is_read_only": True,
                    "raw": {"id": 123, "batch_name": "Offline B87"},
                },
            ],
            "total_count": 2,
        }

        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.get_trainer_calendar_events",
            new=MagicMock(return_value=response_data),
        ) as mocked_list:
            response = self.client.get(
                "/api/employee-events/v1/calendar/events"
                "?contact_id=10&from_date=2026-03-01&to_date=2026-03-31",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual("Calendar events fetched successfully", body["message"])
        self.assertEqual(2, body["data"]["total_count"])
        self.assertEqual("employee_event", body["data"]["events"][0]["source"])
        self.assertEqual("trainer_batch", body["data"]["events"][1]["source"])
        mocked_list.assert_called_once_with(
            contact_id=10,
            from_date="2026-03-01",
            to_date="2026-03-31",
        )

    def test_trainer_calendar_events_invalid_date_returns_400(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.get_trainer_calendar_events",
            new=MagicMock(
                side_effect=EmployeeEventsError(
                    code="EMP_EVENT_INVALID_CALENDAR_QUERY",
                    message="from_date must be in YYYY-MM-DD format",
                    status_code=400,
                )
            ),
        ):
            response = self.client.get(
                "/api/employee-events/v1/calendar/events?contact_id=10&from_date=03-01-2026",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
            )

        self.assertEqual(400, response.status_code)
        self.assertEqual("EMP_EVENT_INVALID_CALENDAR_QUERY", response.json()["error"])

    def test_trainer_calendar_events_invalid_range_returns_400(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.get_trainer_calendar_events",
            new=MagicMock(
                side_effect=EmployeeEventsError(
                    code="EMP_EVENT_INVALID_CALENDAR_QUERY",
                    message="from_date must be less than or equal to to_date",
                    status_code=400,
                )
            ),
        ):
            response = self.client.get(
                "/api/employee-events/v1/calendar/events"
                "?contact_id=10&from_date=2026-03-31&to_date=2026-03-01",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
            )

        self.assertEqual(400, response.status_code)
        self.assertEqual("EMP_EVENT_INVALID_CALENDAR_QUERY", response.json()["error"])

    def test_trainer_calendar_events_service_error_passthrough(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.get_trainer_calendar_events",
            new=MagicMock(
                side_effect=EmployeeEventsError(
                    code="EMP_EVENT_CALENDAR_QUERY_FAILED",
                    message="Could not fetch trainer calendar events",
                    status_code=500,
                )
            ),
        ):
            response = self.client.get(
                "/api/employee-events/v1/calendar/events?contact_id=10",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
            )

        self.assertEqual(500, response.status_code)
        self.assertEqual("EMP_EVENT_CALENDAR_QUERY_FAILED", response.json()["error"])

    def test_workshift_calendar_query_success(self):
        response_data = {
            "timezone": "Asia/Kolkata",
            "from_date": "2026-03-01",
            "to_date": "2026-03-31",
            "range_day_count": 31,
            "employee_count": 2,
            "matched_count": 1,
            "employees": [
                {
                    "employee_id": 1,
                    "employee_name": "Sneha Trainer",
                    "result_status": "configured",
                    "warnings": [],
                    "workshift": {
                        "workshift_id": 9,
                        "workshift_in_time": "10:00:00",
                        "workshift_out_time": "19:00:00",
                        "week_off_code": "0,6",
                        "week_off_days": [0, 6],
                        "is_configured": True,
                        "configuration_issues": [],
                    },
                    "calendar_days": [],
                    "day_count": 31,
                },
                {
                    "employee_id": 2,
                    "employee_name": None,
                    "result_status": "not_found",
                    "warnings": [],
                    "workshift": None,
                    "calendar_days": [],
                    "day_count": 0,
                },
            ],
        }

        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.get_employee_workshift_calendar_batch",
            new=MagicMock(return_value=response_data),
        ) as mocked_query:
            response = self.client.post(
                "/api/employee-events/v1/employees/workshift-calendar/query",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
                json={
                    "employee_ids": [1, 2],
                    "from_date": "2026-03-01",
                    "to_date": "2026-03-31",
                },
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(2, body["data"]["employee_count"])
        self.assertEqual("configured", body["data"]["employees"][0]["result_status"])
        mocked_query.assert_called_once_with(
            employee_ids=[1, 2],
            from_date="2026-03-01",
            to_date="2026-03-31",
        )

    def test_workshift_calendar_query_service_error_passthrough(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.get_employee_workshift_calendar_batch",
            new=MagicMock(
                side_effect=EmployeeEventsError(
                    code="EMP_EVENT_SERVICE_MISCONFIGURED",
                    message="EMP_EVENT_TIMEZONE is invalid",
                    status_code=500,
                )
            ),
        ):
            response = self.client.post(
                "/api/employee-events/v1/employees/workshift-calendar/query",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
                json={
                    "employee_ids": [1],
                    "from_date": "2026-03-01",
                    "to_date": "2026-03-31",
                },
            )

        self.assertEqual(500, response.status_code)
        self.assertEqual("EMP_EVENT_SERVICE_MISCONFIGURED", response.json()["error"])

    def test_leave_calendar_query_success(self):
        response_data = {
            "timezone": "Asia/Kolkata",
            "from_date": "2026-03-01",
            "to_date": "2026-03-03",
            "range_day_count": 3,
            "employee_count": 2,
            "matched_count": 1,
            "filters_applied": {
                "statuses": [0, 1],
                "request_types": [1, 3],
                "department_ids": [9],
            },
            "employees": [
                {
                    "employee_id": 1,
                    "employee_name": "Sneha Trainer",
                    "result_status": "has_events",
                    "warnings": [],
                    "leave_events": [
                        {
                            "leave_request_id": 88,
                            "employee_id": 1,
                            "employee_name": "Sneha Trainer",
                            "department_id": 9,
                            "start": "2026-03-01T09:00:00+05:30",
                            "end": "2026-03-01T17:00:00+05:30",
                            "status": 1,
                            "status_label": "Approved",
                            "request_type": 1,
                            "request_type_name": "Leave",
                            "title": "Leave",
                            "color": "#EF4865",
                            "allDay": False,
                            "module_id": 80,
                        }
                    ],
                    "leave_event_count": 1,
                },
                {
                    "employee_id": 2,
                    "employee_name": None,
                    "result_status": "not_found",
                    "warnings": [],
                    "leave_events": [],
                    "leave_event_count": 0,
                },
            ],
        }
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.get_employee_leave_calendar_batch",
            new=MagicMock(return_value=response_data),
        ) as mocked_query:
            response = self.client.post(
                "/api/employee-events/v1/employees/leave-calendar/query",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
                json={
                    "employee_ids": [1, 2],
                    "from_date": "2026-03-01",
                    "to_date": "2026-03-03",
                    "statuses": [0, 1],
                    "request_types": [1, 3],
                    "department_ids": [9],
                },
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(2, body["data"]["employee_count"])
        self.assertEqual(1, body["data"]["employees"][0]["leave_event_count"])
        mocked_query.assert_called_once_with(
            employee_ids=[1, 2],
            from_date="2026-03-01",
            to_date="2026-03-03",
            statuses=[0, 1],
            request_types=[1, 3],
            department_ids=[9],
        )

    def test_leave_calendar_query_service_error_passthrough(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.get_employee_leave_calendar_batch",
            new=MagicMock(
                side_effect=EmployeeEventsError(
                    code="EMP_EVENT_LEAVE_QUERY_FAILED",
                    message="Could not fetch employee leave calendar",
                    status_code=500,
                )
            ),
        ):
            response = self.client.post(
                "/api/employee-events/v1/employees/leave-calendar/query",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
                json={
                    "employee_ids": [1],
                    "from_date": "2026-03-01",
                    "to_date": "2026-03-31",
                },
            )

        self.assertEqual(500, response.status_code)
        self.assertEqual("EMP_EVENT_LEAVE_QUERY_FAILED", response.json()["error"])

    def test_approve_forwards_status(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.approve_event",
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
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.park_event",
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
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.approve_event",
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
