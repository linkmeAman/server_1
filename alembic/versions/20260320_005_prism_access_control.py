"""PRISM — Policy-driven Role & Identity Security Manager

Revision ID: 20260320_005
Revises: 20260318_004
Create Date: 2026-03-20 00:00:05

All tables created here live in pf_central (the central DB).

Tables introduced:
  prism_roles                  — Role registry (system + custom)
  prism_user_roles             — M2M: users ↔ roles, with optional expiry
  prism_policies               — Named policy documents
  prism_policy_versions        — Append-only version history for every policy change
  prism_policy_statements      — Individual Allow/Deny statements (heart of PRISM)
  prism_role_policies          — Attach policies to roles
  prism_user_policies          — Inline policies directly on a user
  prism_user_permission_boundaries — Hard cap on max permissions (anti-escalation)
  prism_user_attributes        — Manual/derived ABAC attributes on users
  prism_resource_attributes    — Per-resource ABAC attributes (evaluated at runtime)
  prism_access_logs            — Every PDP decision logged, append-only, never delete
  prism_resource_registry      — Catalog of known resource types (drives UI tree)
  prism_action_registry        — Catalog of allowable actions per resource (drives UI)

Cross-DB note:
  user_id columns in PRISM reference the central `auth_supreme_user.id` initially,
  and will also reference `users.id` for app-level users.
  Employees and contacts remain in the main DB.  Foreign keys across DB boundaries
  are enforced at the application layer only — no DB-level FKs to main DB tables.

Deny-by-default:
  Everything is DENY unless an explicit Allow exists.
  An explicit Deny always wins, even over an Allow.

Schema design follows PRISM-HLD.md in SD/.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "20260320_005"
down_revision = "20260318_004"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return inspector.has_table(table_name)


def upgrade() -> None:

    # ── prism_roles ────────────────────────────────────────────────────────
    # Role registry.  'system' roles cannot be deleted.
    if not _has_table("prism_roles"):
        op.create_table(
            "prism_roles",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(length=128), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=True),
            # system roles are built-ins (e.g. super_admin); custom roles are user-defined
            sa.Column(
                "type",
                sa.Enum("system", "custom", name="prism_role_type"),
                nullable=False,
                server_default="custom",
            ),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=text("1")),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "modified_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            ),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    # ── prism_user_roles ───────────────────────────────────────────────────
    # M2M: users <-> roles.  expires_at enables time-bound (STS-style) roles.
    # user_id is a logical FK to auth_supreme_user.id / users.id on central DB.
    if not _has_table("prism_user_roles"):
        op.create_table(
            "prism_user_roles",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("role_id", sa.BigInteger(), sa.ForeignKey("prism_roles.id", ondelete="CASCADE"), nullable=False),
            # audit: who granted this role
            sa.Column("assigned_by", sa.BigInteger(), nullable=True),
            # NULL = permanent; non-NULL = role expires at this datetime
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint("user_id", "role_id", name="uq_prism_user_roles_user_role"),
            sa.Index("ix_prism_user_roles_user_id", "user_id"),
            sa.Index("ix_prism_user_roles_expires_at", "expires_at"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    # ── prism_policies ─────────────────────────────────────────────────────
    # Named policy documents.  version is incremented on every content change.
    # type drives evaluation logic:
    #   identity           — standard user/role-attached allow/deny
    #   resource           — resource-based policy (phase 2)
    #   permission_boundary — hard cap on effective permissions
    #   scp                — service control policy (org-level deny)
    if not _has_table("prism_policies"):
        op.create_table(
            "prism_policies",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(length=128), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default=text("1")),
            sa.Column(
                "type",
                sa.Enum(
                    "identity", "resource", "permission_boundary", "scp",
                    name="prism_policy_type",
                ),
                nullable=False,
                server_default="identity",
            ),
            # user_id of the creator (logical FK, cross-DB safe)
            sa.Column("created_by", sa.BigInteger(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=text("1")),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "modified_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            ),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    # ── prism_policy_versions ──────────────────────────────────────────────
    # Full audit trail of every policy document change.
    # document_json stores the complete policy snapshot at that version.
    # This table is append-only — rows are never updated or deleted.
    if not _has_table("prism_policy_versions"):
        op.create_table(
            "prism_policy_versions",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("policy_id", sa.BigInteger(), sa.ForeignKey("prism_policies.id", ondelete="CASCADE"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            # full snapshot of the policy at this version (all statements)
            sa.Column("document_json", sa.Text(), nullable=False),
            sa.Column("changed_by", sa.BigInteger(), nullable=True),
            sa.Column(
                "changed_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("change_reason", sa.String(length=512), nullable=True),
            sa.UniqueConstraint("policy_id", "version", name="uq_prism_policy_versions_policy_ver"),
            sa.Index("ix_prism_policy_versions_policy_id", "policy_id"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    # ── prism_policy_statements ────────────────────────────────────────────
    # Individual Allow / Deny statements.  This is the heart of PRISM.
    #
    # actions_json     — e.g. ["employee:read", "report:*"]
    # resources_json   — e.g. ["employee:*", "report:${user:id}"]
    # conditions_json  — condition block evaluated by the PDP at runtime
    # not_actions_json — inverse actions (exclude specific actions)
    # not_resources_json — inverse resources (exclude specific resources)
    # priority         — higher value = evaluated first; used to order within a policy
    if not _has_table("prism_policy_statements"):
        op.create_table(
            "prism_policy_statements",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("policy_id", sa.BigInteger(), sa.ForeignKey("prism_policies.id", ondelete="CASCADE"), nullable=False),
            # human-readable statement label (e.g. "AllowHRReadOwnDept")
            sa.Column("sid", sa.String(length=128), nullable=True),
            sa.Column(
                "effect",
                sa.Enum("Allow", "Deny", name="prism_statement_effect"),
                nullable=False,
            ),
            sa.Column("actions_json", sa.Text(), nullable=False),       # JSON array
            sa.Column("resources_json", sa.Text(), nullable=False),     # JSON array
            sa.Column("conditions_json", sa.Text(), nullable=True),     # JSON object
            sa.Column("not_actions_json", sa.Text(), nullable=True),    # JSON array or NULL
            sa.Column("not_resources_json", sa.Text(), nullable=True),  # JSON array or NULL
            sa.Column("priority", sa.Integer(), nullable=False, server_default=text("0")),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=text("1")),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "modified_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            ),
            sa.Index("ix_prism_policy_statements_policy_id", "policy_id"),
            sa.Index("ix_prism_policy_statements_effect", "effect"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    # ── prism_role_policies ────────────────────────────────────────────────
    # Attach policies to roles.  Many-to-many.
    if not _has_table("prism_role_policies"):
        op.create_table(
            "prism_role_policies",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("role_id", sa.BigInteger(), sa.ForeignKey("prism_roles.id", ondelete="CASCADE"), nullable=False),
            sa.Column("policy_id", sa.BigInteger(), sa.ForeignKey("prism_policies.id", ondelete="CASCADE"), nullable=False),
            sa.Column("attached_by", sa.BigInteger(), nullable=True),
            sa.Column(
                "attached_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint("role_id", "policy_id", name="uq_prism_role_policies_role_policy"),
            sa.Index("ix_prism_role_policies_policy_id", "policy_id"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    # ── prism_user_policies ────────────────────────────────────────────────
    # Inline policies attached directly to a specific user.
    # user_id is a logical FK — resolves at the application layer.
    if not _has_table("prism_user_policies"):
        op.create_table(
            "prism_user_policies",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("policy_id", sa.BigInteger(), sa.ForeignKey("prism_policies.id", ondelete="CASCADE"), nullable=False),
            sa.Column("attached_by", sa.BigInteger(), nullable=True),
            sa.Column(
                "attached_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint("user_id", "policy_id", name="uq_prism_user_policies_user_policy"),
            sa.Index("ix_prism_user_policies_user_id", "user_id"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    # ── prism_user_permission_boundaries ──────────────────────────────────
    # Hard cap on effective permissions.  Even admin roles respect the boundary.
    # Effective Permissions = (Identity Policies ∩ Boundary) - Explicit Denies
    # Set by super-admins only; the user themselves cannot override this.
    if not _has_table("prism_user_permission_boundaries"):
        op.create_table(
            "prism_user_permission_boundaries",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False, unique=True),
            sa.Column("policy_id", sa.BigInteger(), sa.ForeignKey("prism_policies.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("set_by", sa.BigInteger(), nullable=True),
            sa.Column(
                "set_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
            ),
            sa.Index("ix_prism_user_permission_boundaries_user_id", "user_id"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    # ── prism_user_attributes ──────────────────────────────────────────────
    # Manual or derived ABAC attributes on users.
    # source='employee_table' entries are synced from main DB at login time.
    # source='manual' are set by admins.
    # source='derived' are computed by the system (e.g. is_manager flag).
    #
    # These feed into the `user:*` namespace in condition evaluation.
    if not _has_table("prism_user_attributes"):
        op.create_table(
            "prism_user_attributes",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("key", sa.String(length=128), nullable=False),
            sa.Column("value", sa.String(length=512), nullable=False),
            sa.Column(
                "source",
                sa.Enum("manual", "derived", "employee_table", name="prism_attr_source"),
                nullable=False,
                server_default="manual",
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint("user_id", "key", name="uq_prism_user_attributes_user_key"),
            sa.Index("ix_prism_user_attributes_user_id", "user_id"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    # ── prism_resource_attributes ──────────────────────────────────────────
    # ABAC attributes on specific resource instances.
    # Evaluated at runtime by the PDP (not cached at login).
    # These feed into the `resource:*` namespace in condition evaluation.
    # Example: resource_type='employee', resource_id='1042', key='department', value='HR'
    if not _has_table("prism_resource_attributes"):
        op.create_table(
            "prism_resource_attributes",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("resource_type", sa.String(length=64), nullable=False),
            sa.Column("resource_id", sa.String(length=64), nullable=False),
            sa.Column("key", sa.String(length=128), nullable=False),
            sa.Column("value", sa.String(length=512), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint(
                "resource_type", "resource_id", "key",
                name="uq_prism_resource_attributes_type_id_key",
            ),
            sa.Index("ix_prism_resource_attributes_resource", "resource_type", "resource_id"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    # ── prism_access_logs ──────────────────────────────────────────────────
    # Append-only audit log of every PDP decision (Allow or Deny).
    # NEVER delete or update rows in this table.
    # request_context_json stores: IP, time, HTTP method, MFA status, user-agent.
    if not _has_table("prism_access_logs"):
        op.create_table(
            "prism_access_logs",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("action", sa.String(length=128), nullable=False),
            sa.Column("resource_type", sa.String(length=64), nullable=False),
            sa.Column("resource_id", sa.String(length=64), nullable=True),
            sa.Column(
                "decision",
                sa.Enum("Allow", "Deny", name="prism_access_decision"),
                nullable=False,
            ),
            # NULL when decision = Deny by default (no matching policy)
            sa.Column("matched_policy_id", sa.BigInteger(), nullable=True),
            sa.Column("matched_statement_id", sa.BigInteger(), nullable=True),
            sa.Column("deny_reason", sa.String(length=256), nullable=True),
            sa.Column("request_context_json", sa.Text(), nullable=True),
            sa.Column(
                "evaluated_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
            ),
            sa.Index("ix_prism_access_logs_user_id_evaluated_at", "user_id", "evaluated_at"),
            sa.Index("ix_prism_access_logs_action_resource", "action", "resource_type"),
            sa.Index("ix_prism_access_logs_decision", "decision"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    # ── prism_resource_registry ────────────────────────────────────────────
    # Catalog of known resource types.  Drives the UI permission tree.
    # parent_code enables nested hierarchy (e.g. "reports.top_summary" under "reports").
    # code must be unique and use dot-notation for namespacing.
    if not _has_table("prism_resource_registry"):
        op.create_table(
            "prism_resource_registry",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("code", sa.String(length=128), nullable=False, unique=True),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("parent_code", sa.String(length=128), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default=text("10")),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=text("1")),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
            ),
            sa.Index("ix_prism_resource_registry_parent_code", "parent_code"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    # ── prism_action_registry ──────────────────────────────────────────────
    # Catalog of allowable actions per resource type.  Drives UI dropdowns.
    # code format: "{resource_code}:{verb}" e.g. "employee:read", "report:export"
    # Wildcard actions ("*") are synthetic and not stored here.
    if not _has_table("prism_action_registry"):
        op.create_table(
            "prism_action_registry",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("code", sa.String(length=128), nullable=False, unique=True),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("resource_code", sa.String(length=128), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default=text("10")),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=text("1")),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
            ),
            sa.Index("ix_prism_action_registry_resource_code", "resource_code"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )


def downgrade() -> None:
    # Drop in reverse dependency order.
    for table in [
        "prism_action_registry",
        "prism_resource_registry",
        "prism_access_logs",
        "prism_resource_attributes",
        "prism_user_attributes",
        "prism_user_permission_boundaries",
        "prism_user_policies",
        "prism_role_policies",
        "prism_policy_statements",
        "prism_policy_versions",
        "prism_policies",
        "prism_user_roles",
        "prism_roles",
    ]:
        if _has_table(table):
            op.drop_table(table)
