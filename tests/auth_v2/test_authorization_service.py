"""Unit tests for auth v2 authorization resolver."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from controllers.auth_v2.services.authorization import AuthorizationResolver


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


class _FakeCentralPermissions:
    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM rbac_role_permission_v2" in sql:
            return _FakeResult(
                [
                    {
                        "role_id": 1,
                        "resource_id": 10,
                        "can_view": 1,
                        "can_add": 0,
                        "can_edit": 1,
                        "can_delete": 0,
                        "can_super": 0,
                        "resource_code": "reports.center_performance",
                    },
                    {
                        "role_id": 2,
                        "resource_id": 10,
                        "can_view": 1,
                        "can_add": 0,
                        "can_edit": 0,
                        "can_delete": 0,
                        "can_super": 0,
                        "resource_code": "reports.center_performance",
                    },
                    {
                        "role_id": 1,
                        "resource_id": 11,
                        "can_view": 0,
                        "can_add": 0,
                        "can_edit": 0,
                        "can_delete": 0,
                        "can_super": 1,
                        "resource_code": "boards.lead_board",
                    },
                    {
                        "role_id": 1,
                        "resource_id": 12,
                        "can_view": 0,
                        "can_add": 0,
                        "can_edit": 0,
                        "can_delete": 0,
                        "can_super": 1,
                        "resource_code": "global",
                    },
                ]
            )
        return _FakeResult([])


class _FakeMain:
    async def execute(self, statement, params=None):
        return _FakeResult([])


class TestAuthorizationResolver(unittest.IsolatedAsyncioTestCase):
    async def test_effective_roles_union_dedupes_direct_and_pair(self):
        resolver = AuthorizationResolver(_FakeMain(), _FakeCentralPermissions())
        with patch.object(
            resolver,
            "get_direct_roles",
            new=AsyncMock(
                return_value=[
                    {"role_id": 5, "role_code": "ops_manager", "role_name": "Ops Manager", "source": "direct"}
                ]
            ),
        ), patch.object(
            resolver,
            "get_pair_roles",
            new=AsyncMock(
                return_value=[
                    {"role_id": 5, "role_code": "ops_manager", "role_name": "Ops Manager", "source": "pair"},
                    {"role_id": 7, "role_code": "reports_analyst", "role_name": "Reports Analyst", "source": "pair"},
                ]
            ),
        ):
            result = await resolver.get_effective_roles(employee_id=1, position_id=2, department_id=3)

        self.assertEqual([5, 7], result["role_ids"])
        self.assertEqual(
            [
                {"role_code": "ops_manager", "role_name": "Ops Manager"},
                {"role_code": "reports_analyst", "role_name": "Reports Analyst"},
            ],
            result["roles"],
        )
        self.assertEqual(3, len(result["trace"]))

    async def test_effective_permissions_are_sorted_deduped_and_global_super_only(self):
        resolver = AuthorizationResolver(_FakeMain(), _FakeCentralPermissions())
        result = await resolver.get_effective_permissions([1, 2])
        self.assertEqual(
            [
                "global:super",
                "reports.center_performance:edit",
                "reports.center_performance:view",
            ],
            result["permissions"],
        )
        self.assertTrue(result["is_super"])

    async def test_resolve_employee_authorization_contains_versions_and_trace(self):
        resolver = AuthorizationResolver(_FakeMain(), _FakeCentralPermissions())
        with patch.object(
            resolver,
            "get_org_context",
            new=AsyncMock(
                return_value={
                    "employee_id": 1,
                    "position_id": 2,
                    "position": "Counselor",
                    "department_id": 3,
                    "department": "Sales",
                    "degraded_name_lookup": False,
                }
            ),
        ), patch.object(
            resolver,
            "get_effective_roles",
            new=AsyncMock(
                return_value={
                    "roles": [{"role_code": "ops", "role_name": "Ops"}],
                    "role_ids": [9],
                    "trace": [{"source": "direct", "role_id": 9}],
                }
            ),
        ), patch.object(
            resolver,
            "get_effective_permissions",
            new=AsyncMock(
                return_value={
                    "permissions": ["boards.lead_board:view"],
                    "resource_ids": [12],
                    "trace": [{"role_id": 9, "resource_id": 12, "actions": ["view"]}],
                    "is_super": False,
                }
            ),
        ), patch.object(
            resolver,
            "compute_permissions_version",
            new=AsyncMock(return_value=123456),
        ):
            data = await resolver.resolve_employee_authorization(employee_id=1)

        self.assertEqual(123456, data["permissions_version"])
        self.assertEqual(1, data["permissions_schema_version"])
        self.assertEqual(["boards.lead_board:view"], data["permissions"])
        self.assertIn("roles", data["grants_trace"])
        self.assertIn("permissions", data["grants_trace"])


if __name__ == "__main__":
    unittest.main()
