"""create rbac_employee_role

Revision ID: 20260302_004
Revises: 20260302_003
Create Date: 2026-03-02 00:00:04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260302_004"
down_revision = "20260302_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rbac_employee_role",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
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
        sa.ForeignKeyConstraint(["role_id"], ["rbac_role.id"]),
        sa.UniqueConstraint("employee_id", "role_id", name="uq_rbac_employee_role_employee_role"),
    )
    op.create_index(
        "ix_rbac_employee_role_employee_active",
        "rbac_employee_role",
        ["employee_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_rbac_employee_role_role_active",
        "rbac_employee_role",
        ["role_id", "is_active"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_rbac_employee_role_role_active", table_name="rbac_employee_role")
    op.drop_index("ix_rbac_employee_role_employee_active", table_name="rbac_employee_role")
    op.drop_table("rbac_employee_role")
