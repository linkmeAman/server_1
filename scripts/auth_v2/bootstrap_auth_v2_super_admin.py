#!/usr/bin/env python3
"""Bootstrap first auth v2 super admin."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from controllers.auth_v2.services.authorization import AuthorizationResolver
from core.database_v2 import central_session_context, main_session_context

GLOBAL_RESOURCE_CODE = "global"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap first auth v2 super admin")
    parser.add_argument("--employee-id", type=int, required=True)
    parser.add_argument("--role-code", type=str, required=True)
    parser.add_argument("--create-role-if-missing", action="store_true")
    parser.add_argument("--role-name", type=str, default=None)
    return parser.parse_args()


async def _active_employee(main_db, employee_id: int) -> Optional[Dict[str, Any]]:
    result = await main_db.execute(
        text(
            """
            SELECT id, position_id, department_id
            FROM employee
            WHERE id = :employee_id
              AND status = 1
              AND (park IS NULL OR park = 0)
            LIMIT 1
            """
        ),
        {"employee_id": int(employee_id)},
    )
    row = result.fetchone()
    return dict(row._mapping) if row else None


async def _resolve_or_create_role(
    central_db,
    *,
    role_code: str,
    role_name: Optional[str],
    create_if_missing: bool,
    actor_employee_id: int,
) -> Dict[str, Any]:
    lookup_result = await central_db.execute(
        text(
            """
            SELECT id, code, name, is_active
            FROM rbac_role
            WHERE code = :code
            LIMIT 1
            FOR UPDATE
            """
        ),
        {"code": role_code},
    )
    existing = lookup_result.fetchone()
    now = datetime.utcnow()

    if existing is None:
        if not create_if_missing:
            raise RuntimeError(
                f"Role '{role_code}' not found. Use --create-role-if-missing to allow creating it."
            )
        resolved_name = role_name.strip() if role_name else role_code.replace("_", " ").title()
        await central_db.execute(
            text(
                """
                INSERT INTO rbac_role (
                    code, name, description, is_active,
                    created_at, modified_at,
                    created_by_user_id, modified_by_user_id,
                    source
                ) VALUES (
                    :code, :name, :description, 1,
                    :created_at, :modified_at,
                    NULL, NULL,
                    :source
                )
                """
            ),
            {
                "code": role_code,
                "name": resolved_name,
                "description": "Bootstrapped super-admin role",
                "created_at": now,
                "modified_at": now,
                "source": "bootstrap_auth_v2_super_admin",
            },
        )
        fetch = await central_db.execute(
            text(
                """
                SELECT id, code, name, is_active
                FROM rbac_role
                WHERE code = :code
                LIMIT 1
                """
            ),
            {"code": role_code},
        )
        created = fetch.fetchone()
        if created is None:
            raise RuntimeError("Failed to create role")
        row = dict(created._mapping)
        return {
            "id": int(row["id"]),
            "code": str(row["code"]),
            "name": str(row["name"]),
            "is_active": int(row.get("is_active") or 0),
        }

    row = dict(existing._mapping)
    role_id = int(row["id"])
    if int(row.get("is_active") or 0) != 1:
        await central_db.execute(
            text(
                """
                UPDATE rbac_role
                SET is_active = 1,
                    modified_at = :modified_at
                WHERE id = :role_id
                """
            ),
            {"modified_at": now, "role_id": role_id},
        )
    return {
        "id": role_id,
        "code": str(row["code"]),
        "name": str(row["name"]),
        "is_active": 1,
    }


async def _ensure_global_resource(central_db, actor_employee_id: int) -> Dict[str, Any]:
    now = datetime.utcnow()
    lookup = await central_db.execute(
        text(
            """
            SELECT id, code, name, is_active
            FROM rbac_resource_v2
            WHERE code = :code
            LIMIT 1
            FOR UPDATE
            """
        ),
        {"code": GLOBAL_RESOURCE_CODE},
    )
    row = lookup.fetchone()
    if row is None:
        await central_db.execute(
            text(
                """
                INSERT INTO rbac_resource_v2 (
                    code, name, parent_id, sort_order, meta, is_active,
                    created_at, modified_at,
                    created_by_user_id, modified_by_user_id,
                    created_by_employee_id, modified_by_employee_id
                ) VALUES (
                    :code, :name, NULL, 10, NULL, 1,
                    :created_at, :modified_at,
                    NULL, NULL,
                    :created_by_employee_id, :modified_by_employee_id
                )
                """
            ),
            {
                "code": GLOBAL_RESOURCE_CODE,
                "name": "Global",
                "created_at": now,
                "modified_at": now,
                "created_by_employee_id": int(actor_employee_id),
                "modified_by_employee_id": int(actor_employee_id),
            },
        )
        fetch = await central_db.execute(
            text(
                """
                SELECT id, code, name, is_active
                FROM rbac_resource_v2
                WHERE code = :code
                LIMIT 1
                """
            ),
            {"code": GLOBAL_RESOURCE_CODE},
        )
        created = fetch.fetchone()
        if created is None:
            raise RuntimeError("Failed to create global resource")
        row_map = dict(created._mapping)
        return {
            "id": int(row_map["id"]),
            "code": str(row_map["code"]),
            "name": str(row_map["name"]),
            "is_active": int(row_map.get("is_active") or 0),
        }

    row_map = dict(row._mapping)
    if int(row_map.get("is_active") or 0) != 1:
        await central_db.execute(
            text(
                """
                UPDATE rbac_resource_v2
                SET is_active = 1,
                    modified_at = :modified_at,
                    modified_by_employee_id = :modified_by_employee_id
                WHERE id = :resource_id
                """
            ),
            {
                "modified_at": now,
                "modified_by_employee_id": int(actor_employee_id),
                "resource_id": int(row_map["id"]),
            },
        )
    return {
        "id": int(row_map["id"]),
        "code": str(row_map["code"]),
        "name": str(row_map["name"]),
        "is_active": 1,
    }


async def _upsert_global_super_permission(
    central_db,
    *,
    role_id: int,
    resource_id: int,
    actor_employee_id: int,
) -> None:
    now = datetime.utcnow()
    await central_db.execute(
        text(
            """
            INSERT INTO rbac_role_permission_v2 (
                role_id, resource_id, can_view, can_add, can_edit, can_delete, can_super, is_active,
                created_at, modified_at,
                created_by_user_id, modified_by_user_id,
                created_by_employee_id, modified_by_employee_id
            ) VALUES (
                :role_id, :resource_id, 0, 0, 0, 0, 1, 1,
                :created_at, :modified_at,
                NULL, NULL,
                :created_by_employee_id, :modified_by_employee_id
            )
            ON DUPLICATE KEY UPDATE
                can_view = VALUES(can_view),
                can_add = VALUES(can_add),
                can_edit = VALUES(can_edit),
                can_delete = VALUES(can_delete),
                can_super = VALUES(can_super),
                is_active = 1,
                modified_at = VALUES(modified_at),
                modified_by_employee_id = VALUES(modified_by_employee_id)
            """
        ),
        {
            "role_id": int(role_id),
            "resource_id": int(resource_id),
            "created_at": now,
            "modified_at": now,
            "created_by_employee_id": int(actor_employee_id),
            "modified_by_employee_id": int(actor_employee_id),
        },
    )


async def _upsert_employee_role(
    central_db,
    *,
    employee_id: int,
    role_id: int,
) -> None:
    now = datetime.utcnow()
    await central_db.execute(
        text(
            """
            INSERT INTO rbac_employee_role (
                employee_id, role_id, is_active, created_at, modified_at, created_by_user_id, modified_by_user_id, source
            ) VALUES (
                :employee_id, :role_id, 1, :created_at, :modified_at, NULL, NULL, :source
            )
            ON DUPLICATE KEY UPDATE
                is_active = 1,
                modified_at = VALUES(modified_at),
                source = VALUES(source)
            """
        ),
        {
            "employee_id": int(employee_id),
            "role_id": int(role_id),
            "created_at": now,
            "modified_at": now,
            "source": "bootstrap_auth_v2_super_admin",
        },
    )


async def _run(args: argparse.Namespace) -> None:
    role_code = args.role_code.strip()
    if not role_code:
        raise RuntimeError("--role-code is required")

    async with main_session_context() as main_db, central_session_context() as central_db:
        employee = await _active_employee(main_db, int(args.employee_id))
        if employee is None:
            raise RuntimeError(f"Employee {int(args.employee_id)} not found or inactive")

        async with central_db.begin():
            role = await _resolve_or_create_role(
                central_db,
                role_code=role_code,
                role_name=args.role_name,
                create_if_missing=bool(args.create_role_if_missing),
                actor_employee_id=int(args.employee_id),
            )
            global_resource = await _ensure_global_resource(central_db, int(args.employee_id))
            await _upsert_global_super_permission(
                central_db,
                role_id=int(role["id"]),
                resource_id=int(global_resource["id"]),
                actor_employee_id=int(args.employee_id),
            )
            await _upsert_employee_role(
                central_db,
                employee_id=int(args.employee_id),
                role_id=int(role["id"]),
            )

        authz = await AuthorizationResolver(main_db, central_db).resolve_employee_authorization(int(args.employee_id))
        summary = {
            "employee_id": int(args.employee_id),
            "effective_roles": authz.get("roles", []),
            "permissions": authz.get("permissions", []),
            "is_super": bool(authz.get("is_super", False)),
            "permissions_version": int(authz.get("permissions_version", 0)),
        }
        print(json.dumps(summary, indent=2, ensure_ascii=True))


def main() -> int:
    args = _parse_args()
    try:
        asyncio.run(_run(args))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
