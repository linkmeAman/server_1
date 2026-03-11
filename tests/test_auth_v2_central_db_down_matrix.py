"""Central DB down behavior matrix tests for auth v2 endpoints."""

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
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return [_FakeRow(row) for row in self._rows]

    def fetchone(self):
        if not self._rows:
            return None
        return _FakeRow(self._rows[0])


class _MainHealthy:
    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM contact" in sql:
            return _FakeResult([{"id": 10, "fname": "A", "mname": "", "lname": "B"}])
        if "FROM employee" in sql and "WHERE id =" in sql:
            return _FakeResult([{"id": 3, "contact_id": 10, "status": 1}])
        if "FROM employee" in sql:
            return _FakeResult(
                [
                    {
                        "employee_id": 3,
                        "ecode": "E-3",
                        "position_id": 2,
                        "department_id": 5,
                    }
                ]
            )
        return _FakeResult([])


class _CentralDownBegin:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _CentralDown:
    def begin(self):
        return _CentralDownBegin()

    async def execute(self, statement, params=None):
        raise RuntimeError("central db down")

    async def commit(self):
        raise RuntimeError("central db down")

    async def rollback(self):
        return None


class TestAuthV2CentralDbDownMatrix(unittest.TestCase):
    def setUp(self):
        ensure_auth_v2_routes()
        main.app.dependency_overrides = {}

        async def _main_dep():
            yield _MainHealthy()

        async def _central_dep():
            yield _CentralDown()

        main.app.dependency_overrides[get_main_db_session] = _main_dep
        main.app.dependency_overrides[get_central_db_session] = _central_dep

    def tearDown(self):
        main.app.dependency_overrides = {}

    def test_matrix(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        claims = {
            "sub": "1",
            "user_id": 1,
            "contact_id": 2,
            "employee_id": 3,
            "roles": [{"role_code": "ops", "role_name": "Ops"}],
            "mobile": "9990001111",
            "jti": "j",
            "iat": 1,
            "exp": 9999999999,
            "iss": "issuer",
            "aud": "aud",
            "auth_ver": 2,
            "typ": "access",
            "permissions": [],
            "is_super": False,
            "permissions_version": 0,
            "permissions_schema_version": 1,
        }

        with patch(
            "controllers.auth_v2.handlers.check_contact.apply_timing_floor",
            new=AsyncMock(),
        ), patch(
            "controllers.auth_v2.handlers.refresh.verify_v2_refresh_token",
            return_value={
                "jti": "rj",
                "user_id": 1,
                "contact_id": 2,
                "employee_id": 3,
                "mobile": "9990001111",
            },
        ), patch(
            "controllers.auth_v2.handlers.logout.verify_v2_refresh_token",
            return_value={"user_id": 1, "contact_id": 2, "employee_id": 3},
        ), patch(
            "controllers.auth_v2.dependencies.verify_v2_access_token",
            return_value=claims,
        ):
            client = TestClient(main.app)
            try:
                r_check = client.post(
                    "/auth/v2/check-contact",
                    json={"country_code": "+1", "mobile": "9990001111"},
                    headers=build_headers(),
                )
                r_login = client.post(
                    "/auth/v2/login-employee",
                    json={
                        "country_code": "+1",
                        "mobile": "9990001111",
                        "employee_id": 3,
                        "password": "p",
                    },
                    headers=build_headers(),
                )
                r_refresh = client.post(
                    "/auth/v2/refresh",
                    json={"refresh_token": "rt"},
                    headers=build_headers(),
                )
                r_logout = client.post(
                    "/auth/v2/logout",
                    json={"refresh_token": "rt"},
                    headers=build_headers(),
                )
                r_me = client.get(
                    "/auth/v2/me",
                    headers=build_headers({"Authorization": "Bearer at"}),
                )
            finally:
                client.close()

        self.assertEqual(200, r_check.status_code)
        self.assertEqual(3, r_check.json()["data"]["employees"][0]["employee_id"])
        self.assertIsNone(r_check.json()["data"]["employees"][0]["position"])
        self.assertIsNone(r_check.json()["data"]["employees"][0]["department"])
        self.assertEqual(503, r_login.status_code)
        self.assertEqual(503, r_refresh.status_code)
        self.assertEqual(503, r_logout.status_code)
        self.assertEqual(200, r_me.status_code)


if __name__ == "__main__":
    unittest.main()
