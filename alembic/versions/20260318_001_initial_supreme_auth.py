"""initial supreme auth schema

Revision ID: 20260318_001
Revises:
Create Date: 2026-03-18 00:00:01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260318_001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_supreme_user",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("country_code", sa.String(length=8), nullable=False),
        sa.Column("mobile", sa.String(length=20), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("is_super", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
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
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "country_code",
            "mobile",
            name="uq_auth_supreme_user_mobile",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )

    op.create_table(
        "auth_refresh_token",
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
        sa.ForeignKeyConstraint(["rotated_from_id"], ["auth_refresh_token.id"]),
        sa.UniqueConstraint("token_hash", name="uq_auth_refresh_token_token_hash"),
        sa.UniqueConstraint("token_jti", name="uq_auth_refresh_token_token_jti"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )

    op.create_index(
        "ix_auth_refresh_token_user_employee_revoked",
        "auth_refresh_token",
        ["user_id", "employee_id", "revoked_at"],
        unique=False,
    )
    op.create_index(
        "ix_auth_refresh_token_expires_at",
        "auth_refresh_token",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_auth_refresh_token_expires_at", table_name="auth_refresh_token")
    op.drop_index("ix_auth_refresh_token_user_employee_revoked", table_name="auth_refresh_token")
    op.drop_table("auth_refresh_token")
    op.drop_table("auth_supreme_user")