"""Tests for POST /auth/v2/login-employee."""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main
from app.modules.auth.constants import (
    AUTH_EMPLOYEE_INACTIVE,
    AUTH_EMPLOYEE_USER_MAPPING_MISSING,
    AUTH_IDENTITY_MISMATCH,
    AUTH_LOGIN_COOLDOWN,
    AUTH_PASSWORD_MIGRATION_DEFERRED,
)
from app.modules.auth.services.common import AuthError
from app.core.database import get_central_db_session, get_main_db_session
from tests.auth_test_utils import build_headers, ensure_auth_v2_routes, testclient_requests_work


class _FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping


class _FakeResult:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        if self._row is None:
            return None
        return _FakeRow(self._row)


class _FakeMainSession:
    async def execute(self, statement, params=None):
        return _FakeResult()


class _FakeCentralSession:
    def __init__(self, identity_row=None, fail_on_insert=False):
        self.identity_row = identity_row
        self.fail_on_insert = fail_on_insert
        self.begin_calls = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM auth_identity" in sql:
            return _FakeResult(self.identity_row)
        if self.fail_on_insert and ("INSERT INTO auth_identity" in sql or "UPDATE auth_identity" in sql):
            raise RuntimeError("migration failed")
        return _FakeResult()

    async def commit(self):
        return None

    async def rollback(self):
        return None


class TestAuthV2LoginEmployee(unittest.TestCase):
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

    def test_success_with_bcrypt_sidecar(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        self._override_sessions(_FakeMainSession(), _FakeCentralSession())

        with patch(
            "app.modules.auth.handlers.login_employee._resolve_main_identity",
            new=AsyncMock(return_value={"contact": {"id": 99}, "employee": {"id": 77}}),
        ), patch(
            "app.modules.auth.handlers.login_employee._resolve_central_identity",
            new=AsyncMock(return_value={"user": {"id": 12, "password": "x"}}),
        ), patch(
            "app.modules.auth.handlers.login_employee._load_lock_state",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.modules.auth.handlers.login_employee._validate_password_and_maybe_migrate",
            new=AsyncMock(return_value=True),
        ), patch(
            "app.modules.auth.handlers.login_employee.AuthorizationResolver.resolve_employee_authorization",
            new=AsyncMock(
                return_value={
                    "position_id": 11,
                    "position": "Sales",
                    "department_id": 7,
                    "department": "West",
                    "roles": [{"role_code": "ops", "role_name": "Ops"}],
                    "permissions": ["boards.lead_board:view"],
                    "is_super": False,
                    "permissions_version": 123,
                    "permissions_schema_version": 1,
                }
            ),
        ), patch(
            "app.modules.auth.handlers.login_employee.issue_v2_token_pair",
            return_value={"access_token": "a", "refresh_token": "r", "jti": "j"},
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
                        "employee_id": 77,
                        "password": "secret",
                    },
                    headers=build_headers(),
                )
            finally:
                client.close()

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual("a", body["data"]["access_token"])
        self.assertEqual([{"role_code": "ops", "role_name": "Ops"}], body["data"]["roles"])
        self.assertEqual(["boards.lead_board:view"], body["data"]["permissions"])

    def test_plaintext_fallback_migration_log_emitted(self):
        async def _run():
            fake_central = _FakeCentralSession(identity_row=None, fail_on_insert=True)
            with patch(
                "app.modules.auth.handlers.login_employee.write_audit_event",
                new=AsyncMock(),
            ) as audit_mock:
                from app.modules.auth.handlers.login_employee import _validate_password_and_maybe_migrate

                ok = await _validate_password_and_maybe_migrate(
                    fake_central,
                    user_id=10,
                    legacy_plain_password="legacy-pass",
                    provided_password="legacy-pass",
                    request_id_value="req-1",
                    ip_value="127.0.0.1",
                    ua_value="ua",
                )

            self.assertTrue(ok)
            self.assertTrue(audit_mock.await_count >= 1)
            called_reason = audit_mock.await_args.kwargs.get("reason_code")
            self.assertEqual(AUTH_PASSWORD_MIGRATION_DEFERRED, called_reason)

        asyncio.run(_run())

    def test_lockout_after_threshold_returns_429_auth_login_cooldown(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        self._override_sessions(_FakeMainSession(), _FakeCentralSession())

        with patch(
            "app.modules.auth.handlers.login_employee._resolve_main_identity",
            new=AsyncMock(return_value={"contact": {"id": 99}, "employee": {"id": 77}}),
        ), patch(
            "app.modules.auth.handlers.login_employee._resolve_central_identity",
            new=AsyncMock(return_value={"user": {"id": 12, "password": "x"}}),
        ), patch(
            "app.modules.auth.handlers.login_employee._load_lock_state",
            new=AsyncMock(return_value={"locked_until": datetime.utcnow() + timedelta(minutes=5)}),
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
                        "employee_id": 77,
                        "password": "secret",
                    },
                    headers=build_headers(),
                )
            finally:
                client.close()

        self.assertEqual(429, response.status_code)
        self.assertEqual(AUTH_LOGIN_COOLDOWN, response.json()["error"])

    def test_mapping_mismatch_variants(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        self._override_sessions(_FakeMainSession(), _FakeCentralSession())

        scenarios = [
            (AUTH_IDENTITY_MISMATCH, "wrong_contact"),
            (AUTH_EMPLOYEE_USER_MAPPING_MISSING, "missing_user"),
            (AUTH_EMPLOYEE_USER_MAPPING_MISSING, "inactive_map"),
        ]

        for code, _label in scenarios:
            with self.subTest(code=code):
                with patch(
                    "app.modules.auth.handlers.login_employee._resolve_main_identity",
                    new=AsyncMock(return_value={"contact": {"id": 99}, "employee": {"id": 77}}),
                ), patch(
                    "app.modules.auth.handlers.login_employee._resolve_central_identity",
                    new=AsyncMock(side_effect=AuthError(code, "mismatch", 401)),
                ), patch(
                    "app.modules.auth.handlers.login_employee._record_failed_attempt",
                    new=AsyncMock(return_value={"fail_count": 1, "locked_until": None}),
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
                                "employee_id": 77,
                                "password": "secret",
                            },
                            headers=build_headers(),
                        )
                    finally:
                        client.close()

                self.assertEqual(401, response.status_code)
                self.assertEqual(code, response.json()["error"])

    def test_employee_inactive_returns_auth_employee_inactive(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        self._override_sessions(_FakeMainSession(), _FakeCentralSession())

        with patch(
            "app.modules.auth.handlers.login_employee._resolve_main_identity",
            new=AsyncMock(side_effect=AuthError(AUTH_EMPLOYEE_INACTIVE, "inactive", 403)),
        ), patch(
            "app.modules.auth.handlers.login_employee._record_failed_attempt",
            new=AsyncMock(return_value={"fail_count": 1, "locked_until": None}),
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
                        "employee_id": 77,
                        "password": "secret",
                    },
                    headers=build_headers(),
                )
            finally:
                client.close()

        self.assertEqual(403, response.status_code)
        self.assertEqual(AUTH_EMPLOYEE_INACTIVE, response.json()["error"])


if __name__ == "__main__":
    unittest.main()


