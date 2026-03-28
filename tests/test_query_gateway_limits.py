"""Complexity and payload size limit tests for SQL gateway."""

import unittest
from queue import Queue
from threading import Thread
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine
from sqlalchemy.pool import StaticPool

import main
from core.database import engines
from core.settings import get_settings
from core.sql_gateway import SQLGatewayError, clear_metadata_cache, execute_gateway_request, parse_gateway_payload


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


class TestQueryGatewayLimits(unittest.TestCase):
    def setUp(self):
        self.settings = get_settings()
        self._original = {
            "SQL_GATEWAY_ALLOWLIST": self.settings.SQL_GATEWAY_ALLOWLIST,
            "SQL_GATEWAY_DB_ENGINE_MAP": self.settings.SQL_GATEWAY_DB_ENGINE_MAP,
            "SQL_GATEWAY_MAX_COLUMNS": self.settings.SQL_GATEWAY_MAX_COLUMNS,
            "SQL_GATEWAY_MAX_FILTERS": self.settings.SQL_GATEWAY_MAX_FILTERS,
            "SQL_GATEWAY_MAX_IN_LIST": self.settings.SQL_GATEWAY_MAX_IN_LIST,
            "SQL_GATEWAY_MAX_ORDER_BY": self.settings.SQL_GATEWAY_MAX_ORDER_BY,
            "SQL_GATEWAY_MAX_GROUP_BY": self.settings.SQL_GATEWAY_MAX_GROUP_BY,
            "SQL_GATEWAY_DEFAULT_LIMIT": self.settings.SQL_GATEWAY_DEFAULT_LIMIT,
            "SQL_GATEWAY_MAX_LIMIT": self.settings.SQL_GATEWAY_MAX_LIMIT,
            "SQL_GATEWAY_ENABLE_TOTAL_COUNT": self.settings.SQL_GATEWAY_ENABLE_TOTAL_COUNT,
            "SQL_GATEWAY_MAX_WRITE_ROWS_DEFAULT": self.settings.SQL_GATEWAY_MAX_WRITE_ROWS_DEFAULT,
            "SQL_GATEWAY_MAX_BULK_INSERT_ROWS": self.settings.SQL_GATEWAY_MAX_BULK_INSERT_ROWS,
            "SQL_GATEWAY_MAX_BODY_BYTES": self.settings.SQL_GATEWAY_MAX_BODY_BYTES,
        }
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
            Column("venue", String(255)),
            Column("city", String(255)),
            Column("status", Integer),
        )
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(venue.insert(), [{"venue": "A", "city": "Pune", "status": 1}])

        engines.clear()
        engines["default"] = engine

        self.settings.SQL_GATEWAY_ALLOWLIST = {
            "venue": {
                "db": "STORE",
                "table_kind": "table",
                "operations": ["select", "insert", "update", "delete"],
                "select_columns": ["id", "venue", "city", "status"],
                "filter_columns": ["id", "city", "status"],
                "group_columns": ["city", "status"],
                "order_columns": ["id", "city"],
                "insert_columns": ["venue", "city", "status"],
                "update_columns": ["status"],
                "max_write_rows": 100,
                "allow_explicit_pk_insert": False,
            }
        }
        self.settings.SQL_GATEWAY_DB_ENGINE_MAP = {"STORE": "default"}
        self.settings.SQL_GATEWAY_MAX_COLUMNS = 2
        self.settings.SQL_GATEWAY_MAX_FILTERS = 1
        self.settings.SQL_GATEWAY_MAX_IN_LIST = 2
        self.settings.SQL_GATEWAY_MAX_ORDER_BY = 1
        self.settings.SQL_GATEWAY_MAX_GROUP_BY = 1
        self.settings.SQL_GATEWAY_DEFAULT_LIMIT = 100
        self.settings.SQL_GATEWAY_MAX_LIMIT = 1000
        self.settings.SQL_GATEWAY_ENABLE_TOTAL_COUNT = True
        self.settings.SQL_GATEWAY_MAX_WRITE_ROWS_DEFAULT = 100
        self.settings.SQL_GATEWAY_MAX_BULK_INSERT_ROWS = 2
        self.settings.SQL_GATEWAY_MAX_BODY_BYTES = 20

        clear_metadata_cache()

    def tearDown(self):
        for key, value in self._original.items():
            setattr(self.settings, key, value)
        engines.clear()
        engines.update(self._original_engines)
        clear_metadata_cache()

    def _assert_limit_error(self, payload):
        with self.assertRaises(SQLGatewayError) as ctx:
            execute_gateway_request(parse_gateway_payload(payload))
        self.assertEqual("SQLGW_COMPLEXITY_LIMIT_EXCEEDED", ctx.exception.code)

    def test_columns_limit_enforced(self):
        payload = {
            "operation": "select",
            "table": "venue",
            "columns": ["id", "city", "status"],
        }
        self._assert_limit_error(payload)

    def test_filters_limit_enforced(self):
        payload = {
            "operation": "select",
            "table": "venue",
            "columns": ["id"],
            "filters": [
                {"column": "id", "op": "eq", "value": 1},
                {"column": "status", "op": "eq", "value": 1},
            ],
        }
        self._assert_limit_error(payload)

    def test_in_list_limit_enforced(self):
        payload = {
            "operation": "select",
            "table": "venue",
            "columns": ["id"],
            "filters": [
                {"column": "id", "op": "in", "value": [1, 2, 3]},
            ],
        }
        self._assert_limit_error(payload)

    def test_bulk_insert_rows_limit_enforced(self):
        payload = {
            "operation": "insert",
            "table": "venue",
            "rows": [
                {"venue": "A", "city": "Pune", "status": 1},
                {"venue": "B", "city": "Pune", "status": 1},
                {"venue": "C", "city": "Pune", "status": 1},
            ],
        }
        self._assert_limit_error(payload)

    def test_body_size_limit_enforced_on_route(self):
        if not _testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        with patch("controllers.api.query_gateway.validate_token", return_value={"sub": "1", "typ": "access"}):
            client = TestClient(main.app)
            try:
                response = client.post(
                    "/api/query/gateway",
                    headers={"Authorization": "Bearer ok", "Content-Type": "application/json"},
                    data='{"operation":"select","table":"venue","columns":["id"]}',
                )
            finally:
                client.close()

        self.assertEqual(400, response.status_code)
        self.assertEqual("SQLGW_COMPLEXITY_LIMIT_EXCEEDED", response.json()["error"])


if __name__ == "__main__":
    unittest.main()
