"""Auth flow tests for org context and permission claim expansion."""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main
from core.database import get_central_db_session, get_main_db_session
from tests.auth_test_utils import build_headers, ensure_auth_v2_routes, testclient_requests_work


class _FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        if not self._rows:
            return None
        return _FakeRow(self._rows[0])

    def fetchall(self):
        return [_FakeRow(row) for row in self._rows]


class _FakeBegin:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeMainSession:
    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM employee" in sql and "WHERE id =" in sql:
            return _FakeResult([{"id": 3}])
        return _FakeResult([])


class _FakeCentralSession:
    def begin(self):
        return _FakeBegin()

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM auth_refresh_token" in sql and "FOR UPDATE" in sql:
            return _FakeResult(
                [
                    {
                        "id": 100,
                        "user_id": 1,
                        "contact_id": 2,
                        "employee_id": 3,
                        "token_jti": "refresh-jti",
                        "token_hash": "hashed-token",
                        "used_at": None,
                        "revoked_at": None,
                        "issued_device_fingerprint_hash": "fp-1",
                    }
                ]
            )
        return _FakeResult([])

    async def commit(self):
        return None

    async def rollback(self):
        return None


class TestAuthFlowsOrgPermissions(unittest.TestCase):
    def setUp(self):
        ensure_auth_v2_routes()
        main.app.dependency_overrides = {}

        async def _main_dep():
            yield _FakeMainSession()

        async def _central_dep():
            yield _FakeCentralSession()

        main.app.dependency_overrides[get_main_db_session] = _main_dep
        main.app.dependency_overrides[get_central_db_session] = _central_dep

    def tearDown(self):
        main.app.dependency_overrides = {}

    def test_login_response_includes_org_roles_permissions(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        authz = {
            "position_id": 11,
            "position": "Sales Counselor",
            "department_id": 7,
            "department": "West",
            "roles": [{"role_code": "ops_manager", "role_name": "Ops Manager"}],
            "permissions": ["boards.lead_board:view", "reports.center_performance:view"],
            "is_super": False,
            "permissions_version": 500,
            "permissions_schema_version": 1,
        }

        with patch(
            "app.modules.auth.handlers.login_employee._resolve_main_identity",
            new=AsyncMock(return_value={"contact": {"id": 2}, "employee": {"id": 3}}),
        ), patch(
            "app.modules.auth.handlers.login_employee._resolve_central_identity",
            new=AsyncMock(return_value={"user": {"id": 1, "password": "x"}}),
        ), patch(
            "app.modules.auth.handlers.login_employee._load_lock_state",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.modules.auth.handlers.login_employee._validate_password_and_maybe_migrate",
            new=AsyncMock(return_value=True),
        ), patch(
            "app.modules.auth.handlers.login_employee.AuthorizationResolver.resolve_employee_authorization",
            new=AsyncMock(return_value=authz),
        ), patch(
            "app.modules.auth.handlers.login_employee.issue_v2_token_pair",
            return_value={"access_token": "at", "refresh_token": "rt", "jti": "j"},
        ), patch(
            "app.modules.auth.handlers.login_employee.write_audit_event",
            new=AsyncMock(),
        ):
            client = TestClient(main.app)
            try:
                response = client.post(
                    "/auth/v2/login-employee",
                    json={
                        "country_code": "+1",
                        "mobile": "9990001111",
                        "employee_id": 3,
                        "password": "secret",
                    },
                    headers=build_headers(),
                )
            finally:
                client.close()

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertEqual(11, data["position_id"])
        self.assertEqual("Sales Counselor", data["position"])
        self.assertEqual(["boards.lead_board:view", "reports.center_performance:view"], data["permissions"])
        self.assertEqual(500, data["permissions_version"])

    def test_refresh_recomputes_permissions_not_old_claims(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        with patch(
            "app.modules.auth.handlers.refresh.verify_v2_refresh_token",
            return_value={
                "jti": "refresh-jti",
                "user_id": 1,
                "contact_id": 2,
                "employee_id": 3,
                "mobile": "9990001111",
                "permissions": ["old.permission:view"],
            },
        ), patch(
            "app.modules.auth.handlers.refresh.refresh_token_hash",
            side_effect=lambda token: "hashed-token" if token == "old-r" else "new-hash",
        ), patch(
            "app.modules.auth.handlers.refresh.compute_device_fingerprint",
            return_value="fp-1",
        ), patch(
            "app.modules.auth.handlers.refresh.AuthorizationResolver.resolve_employee_authorization",
            new=AsyncMock(
                return_value={
                    "position_id": 11,
                    "position": "Sales Counselor",
                    "department_id": 7,
                    "department": "West",
                    "roles": [{"role_code": "ops_manager", "role_name": "Ops Manager"}],
                    "permissions": ["reports.center_performance:edit"],
                    "is_super": False,
                    "permissions_version": 777,
                    "permissions_schema_version": 1,
                }
            ),
        ), patch(
            "app.modules.auth.handlers.refresh.issue_v2_token_pair",
            return_value={"access_token": "new-a", "refresh_token": "new-r", "jti": "new-jti"},
        ), patch(
            "app.modules.auth.handlers.refresh.write_audit_event",
            new=AsyncMock(),
        ):
            client = TestClient(main.app)
            try:
                response = client.post(
                    "/auth/v2/refresh",
                    json={"refresh_token": "old-r"},
                    headers=build_headers(),
                )
            finally:
                client.close()

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertEqual(["reports.center_performance:edit"], data["permissions"])
        self.assertEqual(777, data["permissions_version"])


if __name__ == "__main__":
    unittest.main()


