"""create auth_employee_user_map

Revision ID: 20260302_002
Revises: 20260302_001
Create Date: 2026-03-02 00:00:02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260302_002"
down_revision = "20260302_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_employee_user_map",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("contact_id", sa.BigInteger(), nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
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
        sa.UniqueConstraint("employee_id", name="uq_auth_employee_user_map_employee_id"),
        sa.UniqueConstraint("user_id", name="uq_auth_employee_user_map_user_id"),
    )
    op.create_index(
        "ix_auth_employee_user_map_contact_active",
        "auth_employee_user_map",
        ["contact_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_auth_employee_user_map_employee_active",
        "auth_employee_user_map",
        ["employee_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_auth_employee_user_map_user_active",
        "auth_employee_user_map",
        ["user_id", "is_active"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_auth_employee_user_map_user_active", table_name="auth_employee_user_map")
    op.drop_index("ix_auth_employee_user_map_employee_active", table_name="auth_employee_user_map")
    op.drop_index("ix_auth_employee_user_map_contact_active", table_name="auth_employee_user_map")
    op.drop_table("auth_employee_user_map")
