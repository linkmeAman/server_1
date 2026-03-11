"""create rbac_role

Revision ID: 20260302_003
Revises: 20260302_002
Create Date: 2026-03-02 00:00:03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260302_003"
down_revision = "20260302_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rbac_role",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "modified_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("modified_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("code", name="uq_rbac_role_code"),
    )
    op.create_index("ix_rbac_role_is_active", "rbac_role", ["is_active"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_rbac_role_is_active", table_name="rbac_role")
    op.drop_table("rbac_role")
