"""Regression guard: legacy /login remains unaffected by auth v2 additions."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from tests.auth_test_utils import build_headers, testclient_requests_work


class _FakeUser:
    def __init__(self):
        self.id = 123
        self.mobile = "9990001111"


class _FakeIdentity:
    def __init__(self):
        self.refresh_token = None


class _FakeDb:
    def add(self, item):
        return None

    def commit(self):
        return None

    def close(self):
        return None


class TestLegacyLoginRegression(unittest.TestCase):
    def test_legacy_login_unaffected(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        with patch("app.modules.auth.legacy_router.get_db_session", return_value=_FakeDb()), patch(
            "app.modules.auth.legacy_router.authenticate_user",
            return_value=(_FakeUser(), _FakeIdentity()),
        ), patch("app.modules.auth.legacy_router.create_access_token", return_value="legacy-access"), patch(
            "app.modules.auth.legacy_router.create_refresh_token", return_value="legacy-refresh"
        ):
            client = TestClient(main.app)
            try:
                response = client.post(
                    "/login",
                    json={"mobile": "9990001111", "password": "pass"},
                    headers=build_headers(),
                )
            finally:
                client.close()

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("legacy-access", body["access_token"])
        self.assertEqual("legacy-refresh", body["refresh_token"])
        self.assertEqual("Bearer", body["token_type"])


if __name__ == "__main__":
    unittest.main()
