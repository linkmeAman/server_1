"""Migration audit checks for authz expansion safety."""

from __future__ import annotations

from pathlib import Path
import unittest


LEGACY_TABLES = ("employee", "contact", "user", "rbac_role", "rbac_employee_role")
TARGET_MIGRATIONS = (
    "20260306_007_create_rbac_resource_v2.py",
    "20260306_008_create_rbac_role_permission_v2.py",
    "20260306_009_create_rbac_position_department_role_v2.py",
    "20260306_010_seed_rbac_resource_v2_defaults.py",
)


class TestMigrationAudit(unittest.TestCase):
    def test_no_legacy_table_shape_changes(self):
        versions_dir = Path("alembic/versions")
        forbidden_tokens = ("op.alter_column", "op.add_column", "op.drop_column", "op.drop_constraint", "op.create_foreign_key")

        for migration_name in TARGET_MIGRATIONS:
            path = versions_dir / migration_name
            self.assertTrue(path.exists(), f"Missing migration: {migration_name}")
            content = path.read_text(encoding="utf-8")
            lowered = content.lower()
            for token in forbidden_tokens:
                if token not in lowered:
                    continue
                for table in LEGACY_TABLES:
                    self.assertNotIn(
                        table,
                        lowered,
                        f"{migration_name} contains forbidden schema change token {token} for legacy table {table}",
                    )


if __name__ == "__main__":
    unittest.main()
