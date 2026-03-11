"""create auth_audit_event_v2

Revision ID: 20260302_006
Revises: 20260302_005
Create Date: 2026-03-02 00:00:06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260302_006"
down_revision = "20260302_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_audit_event_v2",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("country_code", sa.String(length=8), nullable=True),
        sa.Column("mobile", sa.String(length=20), nullable=True),
        sa.Column("contact_id", sa.BigInteger(), nullable=True),
        sa.Column("employee_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_auth_audit_event_v2_event_type_created_at",
        "auth_audit_event_v2",
        ["event_type", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_auth_audit_event_v2_country_mobile_created_at",
        "auth_audit_event_v2",
        ["country_code", "mobile", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_auth_audit_event_v2_ip_created_at",
        "auth_audit_event_v2",
        ["ip", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_auth_audit_event_v2_outcome_created_at",
        "auth_audit_event_v2",
        ["outcome", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_auth_audit_event_v2_outcome_created_at", table_name="auth_audit_event_v2")
    op.drop_index("ix_auth_audit_event_v2_ip_created_at", table_name="auth_audit_event_v2")
    op.drop_index("ix_auth_audit_event_v2_country_mobile_created_at", table_name="auth_audit_event_v2")
    op.drop_index("ix_auth_audit_event_v2_event_type_created_at", table_name="auth_audit_event_v2")
    op.drop_table("auth_audit_event_v2")
