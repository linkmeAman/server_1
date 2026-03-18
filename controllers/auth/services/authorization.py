"""Authorization resolver for auth v2.

This module is the single source of truth for effective authz computation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Sequence, Set

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from controllers.auth.constants import AUTH_EMPLOYEE_INACTIVE
from controllers.auth.services.common import AuthError

logger = logging.getLogger(__name__)

PERMISSIONS_SCHEMA_VERSION = 1
ALLOWED_PERMISSION_ACTIONS = ("view", "add", "edit", "delete", "super")
GLOBAL_RESOURCE_CODE = "global"
GLOBAL_SUPER_PERMISSION = "global:super"


class AuthorizationResolver:
    """Resolves org context, effective roles, and flattened permissions."""

    def __init__(self, main_db: AsyncSession, central_db: AsyncSession):
        self.main_db = main_db
        self.central_db = central_db

    async def _active_employee_row(self, employee_id: int) -> Dict[str, Any]:
        result = await self.main_db.execute(
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
        if row is None:
            raise AuthError(AUTH_EMPLOYEE_INACTIVE, "Employee is inactive", 403)
        return dict(row._mapping)

    async def _safe_master_name_lookup(
        self,
        *,
        table: str,
        id_column: str,
        name_column: str,
        row_id: int | None,
        fail_open: bool,
    ) -> str | None:
        if row_id is None:
            return None

        filtered_query = text(
            f"""
            SELECT {name_column}
            FROM {table}
            WHERE {id_column} = :row_id
              AND (park IS NULL OR park = 0)
              AND (inactive IS NULL OR inactive = 0)
              AND (status IS NULL OR status = 1)
            LIMIT 1
            """
        )
        fallback_query = text(
            f"""
            SELECT {name_column}
            FROM {table}
            WHERE {id_column} = :row_id
            LIMIT 1
            """
        )

        try:
            result = await self.central_db.execute(filtered_query, {"row_id": int(row_id)})
            row = result.fetchone()
            if row is None:
                return None
            value = row._mapping.get(name_column)
            return str(value).strip() if value is not None else None
        except Exception:
            # Fallback for schema variants where inactive/status/park columns are absent.
            try:
                result = await self.central_db.execute(fallback_query, {"row_id": int(row_id)})
                row = result.fetchone()
                if row is None:
                    return None
                value = row._mapping.get(name_column)
                return str(value).strip() if value is not None else None
            except Exception:
                if fail_open:
                    return None
                raise

    async def get_org_context(self, employee_id: int, fail_open_name_lookup: bool = True) -> Dict[str, Any]:
        employee = await self._active_employee_row(employee_id)
        position_id = employee.get("position_id")
        department_id = employee.get("department_id")

        degraded_name_lookup = False
        try:
            position_name = await self._safe_master_name_lookup(
                table="employee_position",
                id_column="id",
                name_column="position",
                row_id=int(position_id) if position_id is not None else None,
                fail_open=fail_open_name_lookup,
            )
            department_name = await self._safe_master_name_lookup(
                table="employee_department",
                id_column="id",
                name_column="department",
                row_id=int(department_id) if department_id is not None else None,
                fail_open=fail_open_name_lookup,
            )
        except Exception:
            if not fail_open_name_lookup:
                raise
            degraded_name_lookup = True
            position_name = None
            department_name = None

        if fail_open_name_lookup and (position_name is None or department_name is None):
            degraded_name_lookup = True

        return {
            "employee_id": int(employee["id"]),
            "position_id": int(position_id) if position_id is not None else None,
            "position": position_name,
            "department_id": int(department_id) if department_id is not None else None,
            "department": department_name,
            "degraded_name_lookup": degraded_name_lookup,
        }

    async def get_direct_roles(self, employee_id: int) -> List[Dict[str, Any]]:
        result = await self.central_db.execute(
            text(
                """
                SELECT
                    rr.id AS role_id,
                    rr.code AS role_code,
                    rr.name AS role_name,
                    er.id AS source_id
                FROM rbac_employee_role er
                JOIN rbac_role rr ON rr.id = er.role_id
                WHERE er.employee_id = :employee_id
                  AND er.is_active = 1
                  AND rr.is_active = 1
                ORDER BY rr.code ASC
                """
            ),
            {"employee_id": int(employee_id)},
        )
        rows = [dict(row._mapping) for row in result.fetchall()]
        for row in rows:
            row["source"] = "direct"
        return rows

    async def get_pair_roles(self, position_id: int | None, department_id: int | None) -> List[Dict[str, Any]]:
        if position_id is None or department_id is None:
            return []

        result = await self.central_db.execute(
            text(
                """
                SELECT
                    rr.id AS role_id,
                    rr.code AS role_code,
                    rr.name AS role_name,
                    pdr.id AS source_id
                FROM rbac_position_department_role_v2 pdr
                JOIN rbac_role rr ON rr.id = pdr.role_id
                WHERE pdr.position_id = :position_id
                  AND pdr.department_id = :department_id
                  AND pdr.is_active = 1
                  AND rr.is_active = 1
                ORDER BY rr.code ASC
                """
            ),
            {
                "position_id": int(position_id),
                "department_id": int(department_id),
            },
        )
        rows = [dict(row._mapping) for row in result.fetchall()]
        for row in rows:
            row["source"] = "position_department_pair"
        return rows

    async def get_effective_roles(
        self,
        employee_id: int,
        position_id: int | None,
        department_id: int | None,
    ) -> Dict[str, Any]:
        direct_roles = await self.get_direct_roles(employee_id)
        pair_roles = await self.get_pair_roles(position_id, department_id)

        effective_by_id: Dict[int, Dict[str, Any]] = {}
        trace: List[Dict[str, Any]] = []

        for row in direct_roles + pair_roles:
            role_id = int(row["role_id"])
            role_code = str(row.get("role_code") or "").strip()
            role_name = str(row.get("role_name") or "").strip()
            source = str(row.get("source") or "")
            source_id = row.get("source_id")
            trace.append(
                {
                    "source": source,
                    "source_id": int(source_id) if source_id is not None else None,
                    "role_id": role_id,
                    "role_code": role_code,
                }
            )
            if role_id not in effective_by_id:
                effective_by_id[role_id] = {
                    "role_id": role_id,
                    "role_code": role_code,
                    "role_name": role_name,
                }

        effective_rows = sorted(effective_by_id.values(), key=lambda item: (item["role_code"], item["role_id"]))
        roles = [{"role_code": row["role_code"], "role_name": row["role_name"]} for row in effective_rows]
        role_ids = [int(row["role_id"]) for row in effective_rows]
        return {
            "roles": roles,
            "role_ids": role_ids,
            "trace": trace,
        }

    async def get_effective_permissions(self, role_ids: Sequence[int]) -> Dict[str, Any]:
        if not role_ids:
            return {
                "permissions": [],
                "resource_ids": [],
                "trace": [],
                "is_super": False,
            }

        result = await self.central_db.execute(
            text(
                """
                SELECT
                    rp.role_id,
                    rp.resource_id,
                    rp.can_view,
                    rp.can_add,
                    rp.can_edit,
                    rp.can_delete,
                    rp.can_super,
                    res.code AS resource_code
                FROM rbac_role_permission_v2 rp
                JOIN rbac_resource_v2 res ON res.id = rp.resource_id
                WHERE rp.role_id IN :role_ids
                  AND rp.is_active = 1
                  AND res.is_active = 1
                """
            ).bindparams(bindparam("role_ids", expanding=True)),
            {"role_ids": [int(role_id) for role_id in role_ids]},
        )

        permissions: Set[str] = set()
        trace: List[Dict[str, Any]] = []
        resource_ids: Set[int] = set()
        for row in result.fetchall():
            data = dict(row._mapping)
            role_id = int(data["role_id"])
            resource_id = int(data["resource_id"])
            resource_code = str(data.get("resource_code") or "").strip()
            if not resource_code:
                continue

            row_actions: List[str] = []
            flag_by_action = {
                "view": int(data.get("can_view") or 0),
                "add": int(data.get("can_add") or 0),
                "edit": int(data.get("can_edit") or 0),
                "delete": int(data.get("can_delete") or 0),
                "super": int(data.get("can_super") or 0),
            }
            for action in ALLOWED_PERMISSION_ACTIONS:
                if flag_by_action[action] != 1:
                    continue
                if action == "super" and resource_code != GLOBAL_RESOURCE_CODE:
                    continue
                permission_code = f"{resource_code}:{action}"
                row_actions.append(action)
                permissions.add(permission_code)
                resource_ids.add(resource_id)

            if row_actions:
                trace.append(
                    {
                        "role_id": role_id,
                        "resource_id": resource_id,
                        "resource_code": resource_code,
                        "actions": sorted(row_actions),
                    }
                )

        permission_list = sorted(permissions)
        is_super = GLOBAL_SUPER_PERMISSION in permissions
        return {
            "permissions": permission_list,
            "resource_ids": sorted(resource_ids),
            "trace": trace,
            "is_super": is_super,
        }

    async def _max_modified_epoch(self, sql_text: Any, params: Dict[str, Any]) -> int:
        result = await self.central_db.execute(sql_text, params)
        row = result.fetchone()
        if row is None:
            return 0
        value = row._mapping.get("max_epoch")
        return int(value or 0)

    async def compute_permissions_version(
        self,
        employee_id: int,
        position_id: int | None,
        department_id: int | None,
        role_ids: Sequence[int],
        resource_ids: Sequence[int],
    ) -> int:
        epochs = [
            await self._max_modified_epoch(
                text(
                    """
                    SELECT COALESCE(UNIX_TIMESTAMP(MAX(modified_at)), 0) AS max_epoch
                    FROM rbac_employee_role
                    WHERE employee_id = :employee_id
                    """
                ),
                {"employee_id": int(employee_id)},
            )
        ]

        if position_id is not None and department_id is not None:
            epochs.append(
                await self._max_modified_epoch(
                    text(
                        """
                        SELECT COALESCE(UNIX_TIMESTAMP(MAX(modified_at)), 0) AS max_epoch
                        FROM rbac_position_department_role_v2
                        WHERE position_id = :position_id
                          AND department_id = :department_id
                        """
                    ),
                    {
                        "position_id": int(position_id),
                        "department_id": int(department_id),
                    },
                )
            )

        if role_ids:
            epochs.append(
                await self._max_modified_epoch(
                    text(
                        """
                        SELECT COALESCE(UNIX_TIMESTAMP(MAX(modified_at)), 0) AS max_epoch
                        FROM rbac_role_permission_v2
                        WHERE role_id IN :role_ids
                        """
                    ).bindparams(bindparam("role_ids", expanding=True)),
                    {"role_ids": [int(role_id) for role_id in role_ids]},
                )
            )

        if resource_ids:
            epochs.append(
                await self._max_modified_epoch(
                    text(
                        """
                        SELECT COALESCE(UNIX_TIMESTAMP(MAX(modified_at)), 0) AS max_epoch
                        FROM rbac_resource_v2
                        WHERE id IN :resource_ids
                        """
                    ).bindparams(bindparam("resource_ids", expanding=True)),
                    {"resource_ids": [int(resource_id) for resource_id in resource_ids]},
                )
            )

        return max(epochs) if epochs else 0

    async def resolve_employee_authorization(self, employee_id: int) -> Dict[str, Any]:
        org = await self.get_org_context(employee_id, fail_open_name_lookup=False)
        roles_info = await self.get_effective_roles(
            employee_id=employee_id,
            position_id=org.get("position_id"),
            department_id=org.get("department_id"),
        )
        permissions_info = await self.get_effective_permissions(roles_info["role_ids"])
        permissions_version = await self.compute_permissions_version(
            employee_id=employee_id,
            position_id=org.get("position_id"),
            department_id=org.get("department_id"),
            role_ids=roles_info["role_ids"],
            resource_ids=permissions_info["resource_ids"],
        )

        output = {
            "employee_id": int(employee_id),
            "position_id": org.get("position_id"),
            "position": org.get("position"),
            "department_id": org.get("department_id"),
            "department": org.get("department"),
            "roles": roles_info["roles"],
            "permissions": permissions_info["permissions"],
            "grants_trace": {
                "roles": roles_info["trace"],
                "permissions": permissions_info["trace"],
            },
            "is_super": bool(permissions_info["is_super"]),
            "permissions_version": int(permissions_version),
            "permissions_schema_version": PERMISSIONS_SCHEMA_VERSION,
        }

        logger.info(
            "AUTHZ_RESOLVED employee_id=%s position_id=%s department_id=%s role_count=%s permission_count=%s is_super=%s permissions_version=%s",
            output["employee_id"],
            output["position_id"],
            output["department_id"],
            len(output["roles"]),
            len(output["permissions"]),
            output["is_super"],
            output["permissions_version"],
        )
        return output

