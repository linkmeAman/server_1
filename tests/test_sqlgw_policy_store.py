"""Policy store lifecycle and validation tests."""

import unittest

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine
from sqlalchemy.pool import StaticPool

from app.core.database import engines
from app.core.settings import get_settings
from app.core.sqlgw_policy_store import (
    SQLGWPolicyError,
    activate_policy,
    approve_policy,
    clear_policy_cache,
    create_policy_draft,
    get_active_policy,
    list_policy_versions,
)
from app.core.sqlgw_schema import clear_schema_cache


class TestSQLGWPolicyStore(unittest.TestCase):
    def setUp(self):
        self.settings = get_settings()
        self._original_db_map = self.settings.SQL_GATEWAY_DB_ENGINE_MAP
        self._original_write_cap = self.settings.SQL_GATEWAY_MAX_WRITE_ROWS_DEFAULT
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

        engines.clear()
        engines["default"] = engine
        engines["central"] = engine

        self.settings.SQL_GATEWAY_DB_ENGINE_MAP = {"STORE": "default", "CENTRAL": "central"}
        self.settings.SQL_GATEWAY_MAX_WRITE_ROWS_DEFAULT = 100

        clear_policy_cache()
        clear_schema_cache()

    def tearDown(self):
        self.settings.SQL_GATEWAY_DB_ENGINE_MAP = self._original_db_map
        self.settings.SQL_GATEWAY_MAX_WRITE_ROWS_DEFAULT = self._original_write_cap
        engines.clear()
        engines.update(self._original_engines)
        clear_policy_cache()
        clear_schema_cache()

    def _policy(self, status_column: str = "status"):
        return {
            "venue": {
                "db": "STORE",
                "table_kind": "table",
                "operations": ["select", "insert", "update", "delete"],
                "select_columns": ["id", "city", status_column],
                "filter_columns": ["id", "city", status_column],
                "group_columns": ["city", status_column],
                "order_columns": ["id", "city"],
                "insert_columns": ["city", status_column],
                "update_columns": [status_column],
                "max_write_rows": 100,
                "allow_explicit_pk_insert": False,
            }
        }

    def test_create_approve_activate_and_single_active_invariant(self):
        p1 = create_policy_draft(self._policy(), created_by="1", validate_schema=True)
        self.assertEqual("draft", p1["status"])
        self.assertEqual("1", p1["created_by"])
        self.assertEqual("1", p1["updated_by"])
        self.assertIsNotNone(p1["created_at"])
        self.assertIsNotNone(p1["updated_at"])

        p1_approved = approve_policy(p1["id"], approved_by="2")
        self.assertEqual("approved", p1_approved["status"])
        self.assertEqual("2", p1_approved["approved_by"])
        self.assertEqual("2", p1_approved["updated_by"])
        self.assertIsNotNone(p1_approved["approved_at"])
        self.assertIsNotNone(p1_approved["updated_at"])

        p1_active = activate_policy(p1["id"], activated_by="2")
        self.assertEqual("active", p1_active["status"])
        self.assertEqual("2", p1_active["activated_by"])
        self.assertEqual("2", p1_active["updated_by"])
        self.assertIsNotNone(p1_active["activated_at"])
        self.assertIsNotNone(p1_active["updated_at"])

        p2 = create_policy_draft(self._policy(), created_by="1", validate_schema=True)
        approve_policy(p2["id"], approved_by="2")
        p2_active = activate_policy(p2["id"], activated_by="2")
        self.assertEqual("active", p2_active["status"])

        all_versions = list_policy_versions(limit=10)
        status_by_version = {x["version"]: x["status"] for x in all_versions}
        self.assertEqual("archived", status_by_version[p1["version"]])
        self.assertEqual("active", status_by_version[p2["version"]])

        current_active = get_active_policy()
        self.assertIsNotNone(current_active)
        self.assertEqual(p2["version"], current_active["version"])

    def test_policy_validation_rejects_invalid_identifier(self):
        bad_policy = {
            "venue-bad": {
                "db": "STORE",
                "table_kind": "table",
                "operations": ["select"],
                "select_columns": ["id"],
            }
        }

        with self.assertRaises(SQLGWPolicyError) as ctx:
            create_policy_draft(bad_policy, created_by="1", validate_schema=False)

        self.assertEqual("SQLGW_INVALID_IDENTIFIER", ctx.exception.code)

    def test_policy_validation_rejects_missing_schema_column(self):
        with self.assertRaises(SQLGWPolicyError) as ctx:
            create_policy_draft(self._policy(status_column="status_missing"), created_by="1", validate_schema=True)

        self.assertEqual("SQLGW_POLICY_INVALID", ctx.exception.code)
        self.assertIn("Missing column", ctx.exception.message)

    def test_policy_validation_rejects_missing_table(self):
        bad_policy = {
            "missing_table": {
                "db": "STORE",
                "table_kind": "table",
                "operations": ["select"],
                "select_columns": ["id"],
                "filter_columns": ["id"],
                "group_columns": [],
                "order_columns": ["id"],
                "insert_columns": [],
                "update_columns": [],
            }
        }

        with self.assertRaises(SQLGWPolicyError) as ctx:
            create_policy_draft(bad_policy, created_by="1", validate_schema=True)

        self.assertEqual("SQLGW_POLICY_INVALID", ctx.exception.code)
        self.assertIn("Missing table", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
