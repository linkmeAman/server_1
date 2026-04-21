"""Tests for POST /auth/v2/logout."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main
from app.core.database import get_central_db_session
from tests.auth_test_utils import build_headers, ensure_auth_v2_routes, testclient_requests_work


class _FakeBegin:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeCentralSession:
    def begin(self):
        return _FakeBegin()

    async def execute(self, statement, params=None):
        class _R:
            def fetchone(self):
                class _Row:
                    _mapping = {"id": 101}

                return _Row()

        return _R()

    async def commit(self):
        return None

    async def rollback(self):
        return None


class TestAuthV2Logout(unittest.TestCase):
    def setUp(self):
        ensure_auth_v2_routes()
        main.app.dependency_overrides = {}

    def tearDown(self):
        main.app.dependency_overrides = {}

    def test_logout_revokes_only_session_chain(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        async def _central_dep():
            yield _FakeCentralSession()

        main.app.dependency_overrides[get_central_db_session] = _central_dep

        with patch(
            "app.modules.auth.handlers.logout.verify_refresh_token",
            return_value={"user_id": 1, "contact_id": 2, "employee_id": 3},
        ), patch(
            "app.modules.auth.handlers.logout.refresh_token_hash",
            return_value="hash",
        ), patch(
            "app.modules.auth.handlers.logout.revoke_session_chain",
            new=AsyncMock(return_value=1),
        ) as revoke_mock:
            client = TestClient(main.app)
            try:
                response = client.post(
                    "/auth/v2/logout",
                    json={"refresh_token": "rt"},
                    headers=build_headers(),
                )
            finally:
                client.close()

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.json()["data"]["revoked"])
        self.assertEqual(1, revoke_mock.await_count)


if __name__ == "__main__":
    unittest.main()


