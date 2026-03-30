"""Insert behavior tests for SQL gateway."""

import unittest

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, select
from sqlalchemy.pool import StaticPool

from app.core.database import engines
from app.core.settings import get_settings
from app.core.sql_gateway import SQLGatewayError, clear_metadata_cache, execute_gateway_request, parse_gateway_payload


class TestQueryGatewayInserts(unittest.TestCase):
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

        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        metadata = MetaData()
        self.venue = Table(
            "venue",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("venue", String(255)),
            Column("city", String(255)),
            Column("status", Integer),
            Column("park", Integer),
            Column("created_by", String(64)),
            Column("updated_by", String(64)),
        )
        metadata.create_all(self.engine)

        engines.clear()
        engines["default"] = self.engine

        self.settings.SQL_GATEWAY_ALLOWLIST = {
            "venue": {
                "db": "STORE",
                "table_kind": "table",
                "operations": ["select", "insert", "update", "delete"],
                "select_columns": ["id", "venue", "city", "status", "park", "created_by", "updated_by"],
                "filter_columns": ["id", "city", "status", "park"],
                "group_columns": ["city", "status", "park"],
                "order_columns": ["id", "city"],
                "insert_columns": ["venue", "city", "status", "park", "created_by", "updated_by"],
                "update_columns": ["status", "park", "updated_by"],
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

    def test_insert_rejects_unknown_keys(self):
        payload = {
            "operation": "insert",
            "table": "venue",
            "rows": {"venue": "A", "city": "Pune", "status": 1, "unknown": "x"},
        }
        with self.assertRaises(SQLGatewayError) as ctx:
            execute_gateway_request(parse_gateway_payload(payload))
        self.assertEqual("SQLGW_FORBIDDEN_COLUMN", ctx.exception.code)

    def test_insert_rejects_explicit_pk_by_default(self):
        payload = {
            "operation": "insert",
            "table": "venue",
            "rows": {"id": 99, "venue": "A", "city": "Pune", "status": 1},
        }
        with self.assertRaises(SQLGatewayError) as ctx:
            execute_gateway_request(parse_gateway_payload(payload))
        self.assertEqual("SQLGW_FORBIDDEN_COLUMN", ctx.exception.code)

    def test_insert_returns_inserted_primary_keys_field(self):
        payload = {
            "operation": "insert",
            "table": "venue",
            "rows": {"venue": "A", "city": "Pune", "status": 1},
        }
        result = execute_gateway_request(parse_gateway_payload(payload))

        self.assertIn("inserted_primary_keys", result)
        self.assertIsInstance(result["inserted_primary_keys"], list)
        self.assertEqual(1, result["affected_rows"])

        with self.engine.connect() as conn:
            rows = conn.execute(select(self.venue.c.id)).all()
        self.assertEqual(1, len(rows))

    def test_insert_stamps_actor_user_id_when_audit_columns_exist(self):
        payload = {
            "operation": "insert",
            "table": "venue",
            "rows": {"venue": "A", "city": "Pune", "status": 1},
        }
        execute_gateway_request(parse_gateway_payload(payload), actor_user_id="77")

        with self.engine.connect() as conn:
            row = conn.execute(select(self.venue.c.created_by, self.venue.c.updated_by)).first()
        self.assertEqual("77", row[0])
        self.assertEqual("77", row[1])

    def test_insert_defaults_park_to_zero(self):
        payload = {
            "operation": "insert",
            "table": "venue",
            "rows": {"venue": "A", "city": "Pune", "status": 1},
        }
        execute_gateway_request(parse_gateway_payload(payload))

        with self.engine.connect() as conn:
            row = conn.execute(select(self.venue.c.park)).first()
        self.assertEqual(0, row[0])


if __name__ == "__main__":
    unittest.main()
