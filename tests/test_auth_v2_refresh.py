"""Tests for POST /auth/v2/refresh."""

from __future__ import annotations

import unittest
from datetime import datetime
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
    def __init__(self, shared_state, roles=None):
        self.shared_state = shared_state
        self.roles = roles or ["ops", "admin"]
        self.queries = []

    def begin(self):
        return _FakeBegin()

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.queries.append(sql)

        if "FROM auth_refresh_token_v2" in sql and "FOR UPDATE" in sql:
            row = {
                "id": 100,
                "user_id": self.shared_state["user_id"],
                "contact_id": self.shared_state["contact_id"],
                "employee_id": self.shared_state["employee_id"],
                "token_jti": self.shared_state["token_jti"],
                "token_hash": self.shared_state["token_hash"],
                "used_at": self.shared_state.get("used_at"),
                "revoked_at": self.shared_state.get("revoked_at"),
                "issued_device_fingerprint_hash": self.shared_state.get("issued_device_fingerprint_hash"),
            }
            return _FakeResult([row])

        if "SET used_at" in sql and "UPDATE auth_refresh_token_v2" in sql:
            self.shared_state["used_at"] = datetime.utcnow()
            return _FakeResult([])

        if "SET revoked_at" in sql and "UPDATE auth_refresh_token_v2" in sql:
            self.shared_state["revoked_at"] = datetime.utcnow()
            return _FakeResult([])

        if "SELECT rr.code" in sql:
            return _FakeResult([{"code": role} for role in self.roles])

        return _FakeResult([])

    async def commit(self):
        return None

    async def rollback(self):
        return None


class _FakeMainSession:
    def __init__(self, employee_active=True):
        self.employee_active = employee_active

    async def execute(self, statement, params=None):
        if self.employee_active:
            return _FakeResult([{"id": 1}])
        return _FakeResult([])


