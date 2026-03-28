"""Phase 1 routing regression tests: centralized registry + shadowing guard."""

import unittest
from queue import Queue
from threading import Thread
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import main
from core.settings import get_settings


def _build_headers():
    """Provide API key header when middleware auth is enabled."""
    settings = get_settings()
    if settings.API_KEY_ENABLED and settings.API_KEYS:
        return {"X-API-Key": settings.API_KEYS[0]}
    return {}


def _testclient_requests_work() -> bool:
    """
    Probe whether TestClient requests are operational in this runtime.

    Some constrained environments can hang in the TestClient transport layer.
    """
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
    if thread.is_alive():
        return False
    if result.empty():
        return False
    return bool(result.get())


class TestRoutingPhase1(unittest.TestCase):
    """Lock route inventory/order and prevent known shadowing regression."""

    @classmethod
    def setUpClass(cls):
        cls.routes = list(main.app.router.routes)
        cls.path_to_indices = {}
        for idx, route in enumerate(cls.routes):
            cls.path_to_indices.setdefault(route.path, []).append(idx)

    def _first_index(self, path: str) -> int:
        self.assertIn(path, self.path_to_indices, f"Missing route path: {path}")
        return self.path_to_indices[path][0]

    def test_route_snapshot_and_relative_order(self):
        required_paths = [
            "/login",
            "/refresh",
            "/logout",
            "/forgot-password",
            "/reset-password",
            "/api/example/hello",
            "/api/example/echo",
            "/api/example/calculate",
            "/api/example/users",
            "/api/example/user/{id}",
            "/api/example/create_user",
            "/api/example/random_data",
            "/api/example/async_task",
            "/api/example/status",
            "/api/geosearch/search",
            "/api/geosearch/health",
            "/api/llm/health",
            "/api/llm/models",
            "/api/llm/chat",
            "/api/llm/complete",
            "/api/llm/conversation",
            "/api/query/gateway",
            "/api/employee-events/v1/employees/realtime-data",
            "/api/employee-events/v1/calendar/events",
            "/api/employee-events/v1/employees/workshift-calendar/query",
            "/api/employee-events/v1/employees/leave-calendar/query",
            "/api/employee-events/v1/events/check-conflict",
            "/api/employee-events/v1/events",
            "/api/employee-events/v1/events/{event_id}",
            "/api/employee-events/v1/events/{event_id}/park",
            "/api/employee-events/v1/events/{event_id}/approve",
            "/api/google-calendar/v1/events",
            "/api/google-calendar/v1/events/{event_id}",
            "/internal/sqlgw/schema/databases",
            "/internal/sqlgw/schema/tables",
            "/internal/sqlgw/schema/columns",
            "/internal/sqlgw/policies",
            "/internal/sqlgw/policies/{policy_id}",
            "/internal/sqlgw/policies/{policy_id}/approve",
            "/internal/sqlgw/policies/{policy_id}/activate",
            "/internal/sqlgw/policies/{policy_id}/archive",
            "/orders/list",
            "/orders/get/{id}",
            "/orders/create",
            "/{controller}/{function}",
            "/{controller}/{function}/{item_id}",
            "/health",
            "/controllers",
            "/controllers/{controller_name}/functions",
            "/",
        ]

        for path in required_paths:
            self.assertIn(path, self.path_to_indices, f"Expected route path not found: {path}")

        dynamic_no_id_idx = self._first_index("/{controller}/{function}")
        dynamic_with_id_idx = self._first_index("/{controller}/{function}/{item_id}")

        explicit_paths = [
            "/login",
            "/refresh",
            "/logout",
            "/forgot-password",
            "/reset-password",
            "/api/example/hello",
            "/api/example/echo",
            "/api/example/calculate",
            "/api/example/users",
            "/api/example/user/{id}",
            "/api/example/create_user",
            "/api/example/random_data",
            "/api/example/async_task",
            "/api/example/status",
            "/api/geosearch/search",
            "/api/geosearch/health",
            "/api/llm/health",
            "/api/llm/models",
            "/api/llm/chat",
            "/api/llm/complete",
            "/api/llm/conversation",
            "/api/query/gateway",
            "/api/employee-events/v1/employees/realtime-data",
            "/api/employee-events/v1/calendar/events",
            "/api/employee-events/v1/employees/workshift-calendar/query",
            "/api/employee-events/v1/employees/leave-calendar/query",
            "/api/employee-events/v1/events/check-conflict",
            "/api/employee-events/v1/events",
            "/api/employee-events/v1/events/{event_id}",
            "/api/employee-events/v1/events/{event_id}/park",
            "/api/employee-events/v1/events/{event_id}/approve",
            "/api/google-calendar/v1/events",
            "/api/google-calendar/v1/events/{event_id}",
            "/internal/sqlgw/schema/databases",
            "/internal/sqlgw/schema/tables",
            "/internal/sqlgw/schema/columns",
            "/internal/sqlgw/policies",
            "/internal/sqlgw/policies/{policy_id}",
            "/internal/sqlgw/policies/{policy_id}/approve",
            "/internal/sqlgw/policies/{policy_id}/activate",
            "/internal/sqlgw/policies/{policy_id}/archive",
            "/orders/list",
            "/orders/get/{id}",
            "/orders/create",
            "/health",
            "/controllers",
            "/controllers/{controller_name}/functions",
            "/",
        ]

        for path in explicit_paths:
            idx = self._first_index(path)
            self.assertLess(
                idx,
                dynamic_no_id_idx,
                f"Explicit route must be before dynamic fallback: {path}",
            )
            self.assertLess(
                idx,
                dynamic_with_id_idx,
                f"Explicit route must be before dynamic fallback: {path}",
            )

        self.assertLess(
            self._first_index("/controllers/{controller_name}/functions"),
            dynamic_with_id_idx,
            "Shadowing guard failed: /controllers/{controller_name}/functions "
            "must be registered before /{controller}/{function}/{item_id}",
        )

        self.assertNotIn(
            "/api/v1/api/query/gateway",
            self.path_to_indices,
            "Query gateway must not be mounted under /api/v1 prefix",
        )

    def test_shadowing_regression_controllers_functions_uses_explicit_route(self):
        if not _testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        with patch("main.init_database", return_value=False):
            client = TestClient(main.app)
            try:
                response = client.get("/controllers/example/functions", headers=_build_headers())
            finally:
                client.close()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("success"))
        self.assertIsInstance(payload.get("data"), dict)
        self.assertEqual(payload["data"].get("controller"), "example")
        self.assertIn("functions", payload["data"])
        self.assertIn("hello", payload["data"]["functions"])


if __name__ == "__main__":
    unittest.main()
