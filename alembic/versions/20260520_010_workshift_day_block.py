"""Add block column to workshift_day

Revision ID: 20260520_010
Revises: 20260515_009_hr_tds_tables
Create Date: 2026-05-20

NOTE: workshift_day lives in the main tenant DB (DB_NAME env var), not in
pf_central which is Alembic's default connection target.  We use raw SQL
with a schema-qualified table name so the migration runs correctly regardless
of which DB the Alembic engine is pointed at.
"""
import os
from alembic import op

# revision identifiers
revision = "20260520_010"
down_revision = "20260515_009"
branch_labels = None
depends_on = None


def _main_db() -> str:
    """Return the main tenant DB name from the environment."""
    from app.core.settings import settings
    return settings.DB_NAME


def upgrade() -> None:
    db = _main_db()
    op.execute(
        f"ALTER TABLE `{db}`.`workshift_day` "
        "ADD COLUMN `block` TINYINT(1) NOT NULL DEFAULT 0 "
        "COMMENT '1 = blocked day (employee cannot clock in/out on this day)'"
    )


def downgrade() -> None:
    db = _main_db()
    op.execute(f"ALTER TABLE `{db}`.`workshift_day` DROP COLUMN `block`")
