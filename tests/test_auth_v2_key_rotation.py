"""Tests for auth v2 kid rotation behavior."""

from __future__ import annotations

import json
import unittest

from core.settings import get_settings


class TestAuthV2KeyRotation(unittest.TestCase):
    def test_old_key_verifies_during_window_and_retired_key_rejects(self):
        from app.modules.auth.services import token_service

        try:
            token_service._load_pyseto()
        except Exception:
            self.skipTest("pyseto is not available in this runtime")

        settings = get_settings()
        original = {
            "AUTH_V2_CURRENT_KID": settings.AUTH_V2_CURRENT_KID,
            "AUTH_V2_SIGNING_KEYS_JSON": settings.AUTH_V2_SIGNING_KEYS_JSON,
            "AUTH_V2_ISSUER": settings.AUTH_V2_ISSUER,
            "AUTH_V2_AUDIENCE": settings.AUTH_V2_AUDIENCE,
            "AUTH_V2_TOKEN_VERSION": settings.AUTH_V2_TOKEN_VERSION,
        }

        try:
            settings.AUTH_V2_ISSUER = "issuer-test"
            settings.AUTH_V2_AUDIENCE = "aud-test"
            settings.AUTH_V2_TOKEN_VERSION = 2

            settings.AUTH_V2_CURRENT_KID = "old"
            settings.AUTH_V2_SIGNING_KEYS_JSON = json.dumps(
                [
                    {
                        "kid": "old",
                        "secret": "old-secret",
                        "valid_from": "2020-01-01T00:00:00+00:00",
                        "valid_until": "2035-01-01T00:00:00+00:00",
                        "status": "active",
                    },
                    {
                        "kid": "new",
                        "secret": "new-secret",
                        "valid_from": "2020-01-01T00:00:00+00:00",
                        "valid_until": "2035-01-01T00:00:00+00:00",
                        "status": "active",
                    },
                ]
            )

            pair = token_service.issue_v2_token_pair(
                user_id=1,
                contact_id=2,
                employee_id=3,
                roles=["ops"],
                mobile="9990001111",
            )
            token = pair["access_token"]

            # Rotate current kid to new, keep old verify-only.
            settings.AUTH_V2_CURRENT_KID = "new"
            settings.AUTH_V2_SIGNING_KEYS_JSON = json.dumps(
                [
                    {
                        "kid": "old",
                        "secret": "old-secret",
                        "valid_from": "2020-01-01T00:00:00+00:00",
                        "valid_until": "2035-01-01T00:00:00+00:00",
                        "status": "verify_only",
                    },
                    {
                        "kid": "new",
                        "secret": "new-secret",
                        "valid_from": "2020-01-01T00:00:00+00:00",
                        "valid_until": "2035-01-01T00:00:00+00:00",
                        "status": "active",
                    },
                ]
            )

            claims = token_service.verify_v2_access_token(token)
            self.assertEqual(1, claims["user_id"])

            # Retire old key, verification must fail.
            settings.AUTH_V2_SIGNING_KEYS_JSON = json.dumps(
                [
                    {
                        "kid": "old",
                        "secret": "old-secret",
                        "valid_from": "2020-01-01T00:00:00+00:00",
                        "valid_until": "2035-01-01T00:00:00+00:00",
                        "status": "retired",
                    },
                    {
                        "kid": "new",
                        "secret": "new-secret",
                        "valid_from": "2020-01-01T00:00:00+00:00",
                        "valid_until": "2035-01-01T00:00:00+00:00",
                        "status": "active",
                    },
                ]
            )

            with self.assertRaises(Exception):
                token_service.verify_v2_access_token(token)
        finally:
            settings.AUTH_V2_CURRENT_KID = original["AUTH_V2_CURRENT_KID"]
            settings.AUTH_V2_SIGNING_KEYS_JSON = original["AUTH_V2_SIGNING_KEYS_JSON"]
            settings.AUTH_V2_ISSUER = original["AUTH_V2_ISSUER"]
            settings.AUTH_V2_AUDIENCE = original["AUTH_V2_AUDIENCE"]
            settings.AUTH_V2_TOKEN_VERSION = original["AUTH_V2_TOKEN_VERSION"]

    def test_access_token_length_under_8192_chars(self):
        from app.modules.auth.services import token_service

        try:
            token_service._load_pyseto()
        except Exception:
            self.skipTest("pyseto is not available in this runtime")

        settings = get_settings()
        original = {
            "AUTH_V2_CURRENT_KID": settings.AUTH_V2_CURRENT_KID,
            "AUTH_V2_SIGNING_KEYS_JSON": settings.AUTH_V2_SIGNING_KEYS_JSON,
            "AUTH_V2_ISSUER": settings.AUTH_V2_ISSUER,
            "AUTH_V2_AUDIENCE": settings.AUTH_V2_AUDIENCE,
            "AUTH_V2_TOKEN_VERSION": settings.AUTH_V2_TOKEN_VERSION,
        }
        try:
            settings.AUTH_V2_ISSUER = "issuer-test"
            settings.AUTH_V2_AUDIENCE = "aud-test"
            settings.AUTH_V2_TOKEN_VERSION = 2
            settings.AUTH_V2_CURRENT_KID = "new"
            settings.AUTH_V2_SIGNING_KEYS_JSON = json.dumps(
                [
                    {
                        "kid": "new",
                        "secret": "new-secret",
                        "valid_from": "2020-01-01T00:00:00+00:00",
                        "valid_until": "2035-01-01T00:00:00+00:00",
                        "status": "active",
                    }
                ]
            )

            permissions = [f"reports.feature_{i}:view" for i in range(200)]
            pair = token_service.issue_v2_token_pair(
                user_id=1,
                contact_id=2,
                employee_id=3,
                roles=[{"role_code": "ops", "role_name": "Ops"}],
                mobile="9990001111",
                authorization={
                    "position_id": 11,
                    "position": "Sales",
                    "department_id": 7,
                    "department": "West",
                    "roles": [{"role_code": "ops", "role_name": "Ops"}],
                    "permissions": permissions,
                    "is_super": False,
                    "permissions_version": 5,
                    "permissions_schema_version": 1,
                },
            )
            self.assertLessEqual(len(pair["access_token"]), 8192)
        finally:
            settings.AUTH_V2_CURRENT_KID = original["AUTH_V2_CURRENT_KID"]
            settings.AUTH_V2_SIGNING_KEYS_JSON = original["AUTH_V2_SIGNING_KEYS_JSON"]
            settings.AUTH_V2_ISSUER = original["AUTH_V2_ISSUER"]
            settings.AUTH_V2_AUDIENCE = original["AUTH_V2_AUDIENCE"]
            settings.AUTH_V2_TOKEN_VERSION = original["AUTH_V2_TOKEN_VERSION"]


if __name__ == "__main__":
    unittest.main()

