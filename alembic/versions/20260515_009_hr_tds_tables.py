"""HR module — TDS certificate tables.

Revision ID: 20260515_009
Revises: 20260507_008
Create Date: 2026-05-15 00:00:09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "20260515_009"
down_revision = "20260507_008"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return inspector.has_table(table_name)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # tds_upload_batch — one row per upload action (single PDF or ZIP)
    # ------------------------------------------------------------------
    if not _has_table("tds_upload_batch"):
        op.create_table(
            "tds_upload_batch",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "upload_type",
                sa.Enum("single", "zip", name="tds_upload_type"),
                nullable=False,
            ),
            sa.Column("original_filename", sa.String(512), nullable=False),
            sa.Column("total_files", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("uploaded_by", sa.Integer(), nullable=True),
            sa.Column(
                "status",
                sa.Enum(
                    "pending",
                    "uploaded",
                    "mapping_in_progress",
                    "mapped",
                    name="tds_batch_status",
                ),
                nullable=False,
                server_default="pending",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
                server_onupdate=text("CURRENT_TIMESTAMP"),
            ),
        )

    # ------------------------------------------------------------------
    # tds_document — one row per TDS PDF file
    # ------------------------------------------------------------------
    if not _has_table("tds_document"):
        op.create_table(
            "tds_document",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("batch_id", sa.Integer(), nullable=False),
            # Employee FK — NULL until mapped
            sa.Column("employee_id", sa.Integer(), nullable=True),
            sa.Column("s3_key", sa.String(512), nullable=False),
            sa.Column("original_filename", sa.String(512), nullable=False),
            # Fields parsed from filename (e.g. VIDHI JIGNESH PARMAR_Q2_FY202526_16A.pdf)
            sa.Column("parsed_name", sa.String(255), nullable=True),
            sa.Column("quarter", sa.TinyInteger(), nullable=True),   # 1–4
            sa.Column("fiscal_year", sa.String(10), nullable=True),  # e.g. "2025-26"
            sa.Column(
                "mapping_status",
                sa.Enum(
                    "unmapped",
                    "auto_mapped",
                    "manual_mapped",
                    "failed",
                    name="tds_mapping_status",
                ),
                nullable=False,
                server_default="unmapped",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
                server_onupdate=text("CURRENT_TIMESTAMP"),
            ),
            sa.ForeignKeyConstraint(
                ["batch_id"],
                ["tds_upload_batch.id"],
                name="fk_tds_document_batch",
                ondelete="CASCADE",
            ),
        )
        op.create_index("ix_tds_document_employee_id", "tds_document", ["employee_id"])
        op.create_index("ix_tds_document_batch_id", "tds_document", ["batch_id"])
        op.create_index(
            "ix_tds_document_fiscal_quarter",
            "tds_document",
            ["fiscal_year", "quarter"],
        )


def downgrade() -> None:
    if _has_table("tds_document"):
        op.drop_table("tds_document")
    if _has_table("tds_upload_batch"):
        op.drop_table("tds_upload_batch")
