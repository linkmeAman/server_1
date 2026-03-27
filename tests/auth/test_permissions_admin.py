"""Tests for internal auth v2 permissions admin endpoints."""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

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


class _FakeCentralSession:
    def begin(self):
        return _FakeBegin()

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM rbac_role" in sql and "ORDER BY code" in sql:
            return _FakeResult(
                [
                    {
                        "id": 1,
                        "code": "ops_manager",
                        "name": "Ops Manager",
                        "is_active": 1,
                        "modified_at": datetime.utcnow(),
                    }
                ]
            )
        if "SELECT id" in sql and "FROM rbac_role" in sql and "ORDER BY" not in sql:
            return _FakeResult([{"id": 1}])
        if "SELECT id" in sql and "FROM employee_position" in sql:
            return _FakeResult([{"id": 2}])
        if "SELECT id" in sql and "FROM employee_department" in sql:
            return _FakeResult([{"id": 3}])
        if "SELECT code" in sql and "FROM rbac_resource_v2" in sql:
            return _FakeResult([{"code": "boards.lead_board"}])
        if "FROM rbac_position_department_role_v2" in sql and "FOR UPDATE" in sql:
            return _FakeResult(
                [
                    {
                        "id": 50,
                        "position_id": 2,
                        "department_id": 3,
                        "role_id": 1,
                        "is_active": 1,
                        "modified_at": datetime.utcnow(),
                    }
                ]
            )
        return _FakeResult([])

    async def commit(self):
        return None

    async def rollback(self):
        return None


class _FakeMainSession:
    async def execute(self, statement, params=None):
        return _FakeResult([])


class TestPermissionsAdmin(unittest.TestCase):
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

    def _claims(self, *, auth_ver=2, is_super=False):
        return {
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
            "auth_ver": auth_ver,
            "typ": "access",
            "permissions": ["global:super"] if is_super else [],
            "is_super": is_super,
            "permissions_version": 1,
            "permissions_schema_version": 1,
        }

    def test_legacy_token_rejected(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        with patch(
            "app.modules.auth.dependencies.verify_v2_access_token",
            return_value=self._claims(auth_ver=1, is_super=True),
        ):
            client = TestClient(main.app)
            try:
                response = client.get(
                    "/internal/auth/v2/permissions/roles",
                    headers=build_headers({"Authorization": "Bearer access"}),
                )
            finally:
                client.close()

        self.assertEqual(401, response.status_code)
        self.assertEqual("AUTH_TOKEN_VERSION_MISMATCH", response.json()["error"])

    def test_non_super_forbidden(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        with patch(
            "app.modules.auth.dependencies.verify_v2_access_token",
            return_value=self._claims(is_super=False),
        ):
            client = TestClient(main.app)
            try:
                response = client.get(
                    "/internal/auth/v2/permissions/roles",
                    headers=build_headers({"Authorization": "Bearer access"}),
                )
            finally:
                client.close()

        self.assertEqual(403, response.status_code)
        self.assertEqual("AUTH_FORBIDDEN", response.json()["error"])

    def test_super_can_list_roles(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        with patch(
            "app.modules.auth.dependencies.verify_v2_access_token",
            return_value=self._claims(is_super=True),
        ):
            client = TestClient(main.app)
            try:
                response = client.get(
                    "/internal/auth/v2/permissions/roles",
                    headers=build_headers({"Authorization": "Bearer access"}),
                )
            finally:
                client.close()

        self.assertEqual(200, response.status_code)
        self.assertEqual("ops_manager", response.json()["data"]["roles"][0]["code"])

    def test_non_global_can_super_rejected(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        with patch(
            "app.modules.auth.dependencies.verify_v2_access_token",
            return_value=self._claims(is_super=True),
        ):
            client = TestClient(main.app)
            try:
                response = client.put(
                    "/internal/auth/v2/permissions/role-permissions",
                    json={
                        "role_id": 1,
                        "resource_id": 10,
                        "can_view": 1,
                        "can_add": 0,
                        "can_edit": 0,
                        "can_delete": 0,
                        "can_super": 1,
                        "is_active": 1,
                        "expected_modified_at": 0,
                    },
                    headers=build_headers({"Authorization": "Bearer access"}),
                )
            finally:
                client.close()

        self.assertEqual(400, response.status_code)
        self.assertEqual("AUTH_BAD_REQUEST", response.json()["error"])

    def test_put_mapping_missing_expected_modified_at_is_400(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        with patch(
            "app.modules.auth.dependencies.verify_v2_access_token",
            return_value=self._claims(is_super=True),
        ):
            client = TestClient(main.app)
            try:
                response = client.put(
                    "/internal/auth/v2/permissions/position-department-roles",
                    json={
                        "position_id": 2,
                        "department_id": 3,
                        "role_id": 1,
                        "is_active": 1,
                    },
                    headers=build_headers({"Authorization": "Bearer access"}),
                )
            finally:
                client.close()

        self.assertEqual(400, response.status_code)
        self.assertEqual("AUTH_BAD_REQUEST", response.json()["error"])

    def test_put_mapping_version_conflict_is_409(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        with patch(
            "app.modules.auth.dependencies.verify_v2_access_token",
            return_value=self._claims(is_super=True),
        ):
            client = TestClient(main.app)
            try:
                response = client.put(
                    "/internal/auth/v2/permissions/position-department-roles",
                    json={
                        "position_id": 2,
                        "department_id": 3,
                        "role_id": 1,
                        "is_active": 1,
                        "expected_modified_at": 1,
                    },
                    headers=build_headers({"Authorization": "Bearer access"}),
                )
            finally:
                client.close()

        self.assertEqual(409, response.status_code)
        self.assertEqual("AUTH_BAD_REQUEST", response.json()["error"])


if __name__ == "__main__":
    unittest.main()


