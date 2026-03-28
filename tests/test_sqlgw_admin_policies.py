"""Internal SQLGW policy endpoint lifecycle tests."""

import unittest
from queue import Queue
from threading import Thread
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine
from sqlalchemy.pool import StaticPool

import main
from app.core.database import engines
from app.core.settings import get_settings
from app.core.sqlgw_policy_store import clear_policy_cache
from app.core.sqlgw_schema import clear_schema_cache


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


class TestSQLGWAdminPolicies(unittest.TestCase):
    def setUp(self):
        self.settings = get_settings()
        self._original_db_map = self.settings.SQL_GATEWAY_DB_ENGINE_MAP
        self._original_engines = dict(engines)

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        metadata = MetaData()
        venue = Table(
            "venue",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("city", String(100)),
            Column("status", Integer),
        )
        metadata.create_all(engine)

        engines.clear()
        engines["default"] = engine
        engines["central"] = engine

        self.settings.SQL_GATEWAY_DB_ENGINE_MAP = {"STORE": "default", "CENTRAL": "central"}

        clear_policy_cache()
        clear_schema_cache()

    def tearDown(self):
        self.settings.SQL_GATEWAY_DB_ENGINE_MAP = self._original_db_map
        engines.clear()
        engines.update(self._original_engines)
        clear_policy_cache()
        clear_schema_cache()

    def _claims_for_token(self, token: str):
        if token == "admin":
            return {"sub": "1", "roles": ["sqlgw_admin"]}
        if token == "approver":
            return {"sub": "2", "roles": ["sqlgw_approver"]}
        return {"sub": "3", "roles": ["viewer"]}

    def _headers(self, token: str):
        return {"Authorization": f"Bearer {token}"}

    def _policy_body(self):
        return {
            "policy_json": {
                "venue": {
                    "db": "STORE",
                    "table_kind": "table",
                    "operations": ["select", "insert", "update", "delete"],
                    "select_columns": ["id", "city", "status"],
                    "filter_columns": ["id", "city", "status"],
                    "group_columns": ["city", "status"],
                    "order_columns": ["id", "city"],
                    "insert_columns": ["city", "status"],
                    "update_columns": ["status"],
                    "max_write_rows": 100,
                    "allow_explicit_pk_insert": False,
                }
            },
            "validate_schema": True,
            "notes": "initial policy",
        }

    def test_policy_create_approve_activate_endpoints(self):
        if not _testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        with patch(
            "app.modules.sqlgw_admin.router.validate_token",
            side_effect=lambda token, expected_type=None: self._claims_for_token(token),
        ):
            client = TestClient(main.app)
            try:
                created = client.post(
                    "/internal/sqlgw/policies",
                    headers=self._headers("admin"),
                    json=self._policy_body(),
                )
                self.assertEqual(200, created.status_code)
                policy_id = created.json()["data"]["policy"]["id"]
                self.assertEqual("draft", created.json()["data"]["policy"]["status"])

                approved = client.post(
                    f"/internal/sqlgw/policies/{policy_id}/approve",
                    headers=self._headers("approver"),
                )
                self.assertEqual(200, approved.status_code)
                self.assertEqual("approved", approved.json()["data"]["policy"]["status"])

                activated = client.post(
                    f"/internal/sqlgw/policies/{policy_id}/activate",
                    headers=self._headers("approver"),
                )
                self.assertEqual(200, activated.status_code)
                self.assertEqual("active", activated.json()["data"]["policy"]["status"])
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main()
