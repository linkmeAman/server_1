"""Tests for auth v2 dependency."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from controllers.auth.constants import AUTH_TOKEN_VERSION_MISMATCH
from controllers.auth.dependencies import require_v2_auth
from controllers.auth.services.common import AuthError


class TestAuthV2Dependency(unittest.TestCase):
    def test_rejects_auth_ver_1_token_with_auth_token_version_mismatch(self):
        async def _run():
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
                "auth_ver": 1,
                "typ": "access",
            }
            with patch("controllers.auth.dependencies.verify_v2_access_token", return_value=claims):
                with self.assertRaises(AuthError) as ctx:
                    await require_v2_auth(authorization="Bearer token")

            self.assertEqual(AUTH_TOKEN_VERSION_MISMATCH, ctx.exception.code)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
