"""add password hash columns to legacy user table

Revision ID: 20260318_003
Revises: 20260318_002
Create Date: 2026-03-18 00:00:03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260318_003"
down_revision = "20260318_002"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    cols = _column_names("user")

    if "password_hash" not in cols:
        op.add_column(
            "user",
            sa.Column("password_hash", sa.String(length=255), nullable=True),
        )

    if "password_hash_algo" not in cols:
        op.add_column(
            "user",
            sa.Column(
                "password_hash_algo",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'bcrypt'"),
            ),
        )

    if "password_hash_updated_at" not in cols:
        op.add_column(
            "user",
            sa.Column("password_hash_updated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    cols = _column_names("user")

    if "password_hash_updated_at" in cols:
        op.drop_column("user", "password_hash_updated_at")

    if "password_hash_algo" in cols:
        op.drop_column("user", "password_hash_algo")

    if "password_hash" in cols:
        op.drop_column("user", "password_hash")
