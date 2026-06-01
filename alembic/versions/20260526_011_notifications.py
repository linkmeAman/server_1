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
from sqlalchemy.engine import make_url
from sqlalchemy import inspect

revision = "20260526_011"
down_revision = "20260520_010"
branch_labels = None
depends_on = None


MYSQL_TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_general_ci",
}


def _main_db() -> str:
    from app.core.settings import get_settings

    settings = get_settings()
    if settings.DB_NAME:
        return settings.DB_NAME

    for attr_name in ("DATABASE_MAIN_URL", "DATABASE_URL"):
        raw_url = getattr(settings, attr_name, "")
        if raw_url:
            database = make_url(raw_url).database
            if database:
                return database

    raise RuntimeError("Main DB name is not configured for notification migrations")


def _inspector():
    return inspect(op.get_bind())


def _table_exists(table_name: str, schema: str) -> bool:
    return _inspector().has_table(table_name, schema=schema)


def _index_exists(table_name: str, index_name: str, schema: str) -> bool:
    if not _table_exists(table_name, schema):
        return False
    indexes = _inspector().get_indexes(table_name, schema=schema)
    return any(index["name"] == index_name for index in indexes)


def upgrade() -> None:
    db = _main_db()
    if not _table_exists("notification_event", db):
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
            schema=db,
            **MYSQL_TABLE_OPTIONS,
        )
    if not _index_exists("notification_event", "idx_notification_event_user_time", db):
        op.create_index("idx_notification_event_user_time", "notification_event", ["user_id", "created_at"], schema=db)
    if not _index_exists("notification_event", "idx_notification_event_request", db):
        op.create_index("idx_notification_event_request", "notification_event", ["request_id"], schema=db)
    if not _index_exists("notification_event", "idx_notification_event_severity", db):
        op.create_index("idx_notification_event_severity", "notification_event", ["severity"], schema=db)
    if not _index_exists("notification_event", "idx_notification_event_source", db):
        op.create_index("idx_notification_event_source", "notification_event", ["source"], schema=db)
    if not _index_exists("notification_event", "idx_notification_event_group", db):
        op.create_index("idx_notification_event_group", "notification_event", ["group_key"], schema=db)

    if not _table_exists("notification_user_state", db):
        op.create_table(
            "notification_user_state",
            sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False, autoincrement=True),
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
            sa.UniqueConstraint("event_id", "user_id", name="uq_notification_user_state_event_user"),
            schema=db,
            **MYSQL_TABLE_OPTIONS,
        )
    if not _index_exists("notification_user_state", "idx_notification_user_state_user_read", db):
        op.create_index("idx_notification_user_state_user_read", "notification_user_state", ["user_id", "read_at"], schema=db)
    if not _index_exists("notification_user_state", "idx_notification_user_state_user_cleared", db):
        op.create_index("idx_notification_user_state_user_cleared", "notification_user_state", ["user_id", "cleared_at"], schema=db)

    if not _table_exists("notification_user_preference", db):
        op.create_table(
            "notification_user_preference",
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column("toast_enabled", mysql.TINYINT(display_width=1), nullable=False, server_default=sa.text("1")),
            sa.Column("desktop_enabled", mysql.TINYINT(display_width=1), nullable=False, server_default=sa.text("1")),
            sa.Column("silent_mode", mysql.TINYINT(display_width=1), nullable=False, server_default=sa.text("0")),
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
            schema=db,
            **MYSQL_TABLE_OPTIONS,
        )


def downgrade() -> None:
    db = _main_db()
    if _table_exists("notification_user_preference", db):
        op.drop_table("notification_user_preference", schema=db)
    if _index_exists("notification_user_state", "idx_notification_user_state_user_cleared", db):
        op.drop_index("idx_notification_user_state_user_cleared", table_name="notification_user_state", schema=db)
    if _index_exists("notification_user_state", "idx_notification_user_state_user_read", db):
        op.drop_index("idx_notification_user_state_user_read", table_name="notification_user_state", schema=db)
    if _table_exists("notification_user_state", db):
        op.drop_table("notification_user_state", schema=db)
    if _index_exists("notification_event", "idx_notification_event_group", db):
        op.drop_index("idx_notification_event_group", table_name="notification_event", schema=db)
    if _index_exists("notification_event", "idx_notification_event_source", db):
        op.drop_index("idx_notification_event_source", table_name="notification_event", schema=db)
    if _index_exists("notification_event", "idx_notification_event_severity", db):
        op.drop_index("idx_notification_event_severity", table_name="notification_event", schema=db)
    if _index_exists("notification_event", "idx_notification_event_request", db):
        op.drop_index("idx_notification_event_request", table_name="notification_event", schema=db)
    if _index_exists("notification_event", "idx_notification_event_user_time", db):
        op.drop_index("idx_notification_event_user_time", table_name="notification_event", schema=db)
    if _table_exists("notification_event", db):
        op.drop_table("notification_event", schema=db)
