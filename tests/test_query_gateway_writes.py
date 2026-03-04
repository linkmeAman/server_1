"""Write operation safety tests for SQL gateway."""

import unittest

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine
from sqlalchemy.pool import StaticPool

from core.database import engines
from core.settings import get_settings
from core.sql_gateway import SQLGatewayError, clear_metadata_cache, execute_gateway_request, parse_gateway_payload


class TestQueryGatewayWrites(unittest.TestCase):
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
            Column("park", Integer, default=0),
            Column("updated_by", String(64)),
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
                "select_columns": ["id", "venue", "city", "status", "park", "updated_by"],
                "filter_columns": ["id", "city", "status", "park"],
                "group_columns": ["city", "status", "park"],
                "order_columns": ["id", "city"],
                "insert_columns": ["venue", "city", "status"],
                "update_columns": ["status", "park", "updated_by"],
                "max_write_rows": 1,
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
        self.settings.SQL_GATEWAY_MAX_WRITE_ROWS_DEFAULT = 1
        self.settings.SQL_GATEWAY_MAX_BULK_INSERT_ROWS = 500

        clear_metadata_cache()

    def tearDown(self):
        for key, value in self._original.items():
            setattr(self.settings, key, value)
        engines.clear()
        engines.update(self._original_engines)
        clear_metadata_cache()

    def test_update_requires_filters(self):
        payload = {
            "operation": "update",
            "table": "venue",
            "values": {"status": 0},
            "filters": [],
        }
        with self.assertRaises(SQLGatewayError) as ctx:
            execute_gateway_request(parse_gateway_payload(payload))
        self.assertEqual("SQLGW_INVALID_OPERATOR_PAYLOAD", ctx.exception.code)

    def test_update_rejects_empty_values(self):
        payload = {
            "operation": "update",
            "table": "venue",
            "values": {},
            "filters": [{"column": "id", "op": "eq", "value": 1}],
        }
        with self.assertRaises(SQLGatewayError) as ctx:
            execute_gateway_request(parse_gateway_payload(payload))
        self.assertEqual("SQLGW_INVALID_OPERATOR_PAYLOAD", ctx.exception.code)

    def test_update_rejects_when_write_cap_exceeded(self):
        payload = {
            "operation": "update",
            "table": "venue",
            "values": {"status": 0},
            "filters": [{"column": "city", "op": "eq", "value": "Pune"}],
        }
        with self.assertRaises(SQLGatewayError) as ctx:
            execute_gateway_request(parse_gateway_payload(payload))
        self.assertEqual("SQLGW_WRITE_LIMIT_EXCEEDED", ctx.exception.code)

    def test_delete_rejects_when_write_cap_exceeded(self):
        payload = {
            "operation": "delete",
            "table": "venue",
            "filters": [{"column": "status", "op": "eq", "value": 1}],
        }
        with self.assertRaises(SQLGatewayError) as ctx:
            execute_gateway_request(parse_gateway_payload(payload))
        self.assertEqual("SQLGW_WRITE_LIMIT_EXCEEDED", ctx.exception.code)

    def test_delete_is_soft_delete_and_stamps_updated_by(self):
        payload = {
            "operation": "delete",
            "table": "venue",
            "filters": [{"column": "id", "op": "eq", "value": 1}],
        }
        result = execute_gateway_request(parse_gateway_payload(payload), actor_user_id="123")
        self.assertEqual(1, result["affected_rows"])

        verify_payload = {
            "operation": "select",
            "table": "venue",
            "columns": ["id", "park", "updated_by"],
            "filters": [{"column": "id", "op": "eq", "value": 1}],
        }
        check = execute_gateway_request(parse_gateway_payload(verify_payload))
        self.assertEqual(1, check["returned_count"])
        self.assertEqual(1, check["rows"][0]["park"])
        self.assertEqual("123", check["rows"][0]["updated_by"])


if __name__ == "__main__":
    unittest.main()
