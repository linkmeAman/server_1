"""PRISM — Sidenav Config Table

Revision ID: 20260324_006
Revises: 20260320_005
Create Date: 2026-03-24 00:00:06

Adds a singleton table that stores the dynamic sidebar navigation layout
managed by the PRISM Sidenav Manager page (supreme users only).

Table: prism_sidenav_config
  - id=1 is the singleton row (INSERT ... ON DUPLICATE KEY UPDATE pattern)
  - config_json: JSON array of ManagedNavItem objects (serialised on the frontend)
  - version: incremented on every save (used for ETags / optimistic UI)
  - updated_by_user_id: ID of the supreme user who last saved the config
  - updated_at: auto-updated to CURRENT_TIMESTAMP on every write
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "20260324_006"
down_revision = "20260320_005"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return inspector.has_table(table_name)


def upgrade() -> None:
    if not _has_table("prism_sidenav_config"):
        op.create_table(
            "prism_sidenav_config",
            # Singleton row — always id=1
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
            # Full JSON array of nav items (ManagedNavItem[])
            sa.Column("config_json", sa.Text(length=2**24), nullable=False),
            # Who last saved the config
            sa.Column("updated_by_user_id", sa.Integer(), nullable=False),
            # Monotonically increasing — bumped on every PUT
            sa.Column("version", sa.Integer(), nullable=False, server_default=text("1")),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
                comment="Auto-updated on every write",
            ),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
            comment="Singleton table — one row (id=1) storing the current sidebar nav config",
        )


def downgrade() -> None:
    if _has_table("prism_sidenav_config"):
        op.drop_table("prism_sidenav_config")
