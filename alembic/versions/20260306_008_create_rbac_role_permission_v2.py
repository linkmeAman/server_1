"""create rbac_role_permission_v2

Revision ID: 20260306_008
Revises: 20260306_007
Create Date: 2026-03-06 00:00:08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260306_008"
down_revision = "20260306_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rbac_role_permission_v2",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("can_view", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("can_add", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("can_edit", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("can_delete", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("can_super", sa.SmallInteger(), nullable=False, server_default="0"),
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
        sa.ForeignKeyConstraint(["resource_id"], ["rbac_resource_v2.id"]),
        sa.ForeignKeyConstraint(["role_id"], ["rbac_role.id"]),
        sa.UniqueConstraint("role_id", "resource_id", name="uq_rbac_role_permission_v2_role_resource"),
    )
    op.create_index(
        "ix_rbac_role_permission_v2_role_active",
        "rbac_role_permission_v2",
        ["role_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_rbac_role_permission_v2_resource_active",
        "rbac_role_permission_v2",
        ["resource_id", "is_active"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_rbac_role_permission_v2_resource_active", table_name="rbac_role_permission_v2")
    op.drop_index("ix_rbac_role_permission_v2_role_active", table_name="rbac_role_permission_v2")
    op.drop_table("rbac_role_permission_v2")

