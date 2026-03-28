"""Identifier validation tests for SQL gateway."""

import unittest

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine
from sqlalchemy.pool import StaticPool

from core.database import engines
from core.settings import get_settings
from core.sql_gateway import SQLGatewayError, clear_metadata_cache, execute_gateway_request, parse_gateway_payload


class TestQueryGatewayIdentifiers(unittest.TestCase):
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

        clear_metadata_cache()

    def tearDown(self):
        for key, value in self._original.items():
            setattr(self.settings, key, value)
        engines.clear()
        engines.update(self._original_engines)
        clear_metadata_cache()

    def test_rejects_schema_qualified_table_name(self):
        payload = {
            "operation": "select",
            "table": "venue.bad",
            "columns": ["id"],
        }
        request_model = parse_gateway_payload(payload)

        with self.assertRaises(SQLGatewayError) as ctx:
            execute_gateway_request(request_model)

        self.assertEqual("SQLGW_INVALID_IDENTIFIER", ctx.exception.code)
        self.assertEqual(400, ctx.exception.status_code)

    def test_rejects_wildcard_column(self):
        payload = {
            "operation": "select",
            "table": "venue",
            "columns": ["*"],
        }
        request_model = parse_gateway_payload(payload)

        with self.assertRaises(SQLGatewayError) as ctx:
            execute_gateway_request(request_model)

        self.assertEqual("SQLGW_INVALID_IDENTIFIER", ctx.exception.code)

    def test_rejects_column_with_spaces(self):
        payload = {
            "operation": "select",
            "table": "venue",
            "columns": ["city name"],
        }
        request_model = parse_gateway_payload(payload)

        with self.assertRaises(SQLGatewayError) as ctx:
            execute_gateway_request(request_model)

        self.assertEqual("SQLGW_INVALID_IDENTIFIER", ctx.exception.code)


if __name__ == "__main__":
    unittest.main()
