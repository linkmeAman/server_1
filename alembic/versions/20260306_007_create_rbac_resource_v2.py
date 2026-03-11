"""create rbac_resource_v2

Revision ID: 20260306_007
Revises: 20260302_006
Create Date: 2026-03-06 00:00:07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260306_007"
down_revision = "20260302_006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rbac_resource_v2",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=191), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("meta", sa.JSON(), nullable=True),
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
        sa.Column("created_by_employee_id", sa.BigInteger(), nullable=True),
        sa.Column("modified_by_employee_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["parent_id"], ["rbac_resource_v2.id"]),
        sa.UniqueConstraint("code", name="uq_rbac_resource_v2_code"),
    )
    op.create_index(
        "ix_rbac_resource_v2_parent_active",
        "rbac_resource_v2",
        ["parent_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_rbac_resource_v2_sort_name",
        "rbac_resource_v2",
        ["sort_order", "name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_rbac_resource_v2_sort_name", table_name="rbac_resource_v2")
    op.drop_index("ix_rbac_resource_v2_parent_active", table_name="rbac_resource_v2")
    op.drop_table("rbac_resource_v2")

