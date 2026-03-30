#!/usr/bin/env python3
"""Bootstrap PRISM seed data.

Seeds the following into the central DB (idempotent — safe to re-run):

  ROLES (system, cannot be deleted):
    super_admin   — full wildcard access
    admin         — broad read/write, no system config
    manager       — read/write own department, no cross-org actions
    viewer        — read-only across all resources

  RESOURCE REGISTRY:
    Loaded from authz/resources_manifest.yaml (existing catalog).
    Adds: employees, contacts, users, system (new categories for PRISM).

  ACTION REGISTRY:
    Standard verbs registered per resource:
      list, read, create, write, delete, export
    Not all verbs apply to every resource — see ACTION_MAP below.

  POLICIES (one per system role):
    super_admin_policy   — Allow * on *
    admin_policy         — Allow broad read/write; Deny system:*
    manager_policy       — Allow read/write with dept ABAC condition stub
    viewer_policy        — Allow list/read on *

  ROLE → POLICY attachments:
    Each system role gets its matching policy attached.

Usage:
    python scripts/prism/bootstrap_prism_seed.py
    python scripts/prism/bootstrap_prism_seed.py --dry-run    (print what would be inserted)
    python scripts/prism/bootstrap_prism_seed.py --reset-policies  (re-create policies even if they exist)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import yaml
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import central_session_context

# ── Seed definitions ───────────────────────────────────────────────────────

SYSTEM_ROLES: list[dict] = [
    {
        "name": "super_admin",
        "description": "Full wildcard access. Can manage all PRISM objects. Cannot be deleted.",
        "type": "system",
    },
    {
        "name": "admin",
        "description": "Broad read/write access. Cannot touch system configuration or super_admin boundaries.",
        "type": "system",
    },
    {
        "name": "manager",
        "description": "Read/write within own department. ABAC-gated via user:department condition.",
        "type": "system",
    },
    {
        "name": "viewer",
        "description": "Read-only access across all resources. Cannot create, update, or delete.",
        "type": "system",
    },
]

# Extra resources beyond what's in resources_manifest.yaml
EXTRA_RESOURCES: list[dict] = [
    {"code": "employees",           "name": "Employees",         "parent": None, "sort_order": 40},
    {"code": "employees.profile",   "name": "Employee Profile",  "parent": "employees", "sort_order": 10},
    {"code": "employees.documents", "name": "Documents",         "parent": "employees", "sort_order": 20},
    {"code": "contacts",            "name": "Contacts",          "parent": None, "sort_order": 50},
    {"code": "users",               "name": "Users",             "parent": None, "sort_order": 60},
    {"code": "system",              "name": "System",            "parent": None, "sort_order": 70},
    {"code": "system.roles",        "name": "Role Management",   "parent": "system", "sort_order": 10},
    {"code": "system.policies",     "name": "Policy Management", "parent": "system", "sort_order": 20},
    {"code": "system.audit",        "name": "Audit Logs",        "parent": "system", "sort_order": 30},
]

# Verbs available per resource code (prefix-matched: "reports" also applies to "reports.*")
# Format: { resource_code_prefix: [verbs] }
ACTION_MAP: dict[str, list[str]] = {
    "global":    ["read"],
    "boards":    ["list", "read"],
    "reports":   ["list", "read", "export"],
    "employees": ["list", "read", "create", "write", "delete"],
    "contacts":  ["list", "read", "create", "write"],
    "users":     ["list", "read", "create", "write", "delete"],
    "system":    ["list", "read", "create", "write", "delete"],
}

VERB_LABELS: dict[str, str] = {
    "list":   "List",
    "read":   "Read / View",
    "create": "Create",
    "write":  "Update / Write",
    "delete": "Delete",
    "export": "Export",
}

# Policies: one per system role
# Format: { role_name: { name, description, statements: [...] } }
POLICY_DEFINITIONS: dict[str, dict] = {
    "super_admin": {
        "name": "super_admin_policy",
        "description": "Wildcard allow on all actions and resources. Assigned to super_admin role only.",
        "statements": [
            {
                "sid": "SuperAdminAllowAll",
                "effect": "Allow",
                "actions": ["*"],
                "resources": ["*"],
                "conditions": None,
                "priority": 100,
            }
        ],
    },
    "admin": {
        "name": "admin_policy",
        "description": "Broad read/write. Hard-denies system configuration actions.",
        "statements": [
            {
                "sid": "AdminAllowBroadAccess",
                "effect": "Allow",
                "actions": ["*:list", "*:read", "*:create", "*:write", "*:delete", "*:export"],
                "resources": ["*"],
                "conditions": None,
                "priority": 50,
            },
            {
                "sid": "AdminDenySystemConfig",
                "effect": "Deny",
                "actions": ["system:*"],
                "resources": ["system:*"],
                "conditions": None,
                "priority": 90,
            },
        ],
    },
    "manager": {
        "name": "manager_policy",
        "description": "Read/write employees and reports within own department. ABAC-gated.",
        "statements": [
            {
                "sid": "ManagerReadOwnDeptEmployees",
                "effect": "Allow",
                "actions": ["employees:list", "employees:read", "employees:write"],
                "resources": ["employees:*"],
                "conditions": {
                    "StringEquals": {
                        "user:department": "${resource:department}"
                    }
                },
                "priority": 40,
            },
            {
                "sid": "ManagerReadReports",
                "effect": "Allow",
                "actions": ["reports:list", "reports:read"],
                "resources": ["reports:*"],
                "conditions": None,
                "priority": 30,
            },
            {
                "sid": "ManagerReadBoards",
                "effect": "Allow",
                "actions": ["boards:list", "boards:read"],
                "resources": ["boards:*"],
                "conditions": None,
                "priority": 30,
            },
        ],
    },
    "viewer": {
        "name": "viewer_policy",
        "description": "Read-only. May list and read any resource. Cannot mutate anything.",
        "statements": [
            {
                "sid": "ViewerAllowReadList",
                "effect": "Allow",
                "actions": ["*:list", "*:read"],
                "resources": ["*"],
                "conditions": None,
                "priority": 10,
            },
            {
                "sid": "ViewerDenyAllMutations",
                "effect": "Deny",
                "actions": ["*:create", "*:write", "*:delete", "*:export"],
                "resources": ["*"],
                "conditions": None,
                "priority": 80,
            },
        ],
    },
}


# ── Helpers ────────────────────────────────────────────────────────────────

def _row(result) -> Optional[dict]:
    row = result.fetchone()
    return dict(row._mapping) if row else None


def _rows(result) -> list[dict]:
    return [dict(r._mapping) for r in result.fetchall()]


def _log(msg: str, dry_run: bool = False) -> None:
    prefix = "[DRY-RUN] " if dry_run else ""
    print(f"{prefix}{msg}")


def _load_manifest_resources() -> list[dict]:
    manifest_path = PROJECT_ROOT / "authz" / "resources_manifest.yaml"
    with open(manifest_path) as f:
        data = yaml.safe_load(f)
    resources = []
    for item in data.get("resources", []):
        resources.append({
            "code": item["code"],
            "name": item["name"],
            "parent": item.get("parent"),
            "sort_order": item.get("sort_order", 10),
        })
    return resources


def _build_actions(all_resources: list[dict]) -> list[dict]:
    actions = []
    for resource in all_resources:
        code = resource["code"]
        # Find matching verbs: exact match first, then prefix
        verbs = ACTION_MAP.get(code)
        if verbs is None:
            # Try prefix: "reports.top_summary" → check "reports"
            for prefix, vs in ACTION_MAP.items():
                if code.startswith(prefix + "."):
                    verbs = vs
                    break
        if not verbs:
            continue
        for i, verb in enumerate(verbs, start=1):
            actions.append({
                "code": f"{code}:{verb}",
                "name": f"{VERB_LABELS.get(verb, verb)} — {resource['name']}",
                "resource_code": code,
                "sort_order": i * 10,
            })
    return actions


# ── Seed functions ─────────────────────────────────────────────────────────

async def seed_roles(db, dry_run: bool) -> dict[str, int]:
    """Upsert system roles. Returns {role_name: role_id}."""
    role_ids: dict[str, int] = {}
    for role in SYSTEM_ROLES:
        existing = _row(await db.execute(
            text("SELECT id FROM prism_roles WHERE name = :name"),
            {"name": role["name"]},
        ))
        if existing:
            role_ids[role["name"]] = existing["id"]
            _log(f"  ROLE  '{role['name']}' — already exists (id={existing['id']}), skipped")
        else:
            if not dry_run:
                result = await db.execute(
                    text(
                        "INSERT INTO prism_roles (name, description, type, is_active) "
                        "VALUES (:name, :description, :type, 1)"
                    ),
                    {"name": role["name"], "description": role["description"], "type": role["type"]},
                )
                role_ids[role["name"]] = result.lastrowid
                _log(f"  ROLE  '{role['name']}' — created (id={result.lastrowid})")
            else:
                _log(f"  ROLE  '{role['name']}' — would create", dry_run=True)
                role_ids[role["name"]] = -1
    return role_ids


async def seed_resources(db, all_resources: list[dict], dry_run: bool) -> None:
    """Upsert resource registry entries."""
    for res in all_resources:
        existing = _row(await db.execute(
            text("SELECT id FROM prism_resource_registry WHERE code = :code"),
            {"code": res["code"]},
        ))
        if existing:
            _log(f"  RESOURCE  '{res['code']}' — already exists, skipped")
        else:
            if not dry_run:
                await db.execute(
                    text(
                        "INSERT INTO prism_resource_registry "
                        "(code, name, parent_code, sort_order, is_active) "
                        "VALUES (:code, :name, :parent_code, :sort_order, 1)"
                    ),
                    {
                        "code": res["code"],
                        "name": res["name"],
                        "parent_code": res.get("parent"),
                        "sort_order": res["sort_order"],
                    },
                )
                _log(f"  RESOURCE  '{res['code']}' — created")
            else:
                _log(f"  RESOURCE  '{res['code']}' — would create", dry_run=True)


async def seed_actions(db, actions: list[dict], dry_run: bool) -> None:
    """Upsert action registry entries."""
    for action in actions:
        existing = _row(await db.execute(
            text("SELECT id FROM prism_action_registry WHERE code = :code"),
            {"code": action["code"]},
        ))
        if existing:
            _log(f"  ACTION  '{action['code']}' — already exists, skipped")
        else:
            if not dry_run:
                await db.execute(
                    text(
                        "INSERT INTO prism_action_registry "
                        "(code, name, resource_code, sort_order, is_active) "
                        "VALUES (:code, :name, :resource_code, :sort_order, 1)"
                    ),
                    {
                        "code": action["code"],
                        "name": action["name"],
                        "resource_code": action["resource_code"],
                        "sort_order": action["sort_order"],
                    },
                )
                _log(f"  ACTION  '{action['code']}' — created")
            else:
                _log(f"  ACTION  '{action['code']}' — would create", dry_run=True)


async def seed_policies(db, role_ids: dict[str, int], dry_run: bool, reset: bool) -> dict[str, int]:
    """Create policy documents + statements and attach to roles.
    Returns {role_name: policy_id}."""
    policy_ids: dict[str, int] = {}

    for role_name, policy_def in POLICY_DEFINITIONS.items():
        policy_name = policy_def["name"]
        existing_policy = _row(await db.execute(
            text("SELECT id, version FROM prism_policies WHERE name = :name"),
            {"name": policy_name},
        ))

        if existing_policy and not reset:
            policy_ids[role_name] = existing_policy["id"]
            _log(f"  POLICY  '{policy_name}' — already exists (id={existing_policy['id']}), skipped")
        else:
            if existing_policy and reset:
                if not dry_run:
                    # Deactivate existing statements
                    await db.execute(
                        text("UPDATE prism_policy_statements SET is_active = 0 WHERE policy_id = :id"),
                        {"id": existing_policy["id"]},
                    )
                    policy_id = existing_policy["id"]
                    _log(f"  POLICY  '{policy_name}' — resetting statements (id={policy_id})")
                else:
                    _log(f"  POLICY  '{policy_name}' — would reset", dry_run=True)
                    policy_ids[role_name] = existing_policy["id"]
                    continue
            else:
                if not dry_run:
                    result = await db.execute(
                        text(
                            "INSERT INTO prism_policies "
                            "(name, description, type, version, is_active) "
                            "VALUES (:name, :description, 'identity', 1, 1)"
                        ),
                        {"name": policy_name, "description": policy_def["description"]},
                    )
                    policy_id = result.lastrowid
                    _log(f"  POLICY  '{policy_name}' — created (id={policy_id})")
                else:
                    _log(f"  POLICY  '{policy_name}' — would create", dry_run=True)
                    policy_ids[role_name] = -1
                    continue

            policy_ids[role_name] = policy_id

            # Insert statements
            for stmt in policy_def["statements"]:
                if not dry_run:
                    await db.execute(
                        text(
                            "INSERT INTO prism_policy_statements "
                            "(policy_id, sid, effect, actions_json, resources_json, "
                            "conditions_json, priority, is_active) "
                            "VALUES (:policy_id, :sid, :effect, :actions_json, :resources_json, "
                            ":conditions_json, :priority, 1)"
                        ),
                        {
                            "policy_id": policy_id,
                            "sid": stmt["sid"],
                            "effect": stmt["effect"],
                            "actions_json": json.dumps(stmt["actions"]),
                            "resources_json": json.dumps(stmt["resources"]),
                            "conditions_json": json.dumps(stmt["conditions"]) if stmt["conditions"] else None,
                            "priority": stmt["priority"],
                        },
                    )
                    _log(f"    STATEMENT  '{stmt['sid']}' ({stmt['effect']}) — created")
                else:
                    _log(f"    STATEMENT  '{stmt['sid']}' ({stmt['effect']}) — would create", dry_run=True)

        # Attach policy to role
        role_id = role_ids.get(role_name)
        if role_id and role_id > 0 and policy_ids.get(role_name, -1) > 0:
            pid = policy_ids[role_name]
            existing_attachment = _row(await db.execute(
                text(
                    "SELECT id FROM prism_role_policies WHERE role_id = :rid AND policy_id = :pid"
                ),
                {"rid": role_id, "pid": pid},
            ))
            if existing_attachment:
                _log(f"  ATTACH  role '{role_name}' → policy '{policy_name}' — already attached, skipped")
            else:
                if not dry_run:
                    await db.execute(
                        text(
                            "INSERT INTO prism_role_policies (role_id, policy_id, attached_by) "
                            "VALUES (:role_id, :policy_id, NULL)"
                        ),
                        {"role_id": role_id, "policy_id": pid},
                    )
                    _log(f"  ATTACH  role '{role_name}' → policy '{policy_name}'")
                else:
                    _log(f"  ATTACH  role '{role_name}' → policy '{policy_name}' — would attach", dry_run=True)

    return policy_ids


# ── Main ───────────────────────────────────────────────────────────────────

async def main(dry_run: bool, reset_policies: bool) -> None:
    mode = "DRY-RUN" if dry_run else "LIVE"
    print(f"\n{'='*60}")
    print(f"  PRISM Bootstrap Seed  [{mode}]")
    print(f"{'='*60}\n")

    # Load resources
    manifest_resources = _load_manifest_resources()
    all_resources = manifest_resources + EXTRA_RESOURCES

    # Build action list from resources
    all_actions = _build_actions(all_resources)

    print(f"[Plan] {len(SYSTEM_ROLES)} system roles")
    print(f"[Plan] {len(all_resources)} resource registry entries")
    print(f"[Plan] {len(all_actions)} action entries")
    print(f"[Plan] {len(POLICY_DEFINITIONS)} policies + role attachments\n")

    async with central_session_context() as db:
        print("── Roles ─────────────────────────────────────────────────")
        role_ids = await seed_roles(db, dry_run)
        if not dry_run:
            await db.commit()

        print("\n── Resource Registry ─────────────────────────────────────")
        await seed_resources(db, all_resources, dry_run)
        if not dry_run:
            await db.commit()

        print("\n── Action Registry ───────────────────────────────────────")
        await seed_actions(db, all_actions, dry_run)
        if not dry_run:
            await db.commit()

        print("\n── Policies & Attachments ────────────────────────────────")
        await seed_policies(db, role_ids, dry_run, reset_policies)
        if not dry_run:
            await db.commit()

    print(f"\n{'='*60}")
    print(f"  Done.  {'(nothing written — dry run)' if dry_run else 'All changes committed.'}")
    print(f"{'='*60}\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap PRISM seed data into central DB")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be inserted without writing anything",
    )
    parser.add_argument(
        "--reset-policies",
        action="store_true",
        help="Re-create policy statements even if policies already exist (roles and registry are always idempotent)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(main(dry_run=args.dry_run, reset_policies=args.reset_policies))

