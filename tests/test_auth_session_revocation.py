"""Unit tests for auth refresh-session revocation helpers."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.modules.auth.constants import REVOKE_REASON_LOGOUT
from app.modules.auth.services.session_revocation import revoke_session_chain


class _FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping


class _FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        if not self._rows:
            return None
        return _FakeRow(self._rows[0])

    def fetchall(self):
        return [_FakeRow(row) for row in self._rows]


class _FakeAsyncSession:
    def __init__(self, rows):
        self.rows = {int(row["id"]): dict(row) for row in rows}

    async def execute(self, statement, params=None):
        params = params or {}
        sql = str(statement)

        if "SELECT id, user_id, employee_id, rotated_from_id" in sql:
            row = self.rows.get(int(params["id"]))
            if row is None:
                return _FakeResult([])
            return _FakeResult(
                [
                    {
                        "id": row["id"],
                        "user_id": row["user_id"],
                        "employee_id": row["employee_id"],
                        "rotated_from_id": row.get("rotated_from_id"),
                    }
                ]
            )

        if "SELECT id" in sql and "WHERE rotated_from_id = :rotated_from_id" in sql:
            rotated_from_id = int(params["rotated_from_id"])
            children = [
                {"id": row["id"]}
                for row in self.rows.values()
                if row.get("rotated_from_id") == rotated_from_id
            ]
            return _FakeResult(children)

        if "UPDATE auth_refresh_token" in sql and "WHERE id IN" in sql:
            token_ids = {int(token_id) for token_id in params.get("token_ids", [])}
            revoked_count = 0
            for token_id in token_ids:
                row = self.rows.get(token_id)
                if row is None or row.get("revoked_at") is not None:
                    continue
                row["revoked_at"] = params["now"]
                row["revoke_reason"] = params["reason"]
                revoked_count += 1
            return _FakeResult([], rowcount=revoked_count)

        return _FakeResult([])


class TestAuthSessionRevocation(unittest.IsolatedAsyncioTestCase):
    async def test_revoke_session_chain_only_targets_anchor_chain(self):
        fake_db = _FakeAsyncSession(
            [
                {"id": 1, "user_id": 7, "employee_id": 3, "rotated_from_id": None, "revoked_at": None},
                {"id": 2, "user_id": 7, "employee_id": 3, "rotated_from_id": 1, "revoked_at": None},
                {"id": 3, "user_id": 7, "employee_id": 3, "rotated_from_id": 2, "revoked_at": None},
                {"id": 10, "user_id": 7, "employee_id": 3, "rotated_from_id": None, "revoked_at": None},
                {"id": 11, "user_id": 7, "employee_id": 3, "rotated_from_id": 10, "revoked_at": None},
            ]
        )

        with patch(
            "app.modules.auth.services.session_revocation.write_audit_event",
            new=AsyncMock(),
        ) as audit_mock:
            revoked_count = await revoke_session_chain(
                anchor_token_id=2,
                reason=REVOKE_REASON_LOGOUT,
                db=fake_db,
                user_id=7,
                employee_id=3,
            )

        self.assertEqual(3, revoked_count)
        self.assertIsNotNone(fake_db.rows[1]["revoked_at"])
        self.assertIsNotNone(fake_db.rows[2]["revoked_at"])
        self.assertIsNotNone(fake_db.rows[3]["revoked_at"])
        self.assertIsNone(fake_db.rows[10]["revoked_at"])
        self.assertIsNone(fake_db.rows[11]["revoked_at"])
        self.assertEqual(1, audit_mock.await_count)

    async def test_revoke_session_chain_missing_anchor_returns_zero(self):
        fake_db = _FakeAsyncSession([])
        with patch(
            "app.modules.auth.services.session_revocation.write_audit_event",
            new=AsyncMock(),
        ) as audit_mock:
            revoked_count = await revoke_session_chain(
                anchor_token_id=999,
                reason=REVOKE_REASON_LOGOUT,
                db=fake_db,
                user_id=1,
                employee_id=1,
            )

        self.assertEqual(0, revoked_count)
        self.assertEqual(0, audit_mock.await_count)


if __name__ == "__main__":
    unittest.main()
