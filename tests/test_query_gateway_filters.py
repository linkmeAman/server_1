"""Filter/operator validation tests for SQL gateway."""

import unittest

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine
from sqlalchemy.pool import StaticPool

from app.core.database import engines
from app.core.settings import get_settings
from app.core.sql_gateway import SQLGatewayError, clear_metadata_cache, execute_gateway_request, parse_gateway_payload


class TestQueryGatewayFilters(unittest.TestCase):
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
            Column("created_at", DateTime),
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
                "select_columns": ["id", "venue", "city", "status", "created_at"],
                "filter_columns": ["id", "city", "status", "created_at"],
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
        self.settings.SQL_GATEWAY_MAX_IN_LIST = 3
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

    def _execute(self, filters):
        payload = {
            "operation": "select",
            "table": "venue",
            "columns": ["id"],
            "filters": filters,
        }
        return execute_gateway_request(parse_gateway_payload(payload))

    def _assert_invalid_operator(self, filters):
        with self.assertRaises(SQLGatewayError) as ctx:
            self._execute(filters)
        self.assertEqual("SQLGW_INVALID_OPERATOR_PAYLOAD", ctx.exception.code)

    def test_between_requires_exactly_two_values(self):
        self._assert_invalid_operator([
            {"column": "id", "op": "between", "value": [1]},
        ])

    def test_in_requires_non_empty_list(self):
        self._assert_invalid_operator([
            {"column": "id", "op": "in", "value": []},
        ])

    def test_is_null_rejects_value(self):
        self._assert_invalid_operator([
            {"column": "city", "op": "is_null", "value": "x"},
        ])

    def test_like_requires_string(self):
        self._assert_invalid_operator([
            {"column": "city", "op": "like", "value": 123},
        ])

    def test_numeric_comparison_rejects_non_numeric(self):
        self._assert_invalid_operator([
            {"column": "status", "op": "gt", "value": "abc"},
        ])


if __name__ == "__main__":
    unittest.main()
