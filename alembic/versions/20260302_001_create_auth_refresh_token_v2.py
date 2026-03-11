"""create auth_refresh_token_v2

Revision ID: 20260302_001
Revises: 
Create Date: 2026-03-02 00:00:01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260302_001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_refresh_token_v2",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("contact_id", sa.BigInteger(), nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("token_jti", sa.String(length=128), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("rotated_from_id", sa.BigInteger(), nullable=True),
        sa.Column("revoke_reason", sa.String(length=32), nullable=True),
        sa.Column("issued_ip", sa.String(length=64), nullable=True),
        sa.Column("issued_user_agent", sa.Text(), nullable=True),
        sa.Column("issued_device_fingerprint_hash", sa.String(length=64), nullable=True),
        sa.Column("last_ip", sa.String(length=64), nullable=True),
        sa.Column("last_user_agent", sa.Text(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["rotated_from_id"], ["auth_refresh_token_v2.id"]),
        sa.UniqueConstraint("token_hash", name="uq_auth_refresh_token_v2_token_hash"),
        sa.UniqueConstraint("token_jti", name="uq_auth_refresh_token_v2_token_jti"),
    )
    op.create_index(
        "ix_auth_refresh_token_v2_user_employee_revoked",
        "auth_refresh_token_v2",
        ["user_id", "employee_id", "revoked_at"],
        unique=False,
    )
    op.create_index(
        "ix_auth_refresh_token_v2_expires_at",
        "auth_refresh_token_v2",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_auth_refresh_token_v2_expires_at", table_name="auth_refresh_token_v2")
    op.drop_index("ix_auth_refresh_token_v2_user_employee_revoked", table_name="auth_refresh_token_v2")
    op.drop_table("auth_refresh_token_v2")
