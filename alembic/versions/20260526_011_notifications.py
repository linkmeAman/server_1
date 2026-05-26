"""Add notification event log and per-user state

Revision ID: 20260526_011
Revises: 20260520_010
Create Date: 2026-05-26

Notifications are universal application events.  The event table is immutable
delivery/debug history, while user state stores per-user read and cleared
flags without deleting the underlying log.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "20260526_011"
down_revision = "20260520_010"
branch_labels = None
depends_on = None


MYSQL_TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "notification_event",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("event_timestamp", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("metadata_json", mysql.LONGTEXT(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("group_key", sa.String(length=255), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.PrimaryKeyConstraint("event_id"),
        **MYSQL_TABLE_OPTIONS,
    )
    op.create_index(
        "idx_notification_event_user_time",
        "notification_event",
        ["user_id", "created_at"],
    )
    op.create_index("idx_notification_event_request", "notification_event", ["request_id"])
    op.create_index("idx_notification_event_severity", "notification_event", ["severity"])
    op.create_index("idx_notification_event_source", "notification_event", ["source"])
    op.create_index("idx_notification_event_group", "notification_event", ["group_key"])

    op.create_table(
        "notification_user_state",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            nullable=False,
            autoincrement=True,
        ),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("read_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("cleared_at", mysql.DATETIME(fsp=6), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            "user_id",
            name="uq_notification_user_state_event_user",
        ),
        **MYSQL_TABLE_OPTIONS,
    )
    op.create_index(
        "idx_notification_user_state_user_read",
        "notification_user_state",
        ["user_id", "read_at"],
    )
    op.create_index(
        "idx_notification_user_state_user_cleared",
        "notification_user_state",
        ["user_id", "cleared_at"],
    )

    op.create_table(
        "notification_user_preference",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column(
            "toast_enabled",
            mysql.TINYINT(display_width=1),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "desktop_enabled",
            mysql.TINYINT(display_width=1),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "silent_mode",
            mysql.TINYINT(display_width=1),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "minimum_toast_severity",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'info'"),
        ),
        sa.Column(
            "minimum_desktop_severity",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'info'"),
        ),
        sa.Column(
            "center_severity_filter",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'all'"),
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
        sa.PrimaryKeyConstraint("user_id"),
        **MYSQL_TABLE_OPTIONS,
    )


def downgrade() -> None:
    op.drop_table("notification_user_preference")
    op.drop_index("idx_notification_user_state_user_cleared", table_name="notification_user_state")
    op.drop_index("idx_notification_user_state_user_read", table_name="notification_user_state")
    op.drop_table("notification_user_state")
    op.drop_index("idx_notification_event_group", table_name="notification_event")
    op.drop_index("idx_notification_event_source", table_name="notification_event")
    op.drop_index("idx_notification_event_severity", table_name="notification_event")
    op.drop_index("idx_notification_event_request", table_name="notification_event")
    op.drop_index("idx_notification_event_user_time", table_name="notification_event")
    op.drop_table("notification_event")
