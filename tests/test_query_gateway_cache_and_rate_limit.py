"""Metadata cache and route rate-limit tests for SQL gateway."""

import unittest
from queue import Queue
from threading import Thread
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine
from sqlalchemy.pool import StaticPool

import main
from controllers.api.query_gateway import reset_query_gateway_rate_limiter
from core.database import engines
from core.settings import get_settings
from core.sql_gateway import (
    clear_metadata_cache,
    execute_gateway_request,
    metadata_cache_size,
    parse_gateway_payload,
)


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


class TestQueryGatewayCacheAndRateLimit(unittest.TestCase):
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
            "SQL_GATEWAY_RATE_LIMIT_PER_MINUTE": self.settings.SQL_GATEWAY_RATE_LIMIT_PER_MINUTE,
            "SQL_GATEWAY_MAX_BODY_BYTES": self.settings.SQL_GATEWAY_MAX_BODY_BYTES,
        }
        self._original_engines = dict(engines)

        self.engine = create_engine(
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
        metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            conn.execute(venue.insert(), [{"venue": "A", "city": "Pune", "status": 1}])

        engines.clear()
        engines["default"] = self.engine

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
        self.settings.SQL_GATEWAY_MAX_COLUMNS = 50
        self.settings.SQL_GATEWAY_MAX_FILTERS = 25
        self.settings.SQL_GATEWAY_MAX_IN_LIST = 200
        self.settings.SQL_GATEWAY_MAX_ORDER_BY = 5
        self.settings.SQL_GATEWAY_MAX_GROUP_BY = 10
        self.settings.SQL_GATEWAY_DEFAULT_LIMIT = 100
        self.settings.SQL_GATEWAY_MAX_LIMIT = 1000
        self.settings.SQL_GATEWAY_ENABLE_TOTAL_COUNT = True
        self.settings.SQL_GATEWAY_MAX_WRITE_ROWS_DEFAULT = 100
        self.settings.SQL_GATEWAY_MAX_BULK_INSERT_ROWS = 500
        self.settings.SQL_GATEWAY_RATE_LIMIT_PER_MINUTE = 1
        self.settings.SQL_GATEWAY_MAX_BODY_BYTES = 1024 * 1024

        clear_metadata_cache()
        reset_query_gateway_rate_limiter()

    def tearDown(self):
        for key, value in self._original.items():
            setattr(self.settings, key, value)
        engines.clear()
        engines.update(self._original_engines)
        clear_metadata_cache()
        reset_query_gateway_rate_limiter()

    def test_reflection_metadata_cache_is_reused(self):
        payload = {
            "operation": "select",
            "table": "venue",
            "columns": ["id"],
        }

        execute_gateway_request(parse_gateway_payload(payload))
        execute_gateway_request(parse_gateway_payload(payload))

        self.assertEqual(1, metadata_cache_size())

    def test_rate_limit_returns_429(self):
        if not _testclient_requests_work():
            self.skipTest("TestClient request execution is not responsive in this runtime")

        payload = {
            "operation": "select",
            "table": "venue",
            "columns": ["id"],
        }

        with patch("controllers.api.query_gateway.validate_token", return_value={"sub": "1", "typ": "access"}):
            client = TestClient(main.app)
            try:
                first = client.post(
                    "/api/query/gateway",
                    headers={"Authorization": "Bearer valid"},
                    json=payload,
                )
                second = client.post(
                    "/api/query/gateway",
                    headers={"Authorization": "Bearer valid"},
                    json=payload,
                )
            finally:
                client.close()

        self.assertEqual(200, first.status_code)
        self.assertEqual(429, second.status_code)
        self.assertEqual("SQLGW_RATE_LIMITED", second.json()["error"])


if __name__ == "__main__":
    unittest.main()
