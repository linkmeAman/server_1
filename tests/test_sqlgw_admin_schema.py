"""Internal SQLGW schema endpoint auth and access tests."""

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


class TestSQLGWAdminSchema(unittest.TestCase):
    def setUp(self):
        self.settings = get_settings()
        self._original_db_map = self.settings.SQL_GATEWAY_DB_ENGINE_MAP
        self._original_require_rbac = self.settings.SQLGW_ADMIN_REQUIRE_RBAC
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
            Column("name", String(100)),
        )
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(venue.insert(), [{"name": "A"}])
            conn.exec_driver_sql("CREATE VIEW emp_cont_view AS SELECT id AS emp_id, name FROM venue")

        engines.clear()
        engines["default"] = engine
        engines["central"] = engine

        self.settings.SQL_GATEWAY_DB_ENGINE_MAP = {"STORE": "default", "CENTRAL": "central"}
        self.settings.SQLGW_ADMIN_REQUIRE_RBAC = True
        clear_schema_cache()

    def tearDown(self):
        self.settings.SQL_GATEWAY_DB_ENGINE_MAP = self._original_db_map
        self.settings.SQLGW_ADMIN_REQUIRE_RBAC = self._original_require_rbac
        engines.clear()
        engines.update(self._original_engines)
        clear_schema_cache()

    def test_schema_endpoints_require_auth(self):
        if not _testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        client = TestClient(main.app)
        try:
            response = client.get("/internal/sqlgw/schema/databases")
        finally:
            client.close()

        self.assertEqual(401, response.status_code)
        self.assertEqual("SQLGW_UNAUTHORIZED", response.json()["error"])

    def test_schema_endpoints_require_admin_permission(self):
        if not _testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        with patch("app.modules.sqlgw_admin.router.validate_token", return_value={"sub": "1", "roles": ["viewer"]}):
            client = TestClient(main.app)
            try:
                response = client.get(
                    "/internal/sqlgw/schema/databases",
                    headers={"Authorization": "Bearer token"},
                )
            finally:
                client.close()

        self.assertEqual(403, response.status_code)
        self.assertEqual("SQLGW_FORBIDDEN", response.json()["error"])

    def test_schema_endpoints_return_tables_and_columns_for_admin(self):
        if not _testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        with patch("app.modules.sqlgw_admin.router.validate_token", return_value={"sub": "1", "is_admin": True}):
            client = TestClient(main.app)
            try:
                databases = client.get(
                    "/internal/sqlgw/schema/databases",
                    headers={"Authorization": "Bearer token"},
                )
                tables = client.get(
                    "/internal/sqlgw/schema/tables?db=STORE",
                    headers={"Authorization": "Bearer token"},
                )
                columns = client.get(
                    "/internal/sqlgw/schema/columns?db=STORE&table=venue",
                    headers={"Authorization": "Bearer token"},
                )
            finally:
                client.close()

        self.assertEqual(200, databases.status_code)
        self.assertIn("STORE", databases.json()["data"]["databases"])

        self.assertEqual(200, tables.status_code)
        table_names = {x["name"] for x in tables.json()["data"]["tables"]}
        self.assertIn("venue", table_names)

        self.assertEqual(200, columns.status_code)
        column_names = {x["name"] for x in columns.json()["data"]["columns"]}
        self.assertIn("id", column_names)
        self.assertIn("name", column_names)

    def test_schema_endpoints_allow_any_valid_access_token_when_rbac_disabled(self):
        if not _testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        self.settings.SQLGW_ADMIN_REQUIRE_RBAC = False
        with patch("app.modules.sqlgw_admin.router.validate_token", return_value={"sub": "1", "roles": ["viewer"]}):
            client = TestClient(main.app)
            try:
                response = client.get(
                    "/internal/sqlgw/schema/databases",
                    headers={"Authorization": "Bearer token"},
                )
            finally:
                client.close()

        self.assertEqual(200, response.status_code)


if __name__ == "__main__":
    unittest.main()
