"""Select semantics tests for SQL gateway."""

import unittest

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine
from sqlalchemy.pool import StaticPool

from app.core.database import engines
from app.core.settings import get_settings
from app.core.sql_gateway import SQLGatewayError, clear_metadata_cache, execute_gateway_request, parse_gateway_payload


class TestQueryGatewaySelectSemantics(unittest.TestCase):
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
            conn.execute(
                venue.insert(),
                [
                    {"venue": "A", "city": "Pune", "status": 1},
                    {"venue": "B", "city": "Pune", "status": 1},
                    {"venue": "C", "city": "Delhi", "status": 1},
                ],
            )

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

    def test_include_total_false_omits_total_count(self):
        payload = {
            "operation": "select",
            "table": "venue",
            "columns": ["id", "city"],
            "include_total": False,
        }
        result = execute_gateway_request(parse_gateway_payload(payload))
        self.assertNotIn("total_count", result)

    def test_include_total_true_returns_filtered_count(self):
        payload = {
            "operation": "select",
            "table": "venue",
            "columns": ["id", "city"],
            "filters": [{"column": "city", "op": "eq", "value": "Pune"}],
            "include_total": True,
        }
        result = execute_gateway_request(parse_gateway_payload(payload))
        self.assertEqual(2, result["total_count"])
        self.assertEqual(2, result["returned_count"])

    def test_grouped_total_count_counts_groups(self):
        payload = {
            "operation": "select",
            "table": "venue",
            "columns": ["city"],
            "group_by": ["city"],
            "aggregates": [{"func": "count", "column": "id", "alias": "cnt"}],
            "include_total": True,
        }
        result = execute_gateway_request(parse_gateway_payload(payload))
        self.assertEqual(2, result["total_count"])
        self.assertEqual(2, result["returned_count"])

    def test_offset_without_order_by_rejected(self):
        payload = {
            "operation": "select",
            "table": "venue",
            "columns": ["id"],
            "offset": 1,
        }
        with self.assertRaises(SQLGatewayError) as ctx:
            execute_gateway_request(parse_gateway_payload(payload))
        self.assertEqual("SQLGW_DETERMINISTIC_ORDER_REQUIRED", ctx.exception.code)


if __name__ == "__main__":
    unittest.main()
