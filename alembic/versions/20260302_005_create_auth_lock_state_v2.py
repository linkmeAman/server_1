"""create auth_lock_state_v2

Revision ID: 20260302_005
Revises: 20260302_004
Create Date: 2026-03-02 00:00:05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260302_005"
down_revision = "20260302_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_lock_state_v2",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("key_type", sa.String(length=32), nullable=False),
        sa.Column("country_code", sa.String(length=8), nullable=True),
        sa.Column("mobile", sa.String(length=20), nullable=True),
        sa.Column("employee_id", sa.BigInteger(), nullable=True),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("fail_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_fail_at", sa.DateTime(), nullable=True),
        sa.Column("last_fail_at", sa.DateTime(), nullable=True),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
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
        sa.UniqueConstraint("key_type", "key_hash", name="uq_auth_lock_state_v2_key_type_hash"),
    )
    op.create_index(
        "ix_auth_lock_state_v2_key_type_locked_until",
        "auth_lock_state_v2",
        ["key_type", "locked_until"],
        unique=False,
    )
    op.create_index(
        "ix_auth_lock_state_v2_cc_mobile_emp_key_type",
        "auth_lock_state_v2",
        ["country_code", "mobile", "employee_id", "key_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_auth_lock_state_v2_cc_mobile_emp_key_type", table_name="auth_lock_state_v2")
    op.drop_index("ix_auth_lock_state_v2_key_type_locked_until", table_name="auth_lock_state_v2")
    op.drop_table("auth_lock_state_v2")
