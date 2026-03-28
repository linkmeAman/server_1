"""Gateway allowlist loading tests for DB policy source."""

import unittest

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine
from sqlalchemy.pool import StaticPool

from core.database import engines
from core.settings import get_settings
from core.sql_gateway import SQLGatewayError, clear_metadata_cache, execute_gateway_request, parse_gateway_payload
from core.sqlgw_policy_store import (
    activate_policy,
    approve_policy,
    clear_policy_cache,
    create_policy_draft,
)
from core.sqlgw_schema import clear_schema_cache


class TestSQLGWGatewayPolicyLoading(unittest.TestCase):
    def setUp(self):
        self.settings = get_settings()
        self._original = {
            "SQL_GATEWAY_ALLOWLIST": self.settings.SQL_GATEWAY_ALLOWLIST,
            "SQL_GATEWAY_ALLOWLIST_SOURCE": self.settings.SQL_GATEWAY_ALLOWLIST_SOURCE,
            "SQL_GATEWAY_ALLOWLIST_PATH": self.settings.SQL_GATEWAY_ALLOWLIST_PATH,
            "SQL_GATEWAY_DB_ENGINE_MAP": self.settings.SQL_GATEWAY_DB_ENGINE_MAP,
            "SQL_GATEWAY_POLICY_CACHE_TTL_SECONDS": self.settings.SQL_GATEWAY_POLICY_CACHE_TTL_SECONDS,
            "SQL_GATEWAY_DEFAULT_LIMIT": self.settings.SQL_GATEWAY_DEFAULT_LIMIT,
            "SQL_GATEWAY_MAX_LIMIT": self.settings.SQL_GATEWAY_MAX_LIMIT,
            "SQL_GATEWAY_MAX_COLUMNS": self.settings.SQL_GATEWAY_MAX_COLUMNS,
            "SQL_GATEWAY_MAX_FILTERS": self.settings.SQL_GATEWAY_MAX_FILTERS,
            "SQL_GATEWAY_MAX_IN_LIST": self.settings.SQL_GATEWAY_MAX_IN_LIST,
            "SQL_GATEWAY_MAX_ORDER_BY": self.settings.SQL_GATEWAY_MAX_ORDER_BY,
            "SQL_GATEWAY_MAX_GROUP_BY": self.settings.SQL_GATEWAY_MAX_GROUP_BY,
            "SQL_GATEWAY_MAX_BULK_INSERT_ROWS": self.settings.SQL_GATEWAY_MAX_BULK_INSERT_ROWS,
            "SQL_GATEWAY_MAX_WRITE_ROWS_DEFAULT": self.settings.SQL_GATEWAY_MAX_WRITE_ROWS_DEFAULT,
            "SQL_GATEWAY_ENABLE_TOTAL_COUNT": self.settings.SQL_GATEWAY_ENABLE_TOTAL_COUNT,
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
            Column("city", String(100)),
            Column("status", Integer),
        )
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(venue.insert(), [{"city": "Pune", "status": 1}])

        engines.clear()
        engines["default"] = engine
        engines["central"] = engine

        self.settings.SQL_GATEWAY_ALLOWLIST = {}
        self.settings.SQL_GATEWAY_ALLOWLIST_SOURCE = "db"
        self.settings.SQL_GATEWAY_ALLOWLIST_PATH = ""
        self.settings.SQL_GATEWAY_DB_ENGINE_MAP = {"STORE": "default", "CENTRAL": "central"}
        self.settings.SQL_GATEWAY_POLICY_CACHE_TTL_SECONDS = 60
        self.settings.SQL_GATEWAY_DEFAULT_LIMIT = 100
        self.settings.SQL_GATEWAY_MAX_LIMIT = 1000
        self.settings.SQL_GATEWAY_MAX_COLUMNS = 50
        self.settings.SQL_GATEWAY_MAX_FILTERS = 25
        self.settings.SQL_GATEWAY_MAX_IN_LIST = 200
        self.settings.SQL_GATEWAY_MAX_ORDER_BY = 5
        self.settings.SQL_GATEWAY_MAX_GROUP_BY = 10
        self.settings.SQL_GATEWAY_MAX_BULK_INSERT_ROWS = 500
        self.settings.SQL_GATEWAY_MAX_WRITE_ROWS_DEFAULT = 100
        self.settings.SQL_GATEWAY_ENABLE_TOTAL_COUNT = True

        clear_metadata_cache()
        clear_policy_cache()
        clear_schema_cache()

    def tearDown(self):
        for key, value in self._original.items():
            setattr(self.settings, key, value)
        engines.clear()
        engines.update(self._original_engines)
        clear_metadata_cache()
        clear_policy_cache()
        clear_schema_cache()

    def _activate_policy(self):
        policy = {
            "venue": {
                "db": "STORE",
                "table_kind": "table",
                "operations": ["select"],
                "select_columns": ["id", "city", "status"],
                "filter_columns": ["id", "city", "status"],
                "group_columns": ["city", "status"],
                "order_columns": ["id", "city"],
                "insert_columns": [],
                "update_columns": [],
                "max_write_rows": 100,
                "allow_explicit_pk_insert": False,
            }
        }
        draft = create_policy_draft(policy, created_by="1", validate_schema=True)
        approve_policy(draft["id"], approved_by="2")
        activate_policy(draft["id"], activated_by="2")

    def test_gateway_loads_active_policy_from_db(self):
        self._activate_policy()

        payload = {
            "operation": "select",
            "table": "venue",
            "columns": ["id", "city", "status"],
            "include_total": True,
        }
        result = execute_gateway_request(parse_gateway_payload(payload))

        self.assertEqual(1, result["returned_count"])
        self.assertEqual(1, result["total_count"])

    def test_gateway_fails_closed_without_active_policy(self):
        payload = {
            "operation": "select",
            "table": "venue",
            "columns": ["id"],
        }

        with self.assertRaises(SQLGatewayError) as ctx:
            execute_gateway_request(parse_gateway_payload(payload))

        self.assertEqual("SQLGW_CONFIG_INVALID", ctx.exception.code)
        self.assertEqual(503, ctx.exception.status_code)


if __name__ == "__main__":
    unittest.main()
