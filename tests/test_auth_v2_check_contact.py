"""Tests for POST /auth/v2/check-contact."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main
from core.database_v2 import get_central_db_session, get_main_db_session
from tests.auth_v2_test_utils import build_headers, ensure_auth_v2_routes, testclient_requests_work


class _FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return [_FakeRow(row) for row in self._rows]

    def fetchone(self):
        if not self._rows:
            return None
        return _FakeRow(self._rows[0])


class _FakeMainSession:
    def __init__(self, contact_rows=None, employee_rows=None):
        self.contact_rows = contact_rows or []
        self.employee_rows = employee_rows or []

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM contact" in sql:
            return _FakeResult(self.contact_rows)
        if "FROM employee" in sql:
            if "WHERE id =" in sql:
                employee_id = int((params or {}).get("employee_id") or 0)
                for row in self.employee_rows:
                    if int(row.get("employee_id") or row.get("id") or 0) == employee_id:
                        return _FakeResult(
                            [
                                {
                                    "id": int(row.get("employee_id") or row.get("id")),
                                    "position_id": row.get("position_id"),
                                    "department_id": row.get("department_id"),
                                }
                            ]
                        )
                return _FakeResult([])
            return _FakeResult(self.employee_rows)
        return _FakeResult([])


class _FailIfUsedMainSession(_FakeMainSession):
    async def execute(self, statement, params=None):
        raise AssertionError("Main DB should not be queried in this path")


class _FakeCentralSession:
    async def execute(self, statement, params=None):
        return _FakeResult([])

    async def commit(self):
        return None

    async def rollback(self):
        return None


class TestAuthV2CheckContact(unittest.TestCase):
    def setUp(self):
        ensure_auth_v2_routes()
        main.app.dependency_overrides = {}

    def tearDown(self):
        main.app.dependency_overrides = {}

    def _client(self):
        return TestClient(main.app)

    def _override_sessions(self, main_session, central_session):
        async def _main_dep():
            yield main_session

        async def _central_dep():
            yield central_session

        main.app.dependency_overrides[get_main_db_session] = _main_dep
        main.app.dependency_overrides[get_central_db_session] = _central_dep

    def test_success_with_employee_list_and_display_labels(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        main_session = _FakeMainSession(
            contact_rows=[{"id": 10, "fname": "A", "mname": "", "lname": "B"}],
            employee_rows=[
                {
                    "employee_id": 55,
                    "ecode": "EMP-55",
                    "position_id": 2,
                    "department_id": 7,
                    "position": "Sales",
                    "department": "West",
                }
            ],
        )
        central_session = _FakeCentralSession()
        self._override_sessions(main_session, central_session)

        with patch(
            "controllers.auth_v2.handlers.check_contact.count_events",
            new=AsyncMock(side_effect=[0, 0, 0, 0]),
        ), patch(
            "controllers.auth_v2.handlers.check_contact.write_audit_event",
            new=AsyncMock(),
        ):
            client = self._client()
            try:
                response = client.post(
                    "/auth/v2/check-contact",
                    json={"country_code": "+1", "mobile": "9990001111"},
                    headers=build_headers(),
                )
            finally:
                client.close()

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(10, body["data"]["contact_id"])
        self.assertEqual(55, body["data"]["employees"][0]["employee_id"])
        self.assertIn("EMP-55", body["data"]["employees"][0]["display_label"])

    def test_ambiguous_contact_returns_no_employee_data(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        main_session = _FakeMainSession(
            contact_rows=[
                {"id": 1, "fname": "A", "mname": "", "lname": "B"},
                {"id": 2, "fname": "C", "mname": "", "lname": "D"},
            ]
        )
        central_session = _FakeCentralSession()
        self._override_sessions(main_session, central_session)

        with patch(
            "controllers.auth_v2.handlers.check_contact.count_events",
            new=AsyncMock(side_effect=[0, 0, 0, 0]),
        ), patch(
            "controllers.auth_v2.handlers.check_contact.write_audit_event",
            new=AsyncMock(),
        ):
            client = self._client()
            try:
                response = client.post(
                    "/auth/v2/check-contact",
                    json={"country_code": "+1", "mobile": "9990001111"},
                    headers=build_headers(),
                )
            finally:
                client.close()

        self.assertEqual(404, response.status_code)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual("AUTH_CONTACT_NOT_FOUND", body["error"])
        self.assertIn("request_id", body["data"])
        self.assertEqual({}, body["data"]["details"])

    def test_ip_rate_limit_threshold_enforced_and_headers_present(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        self._override_sessions(_FailIfUsedMainSession(), _FakeCentralSession())

        with patch(
            "controllers.auth_v2.handlers.check_contact.count_events",
            new=AsyncMock(side_effect=[999]),
        ), patch(
            "controllers.auth_v2.handlers.check_contact.write_audit_event",
            new=AsyncMock(),
        ):
            client = self._client()
            try:
                response = client.post(
                    "/auth/v2/check-contact",
                    json={"country_code": "+1", "mobile": "9990001111"},
                    headers=build_headers(),
                )
            finally:
                client.close()

        self.assertEqual(429, response.status_code)
        body = response.json()
        self.assertEqual("AUTH_RATE_LIMITED", body["error"])
        self.assertTrue(response.headers.get("Retry-After"))
        self.assertTrue(response.headers.get("X-RateLimit-Limit"))
        self.assertTrue(response.headers.get("X-RateLimit-Remaining") is not None)
        self.assertTrue(response.headers.get("X-RateLimit-Reset"))

    def test_ip_mobile_rate_limit_threshold_enforced(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        self._override_sessions(_FailIfUsedMainSession(), _FakeCentralSession())

        with patch(
            "controllers.auth_v2.handlers.check_contact.count_events",
            new=AsyncMock(side_effect=[0, 999]),
        ), patch(
            "controllers.auth_v2.handlers.check_contact.write_audit_event",
            new=AsyncMock(),
        ):
            client = self._client()
            try:
                response = client.post(
                    "/auth/v2/check-contact",
                    json={"country_code": "+1", "mobile": "9990001111"},
                    headers=build_headers(),
                )
            finally:
                client.close()

        self.assertEqual(429, response.status_code)
        self.assertEqual("AUTH_RATE_LIMITED", response.json()["error"])

    def test_anti_enumeration_generic_response_after_n_failures(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        self._override_sessions(_FailIfUsedMainSession(), _FakeCentralSession())

        with patch(
            "controllers.auth_v2.handlers.check_contact.count_events",
            new=AsyncMock(side_effect=[0, 0, 0, 99]),
        ), patch(
            "controllers.auth_v2.handlers.check_contact.write_audit_event",
            new=AsyncMock(),
        ):
            client = self._client()
            try:
                response = client.post(
                    "/auth/v2/check-contact",
                    json={"country_code": "+1", "mobile": "9990001111"},
                    headers=build_headers(),
                )
            finally:
                client.close()

        self.assertEqual(404, response.status_code)
        self.assertEqual("AUTH_CONTACT_NOT_FOUND", response.json()["error"])

    def test_timing_floor_applied_to_both_success_and_failure_paths(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        success_main = _FakeMainSession(
            contact_rows=[{"id": 10, "fname": "A", "mname": "", "lname": "B"}],
            employee_rows=[{"employee_id": 55, "ecode": "E55", "position_id": 2, "department_id": 3}],
        )
        failure_main = _FakeMainSession(
            contact_rows=[
                {"id": 1, "fname": "A", "mname": "", "lname": "B"},
                {"id": 2, "fname": "C", "mname": "", "lname": "D"},
            ]
        )
        central = _FakeCentralSession()

        async def _main_dep_success():
            yield success_main

        async def _main_dep_failure():
            yield failure_main

        async def _central_dep():
            yield central

        main.app.dependency_overrides[get_central_db_session] = _central_dep

        with patch(
            "controllers.auth_v2.handlers.check_contact.count_events",
            new=AsyncMock(side_effect=[0, 0, 0, 0, 0, 0, 0, 0]),
        ), patch(
            "controllers.auth_v2.handlers.check_contact.write_audit_event",
            new=AsyncMock(),
        ), patch(
            "controllers.auth_v2.handlers.check_contact.apply_timing_floor",
            new=AsyncMock(),
        ) as timing_mock:
            client = self._client()
            try:
                main.app.dependency_overrides[get_main_db_session] = _main_dep_success
                r1 = client.post(
                    "/auth/v2/check-contact",
                    json={"country_code": "+1", "mobile": "9990001111"},
                    headers=build_headers(),
                )
                main.app.dependency_overrides[get_main_db_session] = _main_dep_failure
                r2 = client.post(
                    "/auth/v2/check-contact",
                    json={"country_code": "+1", "mobile": "9990001111"},
                    headers=build_headers(),
                )
            finally:
                client.close()

        self.assertEqual(200, r1.status_code)
        self.assertEqual(404, r2.status_code)
        self.assertEqual(2, timing_mock.await_count)


if __name__ == "__main__":
    unittest.main()
