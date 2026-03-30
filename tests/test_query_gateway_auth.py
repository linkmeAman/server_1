"""Authentication guard tests for SQL gateway route."""

import unittest
from queue import Queue
from threading import Thread
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import main


def _testclient_requests_work() -> bool:
    probe_app = FastAPI()

    @probe_app.get("/__probe")
    def _probe():
        return {"ok": True}

    result = Queue(maxsize=1)

    def _run_probe():
        try:
            client = TestClient(probe_app)
            try:
                response = client.get("/__probe")
            finally:
                client.close()
            result.put(response.status_code == 200)
        except Exception:
            result.put(False)

    thread = Thread(target=_run_probe, daemon=True)
    thread.start()
    thread.join(timeout=2.0)
    if thread.is_alive() or result.empty():
        return False
    return bool(result.get())


class TestQueryGatewayAuth(unittest.TestCase):
    def _payload(self):
        return {
            "operation": "select",
            "table": "venue",
            "columns": ["id"],
        }

    def test_missing_authorization_returns_401(self):
        if not _testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        client = TestClient(main.app)
        try:
            response = client.post("/api/query/gateway", json=self._payload())
        finally:
            client.close()

        self.assertEqual(401, response.status_code)
        body = response.json()
        self.assertEqual("SQLGW_UNAUTHORIZED", body["error"])
        self.assertIn("request_id", body.get("data", {}))
        self.assertTrue(response.headers.get("X-Request-ID"))

    def test_invalid_token_returns_401(self):
        if not _testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        with patch("app.modules.query_gateway.router.validate_token", side_effect=ValueError("invalid token")):
            client = TestClient(main.app)
            try:
                response = client.post(
                    "/api/query/gateway",
                    headers={"Authorization": "Bearer bad"},
                    json=self._payload(),
                )
            finally:
                client.close()

        self.assertEqual(401, response.status_code)
        body = response.json()
        self.assertEqual("SQLGW_UNAUTHORIZED", body["error"])

    def test_expired_token_returns_401(self):
        if not _testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        with patch("app.modules.query_gateway.router.validate_token", side_effect=ValueError("Token expired")):
            client = TestClient(main.app)
            try:
                response = client.post(
                    "/api/query/gateway",
                    headers={"Authorization": "Bearer expired"},
                    json=self._payload(),
                )
            finally:
                client.close()

        self.assertEqual(401, response.status_code)
        body = response.json()
        self.assertEqual("SQLGW_UNAUTHORIZED", body["error"])


if __name__ == "__main__":
    unittest.main()
