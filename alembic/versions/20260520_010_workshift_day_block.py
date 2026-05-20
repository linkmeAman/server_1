"""Add block column to workshift_day

Revision ID: 20260520_010
Revises: 20260515_009_hr_tds_tables
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "20260520_010"
down_revision = "20260515_009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workshift_day",
        sa.Column(
            "block",
            sa.SmallInteger(),
            nullable=False,
            server_default="0",
            comment="1 = blocked day (employee cannot clock in/out on this day)",
        ),
    )


def downgrade() -> None:
    op.drop_column("workshift_day", "block")
