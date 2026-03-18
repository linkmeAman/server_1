"""Route tests for Employee Events V1 demo events endpoint."""

import unittest
from queue import Queue
from threading import Thread
from unittest.mock import patch

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


class TestDemoEventsQueryRoute(unittest.TestCase):
    def setUp(self):
        if not _testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()

    def test_demo_query_missing_app_token_returns_401(self):
        response = self.client.post(
            "/api/employee-events/v1/demo/query",
            headers={**_middleware_headers()},
            json={
                "employee_ids": [1, 2],
                "from_date": "2026-03-01",
                "to_date": "2026-03-31",
            },
        )
        self.assertEqual(401, response.status_code)
        self.assertEqual("EMP_EVENT_UNAUTHORIZED", response.json()["error"])

    def test_demo_query_invalid_json_returns_400(self):
        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ):
            response = self.client.post(
                "/api/employee-events/v1/demo/query",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                    "Content-Type": "application/json",
                },
                data="{not-json",
            )

        self.assertEqual(400, response.status_code)
        body = response.json()
        self.assertEqual("EMP_EVENT_INVALID_DEMO_QUERY", body["error"])

    def test_demo_query_invalid_payload_returns_400(self):
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
                        "/api/employee-events/v1/demo/query",
                        headers={
                            **_middleware_headers(),
                            "Authorization": "Bearer ok",
                        },
                        json=payload,
                    )
                    self.assertEqual(400, response.status_code)
                    self.assertEqual(
                        "EMP_EVENT_INVALID_DEMO_QUERY",
                        response.json()["error"],
                    )

    def test_demo_query_semantic_failures_return_400(self):
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
                "venue_ids": [0],
            },
            {
                "employee_ids": [1],
                "from_date": "2026-03-01",
                "to_date": "2026-03-31",
                "batch_ids": [0],
            },
        ]

        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ):
            for payload in cases:
                with self.subTest(payload=payload):
                    response = self.client.post(
                        "/api/employee-events/v1/demo/query",
                        headers={
                            **_middleware_headers(),
                            "Authorization": "Bearer ok",
                        },
                        json=payload,
                    )
                    self.assertEqual(400, response.status_code)
                    self.assertEqual(
                        "EMP_EVENT_INVALID_DEMO_QUERY",
                        response.json()["error"],
                    )

    def test_demo_query_valid_request_returns_success(self):
        mock_result = {
            "from_date": "2026-03-01",
            "to_date": "2026-03-31",
            "range_day_count": 31,
            "employee_count": 1,
            "matched_count": 1,
            "total_demos": 2,
            "employees": [
                {
                    "employee_id": 1,
                    "demos": [
                        {"id": 10, "host_employee_id": 1, "name": "Demo A"},
                        {"id": 11, "host_employee_id": 1, "name": "Demo B"},
                    ],
                    "demo_count": 2,
                }
            ],
        }

        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.get_demo_events_batch",
            return_value=mock_result,
        ):
            response = self.client.post(
                "/api/employee-events/v1/demo/query",
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

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("Demo events fetched successfully", body["message"])
        self.assertEqual(1, body["data"]["employee_count"])
        self.assertEqual(2, body["data"]["total_demos"])

    def test_demo_query_with_all_optional_filters(self):
        mock_result = {
            "from_date": "2026-03-01",
            "to_date": "2026-03-31",
            "range_day_count": 31,
            "employee_count": 1,
            "matched_count": 0,
            "total_demos": 0,
            "employees": [
                {"employee_id": 1, "demos": [], "demo_count": 0}
            ],
        }

        with patch(
            "controllers.employee_events_v1.dependencies.validate_token",
            return_value={"sub": "100", "typ": "access"},
        ), patch(
            "controllers.employee_events_v1.services.event_service.EmployeeEventsService.get_demo_events_batch",
            return_value=mock_result,
        ) as mock_batch:
            response = self.client.post(
                "/api/employee-events/v1/demo/query",
                headers={
                    **_middleware_headers(),
                    "Authorization": "Bearer ok",
                },
                json={
                    "employee_ids": [1],
                    "from_date": "2026-03-01",
                    "to_date": "2026-03-31",
                    "statuses": [1, 2],
                    "types": [3],
                    "venue_ids": [10],
                    "batch_ids": [5],
                },
            )

        self.assertEqual(200, response.status_code)
        # When patching at class level, first positional arg is self
        call_args = mock_batch.call_args
        call_kwargs = call_args[1]
        self.assertEqual([1, 2], call_kwargs["statuses"])
        self.assertEqual([3], call_kwargs["types"])
        self.assertEqual([10], call_kwargs["venue_ids"])
        self.assertEqual([5], call_kwargs["batch_ids"])


if __name__ == "__main__":
    unittest.main()
