"""Unit tests for legacy refresh-session chain helpers."""

from __future__ import annotations

from datetime import datetime, timedelta
import unittest

from app.modules.auth.constants import REVOKE_REASON_LOGOUT
from app.modules.auth.legacy_router import (
    _legacy_insert_refresh_row,
    _legacy_revoke_refresh_chain,
)


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


class _FakeSession:
    def __init__(self):
        self.rows = {}
        self.next_id = 1

    def execute(self, statement, params=None):
        params = params or {}
        sql = str(statement)

        if "INSERT INTO auth_refresh_token" in sql:
            token_id = self.next_id
            self.next_id += 1
            self.rows[token_id] = {
                "id": token_id,
                "user_id": int(params["user_id"]),
                "rotated_from_id": params.get("rotated_from_id"),
                "revoked_at": None,
                "revoke_reason": None,
            }
            return _FakeResult([])

        if "SELECT id, rotated_from_id" in sql:
            row = self.rows.get(int(params["id"]))
            if row is None:
                return _FakeResult([])
            return _FakeResult(
                [{"id": row["id"], "rotated_from_id": row.get("rotated_from_id")}]
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
            ids = {int(token_id) for token_id in params.get("token_ids", [])}
            revoked = 0
            for token_id in ids:
                row = self.rows.get(token_id)
                if row is None or row.get("revoked_at") is not None:
                    continue
                row["revoked_at"] = params["now"]
                row["revoke_reason"] = params["reason"]
                revoked += 1
            return _FakeResult([], rowcount=revoked)

        return _FakeResult([])


class TestLegacySessionChain(unittest.TestCase):
    def test_insert_legacy_rows_allows_multiple_active_sessions(self):
        db = _FakeSession()
        now = datetime.utcnow()
        _legacy_insert_refresh_row(
            db,
            user_id=44,
            token_jti="jti-1",
            token_hash="hash-1",
            issued_at=now,
            expires_at=now + timedelta(days=7),
        )
        _legacy_insert_refresh_row(
            db,
            user_id=44,
            token_jti="jti-2",
            token_hash="hash-2",
            issued_at=now,
            expires_at=now + timedelta(days=7),
        )

        self.assertEqual(2, len(db.rows))
        self.assertTrue(all(row["revoked_at"] is None for row in db.rows.values()))

    def test_revoke_chain_does_not_touch_other_legacy_sessions(self):
        db = _FakeSession()
        # Chain A: 1 -> 2
        db.rows[1] = {"id": 1, "user_id": 9, "rotated_from_id": None, "revoked_at": None, "revoke_reason": None}
        db.rows[2] = {"id": 2, "user_id": 9, "rotated_from_id": 1, "revoked_at": None, "revoke_reason": None}
        # Independent session B: 3
        db.rows[3] = {"id": 3, "user_id": 9, "rotated_from_id": None, "revoked_at": None, "revoke_reason": None}
        db.next_id = 4

        revoked = _legacy_revoke_refresh_chain(
            db,
            anchor_token_id=2,
            reason=REVOKE_REASON_LOGOUT,
        )

        self.assertEqual(2, revoked)
        self.assertIsNotNone(db.rows[1]["revoked_at"])
        self.assertIsNotNone(db.rows[2]["revoked_at"])
        self.assertIsNone(db.rows[3]["revoked_at"])


if __name__ == "__main__":
    unittest.main()
