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
NOTIFICATION_MIGRATION = "20260526_011_notifications.py"
NOTIFICATION_FOLLOWUP_MIGRATION = "20260527_012_notification_rules_followup.py"


class TestMigrationAudit(unittest.TestCase):
    def test_no_legacy_table_shape_changes(self):
        versions_dir = Path("alembic/versions")
        forbidden_tokens = ("op.alter_column", "op.add_column", "op.drop_column", "op.drop_constraint", "op.create_foreign_key")

        for migration_name in TARGET_MIGRATIONS:
            path = versions_dir / migration_name
            if not path.exists():
                continue
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

    def test_notification_migrations_use_general_ci_and_expected_chain(self):
        versions_dir = Path("alembic/versions")
        notifications = versions_dir / NOTIFICATION_MIGRATION
        followup = versions_dir / NOTIFICATION_FOLLOWUP_MIGRATION

        self.assertTrue(notifications.exists(), f"Missing migration: {NOTIFICATION_MIGRATION}")
        self.assertTrue(followup.exists(), f"Missing migration: {NOTIFICATION_FOLLOWUP_MIGRATION}")

        notifications_content = notifications.read_text(encoding="utf-8")
        followup_content = followup.read_text(encoding="utf-8")

        self.assertIn('"mysql_collate": "utf8mb4_general_ci"', notifications_content)
        self.assertIn('"mysql_collate": "utf8mb4_general_ci"', followup_content)
        self.assertIn('revision = "20260526_011"', notifications_content)
        self.assertIn('down_revision = "20260520_010"', notifications_content)
        self.assertIn('revision = "20260527_012"', followup_content)
        self.assertIn('down_revision = "20260526_011"', followup_content)


if __name__ == "__main__":
    unittest.main()
