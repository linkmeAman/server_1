"""Add notification delivery rules and follow-up dispatch ledger

Revision ID: 20260527_012
Revises: 20260526_011
Create Date: 2026-05-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import make_url
from sqlalchemy import inspect

revision = "20260527_012"
down_revision = "20260526_011"
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
    if not _table_exists("notification_delivery_rule", db):
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
            sa.Column("reminder_offsets_json", mysql.LONGTEXT(), nullable=False),
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
            schema=db,
            **MYSQL_TABLE_OPTIONS,
        )
    if not _index_exists("notification_delivery_rule", "idx_notification_delivery_rule_event", db):
        op.create_index(
            "idx_notification_delivery_rule_event",
            "notification_delivery_rule",
            ["source", "event_type", "enabled"],
            schema=db,
        )

    if not _table_exists("notification_dispatch_ledger", db):
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
            schema=db,
            **MYSQL_TABLE_OPTIONS,
        )
    if not _index_exists("notification_dispatch_ledger", "idx_notification_dispatch_ledger_event", db):
        op.create_index(
            "idx_notification_dispatch_ledger_event",
            "notification_dispatch_ledger",
            ["event_id"],
            schema=db,
        )


def downgrade() -> None:
    db = _main_db()
    if _index_exists("notification_dispatch_ledger", "idx_notification_dispatch_ledger_event", db):
        op.drop_index(
            "idx_notification_dispatch_ledger_event",
            table_name="notification_dispatch_ledger",
            schema=db,
        )
    if _table_exists("notification_dispatch_ledger", db):
        op.drop_table("notification_dispatch_ledger", schema=db)
    if _index_exists("notification_delivery_rule", "idx_notification_delivery_rule_event", db):
        op.drop_index(
            "idx_notification_delivery_rule_event",
            table_name="notification_delivery_rule",
            schema=db,
        )
    if _table_exists("notification_delivery_rule", db):
        op.drop_table("notification_delivery_rule", schema=db)
