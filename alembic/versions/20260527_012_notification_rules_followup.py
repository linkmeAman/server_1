"""Add notification delivery rules and follow-up dispatch ledger

Revision ID: 20260527_012
Revises: 20260526_011
Create Date: 2026-05-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "20260527_012"
down_revision = "20260526_011"
branch_labels = None
depends_on = None


MYSQL_TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "notification_delivery_rule",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column(
            "enabled",
            mysql.TINYINT(display_width=1),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "reminder_offsets_json",
            mysql.LONGTEXT(),
            nullable=False,
        ),
        sa.Column(
            "recipient_scope",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'assigned_to_me'"),
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
        ),
        sa.PrimaryKeyConstraint("user_id", "source", "event_type"),
        **MYSQL_TABLE_OPTIONS,
    )
    op.create_index(
        "idx_notification_delivery_rule_event",
        "notification_delivery_rule",
        ["source", "event_type", "enabled"],
    )

    op.create_table(
        "notification_dispatch_ledger",
        sa.Column("followup_id", sa.String(length=64), nullable=False),
        sa.Column("reminder_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("offset_minutes", sa.Integer(), nullable=False),
        sa.Column("recipient_user_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column(
            "dispatched_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.PrimaryKeyConstraint(
            "followup_id",
            "reminder_at",
            "offset_minutes",
            "recipient_user_id",
            name="pk_notification_dispatch_ledger",
        ),
        **MYSQL_TABLE_OPTIONS,
    )
    op.create_index(
        "idx_notification_dispatch_ledger_event",
        "notification_dispatch_ledger",
        ["event_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_notification_dispatch_ledger_event",
        table_name="notification_dispatch_ledger",
    )
    op.drop_table("notification_dispatch_ledger")
    op.drop_index(
        "idx_notification_delivery_rule_event",
        table_name="notification_delivery_rule",
    )
    op.drop_table("notification_delivery_rule")
