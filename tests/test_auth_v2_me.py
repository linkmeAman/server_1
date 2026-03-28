"""Tests for GET /auth/v2/me."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from tests.auth_test_utils import build_headers, ensure_auth_v2_routes, testclient_requests_work


class TestAuthV2Me(unittest.TestCase):
    def setUp(self):
        ensure_auth_v2_routes()

    def test_me_returns_claims_without_db_call(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        claims = {
            "sub": "1",
            "user_id": 1,
            "contact_id": 2,
            "employee_id": 3,
            "roles": ["ops"],
            "mobile": "9990001111",
            "jti": "j",
            "iat": 1,
            "exp": 9999999999,
            "iss": "issuer",
            "aud": "aud",
            "auth_ver": 2,
            "typ": "access",
        }

        with patch(
            "app.modules.auth.dependencies.verify_v2_access_token",
            return_value=claims,
        ), patch(
            "app.core.database.get_central_db_session",
            side_effect=RuntimeError("db should not be used"),
        ):
            client = TestClient(main.app)
            try:
                response = client.get(
                    "/auth/v2/me",
                    headers=build_headers({"Authorization": "Bearer access"}),
                )
            finally:
                client.close()

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.json()["data"]["user_id"])
        self.assertEqual(
            [{"role_code": "ops", "role_name": "ops"}],
            response.json()["data"]["roles"],
        )

    def test_me_works_when_central_db_unavailable(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        claims = {
            "sub": "1",
            "user_id": 1,
            "contact_id": 2,
            "employee_id": 3,
            "roles": ["ops"],
            "mobile": "9990001111",
            "jti": "j",
            "iat": 1,
            "exp": 9999999999,
            "iss": "issuer",
            "aud": "aud",
            "auth_ver": 2,
            "typ": "access",
        }

        with patch(
            "app.modules.auth.dependencies.verify_v2_access_token",
            return_value=claims,
        ), patch(
            "app.core.database.get_central_async_engine",
            side_effect=RuntimeError("central down"),
        ):
            client = TestClient(main.app)
            try:
                response = client.get(
                    "/auth/v2/me",
                    headers=build_headers({"Authorization": "Bearer access"}),
                )
            finally:
                client.close()

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            [{"role_code": "ops", "role_name": "ops"}],
            response.json()["data"]["roles"],
        )

    def test_me_old_token_defaults_permissions_fields(self):
        if not testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        claims = {
            "sub": "1",
            "user_id": 1,
            "contact_id": 2,
            "employee_id": 3,
            "roles": [],
            "mobile": "9990001111",
            "jti": "j",
            "iat": 1,
            "exp": 9999999999,
            "iss": "issuer",
            "aud": "aud",
            "auth_ver": 2,
            "typ": "access",
        }

        with patch(
            "app.modules.auth.dependencies.verify_v2_access_token",
            return_value=claims,
        ):
            client = TestClient(main.app)
            try:
                response = client.get(
                    "/auth/v2/me",
                    headers=build_headers({"Authorization": "Bearer access"}),
                )
            finally:
                client.close()

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertEqual([], data["permissions"])
        self.assertFalse(data["is_super"])
        self.assertEqual(0, data["permissions_version"])
        self.assertEqual(1, data["permissions_schema_version"])


if __name__ == "__main__":
    unittest.main()



