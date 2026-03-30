"""View behavior tests for SQL gateway."""

import unittest

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine
from sqlalchemy.pool import StaticPool

from app.core.database import engines
from app.core.settings import get_settings
from app.core.sql_gateway import SQLGatewayError, clear_metadata_cache, execute_gateway_request, parse_gateway_payload


class TestQueryGatewayViews(unittest.TestCase):
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
        employees = Table(
            "employees",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("name", String(255)),
        )
        metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            conn.execute(employees.insert(), [{"name": "Alice"}, {"name": "Bob"}])
            conn.exec_driver_sql("CREATE VIEW emp_cont_view AS SELECT id AS emp_id, name FROM employees")

        engines.clear()
        engines["default"] = self.engine

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

    def test_select_allowed_on_view(self):
        self.settings.SQL_GATEWAY_ALLOWLIST = {
            "emp_cont_view": {
                "db": "STORE",
                "table_kind": "view",
                "operations": ["select"],
                "select_columns": ["emp_id", "name"],
                "filter_columns": ["emp_id", "name"],
                "group_columns": ["emp_id"],
                "order_columns": ["emp_id"],
            }
        }

        payload = {
            "operation": "select",
            "table": "emp_cont_view",
            "columns": ["emp_id", "name"],
            "include_total": True,
        }
        result = execute_gateway_request(parse_gateway_payload(payload))

        self.assertEqual(2, result["returned_count"])
        self.assertEqual(2, result["total_count"])

    def test_write_blocked_when_not_in_view_operations(self):
        self.settings.SQL_GATEWAY_ALLOWLIST = {
            "emp_cont_view": {
                "db": "STORE",
                "table_kind": "view",
                "operations": ["select"],
                "select_columns": ["emp_id", "name"],
                "filter_columns": ["emp_id", "name"],
                "group_columns": ["emp_id"],
                "order_columns": ["emp_id"],
                "update_columns": ["name"],
            }
        }

        payload = {
            "operation": "update",
            "table": "emp_cont_view",
            "values": {"name": "Charlie"},
            "filters": [{"column": "emp_id", "op": "eq", "value": 1}],
        }

        with self.assertRaises(SQLGatewayError) as ctx:
            execute_gateway_request(parse_gateway_payload(payload))

        self.assertEqual("SQLGW_FORBIDDEN_TABLE", ctx.exception.code)


if __name__ == "__main__":
    unittest.main()