class TestAuthV2Refresh(unittest.TestCase):
    def setUp(self):
        ensure_auth_v2_routes()
        main.app.dependency_overrides = {}

    def tearDown(self):
        main.app.dependency_overrides = {}

    def _override_sessions(self, main_session, central_session):
        async def _main_dep():
            yield main_session

        async def _central_dep():
            yield central_session

        main.app.dependency_overrides[get_main_db_session] = _main_dep
        main.app.dependency_overrides[get_central_db_session] = _central_dep

    def _base_claims(self):
        return {
            "jti": "refresh-jti",
            "user_id": 1,
            "contact_id": 2,
            "employee_id": 3,
            "mobile": "9990001111",
        }

    def _shared_state(self):
        return {
            "user_id": 1,
            "contact_id": 2,
            "employee_id": 3,
            "token_jti": "refresh-jti",
            "token_hash": "hashed-token",
            "used_at": None,
            "revoked_at": None,
            "issued_device_fingerprint_hash": "fp-1",
        }

    def test_one_time_rotation_returns_new_token_pair(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        shared = self._shared_state()
        central = _FakeCentralSession(shared)
        self._override_sessions(_FakeMainSession(True), central)

        with patch(
            "controllers.auth_v2.handlers.refresh.verify_v2_refresh_token",
            return_value=self._base_claims(),
        ), patch(
            "controllers.auth_v2.handlers.refresh.AuthorizationResolver.resolve_employee_authorization",
            new=AsyncMock(
                return_value={
                    "position_id": 9,
                    "position": "Ops",
                    "department_id": 4,
                    "department": "HQ",
                    "roles": [{"role_code": "ops", "role_name": "Ops"}],
                    "permissions": ["boards.lead_board:view"],
                    "is_super": False,
                    "permissions_version": 22,
                    "permissions_schema_version": 1,
                }
            ),
        ), patch(
            "controllers.auth_v2.handlers.refresh.refresh_token_hash",
            side_effect=lambda token: "hashed-token" if token == "old-r" else "new-hash",
        ), patch(
            "controllers.auth_v2.handlers.refresh.compute_device_fingerprint",
            return_value="fp-1",
        ), patch(
            "controllers.auth_v2.handlers.refresh.issue_v2_token_pair",
            return_value={"access_token": "new-a", "refresh_token": "new-r", "jti": "new-jti"},
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
        self.assertEqual("new-a", response.json()["data"]["access_token"])
        self.assertEqual([{"role_code": "ops", "role_name": "Ops"}], response.json()["data"]["roles"])
        self.assertTrue(any("FOR UPDATE" in q for q in central.queries))

    def test_replay_detection_returns_401_and_session_family_revoked(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        shared = self._shared_state()
        shared["used_at"] = datetime.utcnow()
        central = _FakeCentralSession(shared)
        self._override_sessions(_FakeMainSession(True), central)

        with patch(
            "controllers.auth_v2.handlers.refresh.verify_v2_refresh_token",
            return_value=self._base_claims(),
        ), patch(
            "controllers.auth_v2.handlers.refresh.refresh_token_hash",
            return_value="hashed-token",
        ), patch(
            "controllers.auth_v2.handlers.refresh.revoke_session_family",
            new=AsyncMock(return_value=3),
        ) as revoke_mock:
            client = TestClient(main.app)
            try:
                response = client.post(
                    "/auth/v2/refresh",
                    json={"refresh_token": "old-r"},
                    headers=build_headers(),
                )
            finally:
                client.close()

        self.assertEqual(401, response.status_code)
        self.assertEqual("AUTH_REFRESH_REPLAY_DETECTED", response.json()["error"])
        self.assertEqual(1, revoke_mock.await_count)

    def test_concurrent_refresh_race_only_one_succeeds(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        shared = self._shared_state()
        central = _FakeCentralSession(shared)
        self._override_sessions(_FakeMainSession(True), central)

        with patch(
            "controllers.auth_v2.handlers.refresh.verify_v2_refresh_token",
            return_value=self._base_claims(),
        ), patch(
            "controllers.auth_v2.handlers.refresh.AuthorizationResolver.resolve_employee_authorization",
            new=AsyncMock(
                return_value={
                    "position_id": 9,
                    "position": "Ops",
                    "department_id": 4,
                    "department": "HQ",
                    "roles": [{"role_code": "ops", "role_name": "Ops"}],
                    "permissions": ["boards.lead_board:view"],
                    "is_super": False,
                    "permissions_version": 22,
                    "permissions_schema_version": 1,
                }
            ),
        ), patch(
            "controllers.auth_v2.handlers.refresh.refresh_token_hash",
            side_effect=lambda token: "hashed-token" if token == "old-r" else "new-hash",
        ), patch(
            "controllers.auth_v2.handlers.refresh.compute_device_fingerprint",
            return_value="fp-1",
        ), patch(
            "controllers.auth_v2.handlers.refresh.issue_v2_token_pair",
            return_value={"access_token": "new-a", "refresh_token": "new-r", "jti": "new-jti"},
        ), patch(
            "controllers.auth_v2.handlers.refresh.revoke_session_family",
            new=AsyncMock(return_value=2),
        ):
            client = TestClient(main.app)
            try:
                r1 = client.post("/auth/v2/refresh", json={"refresh_token": "old-r"}, headers=build_headers())
                r2 = client.post("/auth/v2/refresh", json={"refresh_token": "old-r"}, headers=build_headers())
            finally:
                client.close()

        codes = sorted([r1.status_code, r2.status_code])
        self.assertEqual([401, 200], codes)

    def test_fingerprint_mismatch_returns_401_auth_session_binding_failed(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        shared = self._shared_state()
        shared["issued_device_fingerprint_hash"] = "issued-fp"
        central = _FakeCentralSession(shared)
        self._override_sessions(_FakeMainSession(True), central)

        with patch(
            "controllers.auth_v2.handlers.refresh.verify_v2_refresh_token",
            return_value=self._base_claims(),
        ), patch(
            "controllers.auth_v2.handlers.refresh.refresh_token_hash",
            return_value="hashed-token",
        ), patch(
            "controllers.auth_v2.handlers.refresh.compute_device_fingerprint",
            return_value="different-fp",
        ):
            client = TestClient(main.app)
            try:
                response = client.post("/auth/v2/refresh", json={"refresh_token": "old-r"}, headers=build_headers())
            finally:
                client.close()

        self.assertEqual(401, response.status_code)
        self.assertEqual("AUTH_SESSION_BINDING_FAILED", response.json()["error"])

    def test_employee_deactivated_mid_session_returns_403(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        shared = self._shared_state()
        central = _FakeCentralSession(shared)
        self._override_sessions(_FakeMainSession(False), central)

        with patch(
            "controllers.auth_v2.handlers.refresh.verify_v2_refresh_token",
            return_value=self._base_claims(),
        ), patch(
            "controllers.auth_v2.handlers.refresh.refresh_token_hash",
            return_value="hashed-token",
        ), patch(
            "controllers.auth_v2.handlers.refresh.compute_device_fingerprint",
            return_value="fp-1",
        ):
            client = TestClient(main.app)
            try:
                response = client.post("/auth/v2/refresh", json={"refresh_token": "old-r"}, headers=build_headers())
            finally:
                client.close()

        self.assertEqual(403, response.status_code)
        self.assertEqual("AUTH_EMPLOYEE_INACTIVE", response.json()["error"])

    def test_role_deactivated_recomputed_roles_exclude_it(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        shared = self._shared_state()
        central = _FakeCentralSession(shared, roles=["active_role"])
        self._override_sessions(_FakeMainSession(True), central)

        with patch(
            "controllers.auth_v2.handlers.refresh.verify_v2_refresh_token",
            return_value=self._base_claims(),
        ), patch(
            "controllers.auth_v2.handlers.refresh.AuthorizationResolver.resolve_employee_authorization",
            new=AsyncMock(
                return_value={
                    "position_id": 9,
                    "position": "Ops",
                    "department_id": 4,
                    "department": "HQ",
                    "roles": [{"role_code": "active_role", "role_name": "Active Role"}],
                    "permissions": ["reports.top_summary:view"],
                    "is_super": False,
                    "permissions_version": 99,
                    "permissions_schema_version": 1,
                }
            ),
        ), patch(
            "controllers.auth_v2.handlers.refresh.refresh_token_hash",
            side_effect=lambda token: "hashed-token" if token == "old-r" else "new-hash",
        ), patch(
            "controllers.auth_v2.handlers.refresh.compute_device_fingerprint",
            return_value="fp-1",
        ), patch(
            "controllers.auth_v2.handlers.refresh.issue_v2_token_pair",
            return_value={"access_token": "new-a", "refresh_token": "new-r", "jti": "new-jti"},
        ):
            client = TestClient(main.app)
            try:
                response = client.post("/auth/v2/refresh", json={"refresh_token": "old-r"}, headers=build_headers())
            finally:
                client.close()

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            [{"role_code": "active_role", "role_name": "Active Role"}],
            response.json()["data"]["roles"],
        )


if __name__ == "__main__":
    unittest.main()
