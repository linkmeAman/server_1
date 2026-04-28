"""Report platform metadata tables.

Revision ID: 20260428_007
Revises: 20260324_006
Create Date: 2026-04-28 00:00:07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "20260428_007"
down_revision = "20260324_006"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return inspector.has_table(table_name)


def upgrade() -> None:
    if not _has_table("report_definitions"):
        op.create_table(
            "report_definitions",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("slug", sa.String(length=128), nullable=False),
            sa.Column("name", sa.String(length=191), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("category", sa.String(length=128), nullable=False, server_default="Reports"),
            sa.Column(
                "kind",
                sa.Enum("table", "route", name="report_definition_kind"),
                nullable=False,
                server_default="table",
            ),
            sa.Column(
                "status",
                sa.Enum("draft", "published", "archived", name="report_definition_status"),
                nullable=False,
                server_default="draft",
            ),
            sa.Column("prism_resource_code", sa.String(length=191), nullable=False),
            sa.Column("source_legacy_report_id", sa.Integer(), nullable=True),
            sa.Column("route_path", sa.String(length=512), nullable=True),
            sa.Column("active_version_id", sa.BigInteger(), nullable=True),
            sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
            sa.Column("modified_by_user_id", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
            sa.Column(
                "modified_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
                server_onupdate=text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint("slug", name="uq_report_definitions_slug"),
            sa.Index("ix_report_definitions_status", "status"),
            sa.Index("ix_report_definitions_prism_resource", "prism_resource_code"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    if not _has_table("report_versions"):
        op.create_table(
            "report_versions",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("report_id", sa.BigInteger(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("definition_json", sa.Text(length=2**24), nullable=False),
            sa.Column(
                "status",
                sa.Enum("draft", "published", "archived", name="report_version_status"),
                nullable=False,
                server_default="draft",
            ),
            sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("report_id", "version", name="uq_report_versions_report_version"),
            sa.Index("ix_report_versions_report_id", "report_id"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    if not _has_table("report_run_logs"):
        op.create_table(
            "report_run_logs",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("report_slug", sa.String(length=128), nullable=False),
            sa.Column("report_version", sa.Integer(), nullable=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("employee_id", sa.BigInteger(), nullable=True),
            sa.Column("action", sa.String(length=64), nullable=False),
            sa.Column("request_json", sa.Text(), nullable=True),
            sa.Column("result_count", sa.Integer(), nullable=False, server_default=text("0")),
            sa.Column(
                "status",
                sa.Enum("success", "error", name="report_run_status"),
                nullable=False,
                server_default="success",
            ),
            sa.Column("error_code", sa.String(length=128), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=False, server_default=text("0")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
            sa.Index("ix_report_run_logs_report_created", "report_slug", "created_at"),
            sa.Index("ix_report_run_logs_user_created", "user_id", "created_at"),
            sa.Index("ix_report_run_logs_request_id", "request_id"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )


def downgrade() -> None:
    for table_name in ["report_run_logs", "report_versions", "report_definitions"]:
        if _has_table(table_name):
            op.drop_table(table_name)

