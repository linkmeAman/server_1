"""verify-identity + select-role flow support tables

Revision ID: 20260318_004
Revises: 20260318_003
Create Date: 2026-03-18 00:00:04

Tables introduced / used by POST /auth/verify-identity and POST /auth/select-role
----------------------------------------------------------------------------------
All tables below live in pf_central (the central DB).

  auth_lock_state
    Brute-force lock state keyed by (key_type, key_hash).
    login-employee flow uses key_type='login_employee'  (employee_id required).
    verify-identity flow uses key_type='verify_identity' (employee_id is NULL).
    → If the table already exists, this migration is a no-op for it.

  auth_audit_event
    Append-only audit log for all auth events.
    → If the table already exists, this migration is a no-op for it.

Tables from the existing login-employee flow (already in migration 001):
  auth_refresh_token  — session refresh tokens

Tables in the client DB (never touched by central migrations):
  contact             — person record  (contact.id = user.contact_id)
  employee            — employee role  (employee.contact_id = contact.id)
  employee_position   — position lookup table
  employee_department — department lookup table
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260318_004"
down_revision = "20260318_003"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return inspector.has_table(table_name)


def upgrade() -> None:
    # ── auth_lock_state ────────────────────────────────────────────────────
    # Tracks failed-attempt counts and cooldown locks per (key_type, key_hash).
    #   key_type  = 'login_employee'   → employee-level lock (employee_id set)
    #   key_type  = 'verify_identity'  → user-level lock    (employee_id NULL)
    if not _has_table("auth_lock_state"):
        op.create_table(
            "auth_lock_state",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("key_type", sa.String(length=32), nullable=False),
            sa.Column("country_code", sa.String(length=8), nullable=False),
            sa.Column("mobile", sa.String(length=20), nullable=False),
            # NULL when key_type='verify_identity' (mobile-level lock, no employee yet)
            sa.Column("employee_id", sa.BigInteger(), nullable=True),
            sa.Column("key_hash", sa.String(length=64), nullable=False),
            sa.Column("fail_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
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
                server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint("key_type", "key_hash", name="uq_auth_lock_state_type_hash"),
            sa.Index("ix_auth_lock_state_locked_until", "locked_until"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    # ── auth_audit_event ───────────────────────────────────────────────────
    # Append-only audit trail.  all auth events write here.
    if not _has_table("auth_audit_event"):
        op.create_table(
            "auth_audit_event",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("outcome", sa.String(length=16), nullable=False),
            sa.Column("reason_code", sa.String(length=64), nullable=True),
            sa.Column("country_code", sa.String(length=8), nullable=True),
            sa.Column("mobile", sa.String(length=20), nullable=True),
            sa.Column("contact_id", sa.BigInteger(), nullable=True),
            sa.Column("employee_id", sa.BigInteger(), nullable=True),
            sa.Column("user_id", sa.BigInteger(), nullable=True),
            sa.Column("ip", sa.String(length=64), nullable=True),
            sa.Column("user_agent", sa.Text(), nullable=True),
            sa.Column("request_id", sa.String(length=64), nullable=True),
            sa.Column("details_json", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Index("ix_auth_audit_event_event_type_created_at", "event_type", "created_at"),
            sa.Index("ix_auth_audit_event_mobile_created_at", "mobile", "created_at"),
            sa.Index("ix_auth_audit_event_user_id", "user_id"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )



def downgrade() -> None:
    if _has_table("auth_audit_event"):
        op.drop_table("auth_audit_event")
    if _has_table("auth_lock_state"):
        op.drop_table("auth_lock_state")
