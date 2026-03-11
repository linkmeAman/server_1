"""Tests for internal password-changed webhook."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main
from core.database_v2 import get_central_db_session
from core.settings import get_settings
from tests.auth_v2_test_utils import ensure_auth_v2_routes, testclient_requests_work


class _FakeBegin:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeCentralSession:
    def begin(self):
        return _FakeBegin()

    async def execute(self, statement, params=None):
        return None

    async def commit(self):
        return None

    async def rollback(self):
        return None


class TestAuthV2InternalPasswordChanged(unittest.TestCase):
    def setUp(self):
        ensure_auth_v2_routes()
        main.app.dependency_overrides = {}

    def tearDown(self):
        main.app.dependency_overrides = {}

    def test_password_change_webhook_revokes_all_sessions_for_user(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        async def _central_dep():
            yield _FakeCentralSession()

        main.app.dependency_overrides[get_central_db_session] = _central_dep

        settings = get_settings()
        api_key = settings.API_KEYS[0] if settings.API_KEYS else "test-key"
        if not settings.API_KEYS:
            settings.API_KEYS = [api_key]

        with patch(
            "controllers.auth_v2.handlers.internal_password_changed.revoke_all_sessions_for_user",
            new=AsyncMock(return_value=6),
        ) as revoke_mock:
            client = TestClient(main.app)
            try:
                response = client.post(
                    "/internal/auth/v2/events/password-changed",
                    json={"user_id": 42, "reason": "manual"},
                    headers={"X-API-Key": api_key, "X-Internal-Caller": "unit-test"},
                )
            finally:
                client.close()

        self.assertEqual(200, response.status_code)
        self.assertEqual(6, response.json()["data"]["revoked"])
        self.assertEqual(1, revoke_mock.await_count)


if __name__ == "__main__":
    unittest.main()
