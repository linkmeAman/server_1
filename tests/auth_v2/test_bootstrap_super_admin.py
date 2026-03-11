"""Tests for bootstrap_auth_v2_super_admin script."""

from __future__ import annotations

import argparse
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from scripts.auth_v2 import bootstrap_auth_v2_super_admin as bootstrap


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


class _FakeBegin:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeMainSession:
    async def execute(self, statement, params=None):
        return _FakeResult([{"id": 7, "position_id": 2, "department_id": 3}])


class _FakeCentralSession:
    def __init__(self):
        self.role = None
        self.resource = None
        self.permission = False
        self.employee_role = False

    def begin(self):
        return _FakeBegin()

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM rbac_role" in sql and "FOR UPDATE" in sql:
            if self.role is None:
                return _FakeResult([])
            return _FakeResult([self.role])
        if "INSERT INTO rbac_role (" in sql:
            self.role = {"id": 100, "code": params["code"], "name": params["name"], "is_active": 1}
            return _FakeResult([])
        if "FROM rbac_role" in sql and "WHERE code = :code" in sql:
            return _FakeResult([self.role] if self.role else [])

        if "FROM rbac_resource_v2" in sql and "FOR UPDATE" in sql:
            if self.resource is None:
                return _FakeResult([])
            return _FakeResult([self.resource])
        if "INSERT INTO rbac_resource_v2" in sql:
            self.resource = {"id": 200, "code": "global", "name": "Global", "is_active": 1}
            return _FakeResult([])
        if "FROM rbac_resource_v2" in sql and "WHERE code = :code" in sql:
            return _FakeResult([self.resource] if self.resource else [])

        if "INSERT INTO rbac_role_permission_v2" in sql:
            self.permission = True
            return _FakeResult([])
        if "INSERT INTO rbac_employee_role" in sql:
            self.employee_role = True
            return _FakeResult([])
        return _FakeResult([])


class TestBootstrapSuperAdmin(unittest.IsolatedAsyncioTestCase):
    def _args(self, *, create_role_if_missing: bool) -> argparse.Namespace:
        return argparse.Namespace(
            employee_id=7,
            role_code="auth_super_admin",
            create_role_if_missing=create_role_if_missing,
            role_name="Auth Super Admin",
        )

    async def test_missing_role_fails_without_create_flag(self):
        main_session = _FakeMainSession()
        central_session = _FakeCentralSession()

        @asynccontextmanager
        async def _main_ctx():
            yield main_session

        @asynccontextmanager
        async def _central_ctx():
            yield central_session

        with patch("scripts.auth_v2.bootstrap_auth_v2_super_admin.main_session_context", _main_ctx), patch(
            "scripts.auth_v2.bootstrap_auth_v2_super_admin.central_session_context", _central_ctx
        ), patch(
            "scripts.auth_v2.bootstrap_auth_v2_super_admin.AuthorizationResolver.resolve_employee_authorization",
            new=AsyncMock(return_value={"roles": [], "permissions": [], "is_super": False, "permissions_version": 0}),
        ):
            with self.assertRaises(RuntimeError):
                await bootstrap._run(self._args(create_role_if_missing=False))

    async def test_bootstrap_is_idempotent_and_sets_super_permission(self):
        main_session = _FakeMainSession()
        central_session = _FakeCentralSession()

        @asynccontextmanager
        async def _main_ctx():
            yield main_session

        @asynccontextmanager
        async def _central_ctx():
            yield central_session

        with patch("scripts.auth_v2.bootstrap_auth_v2_super_admin.main_session_context", _main_ctx), patch(
            "scripts.auth_v2.bootstrap_auth_v2_super_admin.central_session_context", _central_ctx
        ), patch(
            "scripts.auth_v2.bootstrap_auth_v2_super_admin.AuthorizationResolver.resolve_employee_authorization",
            new=AsyncMock(
                return_value={
                    "roles": [{"role_code": "auth_super_admin", "role_name": "Auth Super Admin"}],
                    "permissions": ["global:super"],
                    "is_super": True,
                    "permissions_version": 1,
                }
            ),
        ):
            await bootstrap._run(self._args(create_role_if_missing=True))
            await bootstrap._run(self._args(create_role_if_missing=True))

        self.assertTrue(central_session.permission)
        self.assertTrue(central_session.employee_role)
        self.assertIsNotNone(central_session.role)
        self.assertIsNotNone(central_session.resource)


if __name__ == "__main__":
    unittest.main()
