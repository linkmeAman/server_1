"""Internal admin APIs for auth v2 permissions management."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from controllers.auth_v2.constants import AUTH_BAD_REQUEST, AUTH_SERVICE_UNAVAILABLE
from controllers.auth_v2.dependencies import require_v2_super_auth
from controllers.auth_v2.schemas.models import CurrentV2User
from controllers.auth_v2.services.authorization import AuthorizationResolver
from controllers.auth_v2.services.common import AuthV2Error, request_id, success_json_response
from core.database_v2 import get_central_db_session, get_main_db_session

router = APIRouter(prefix="/internal/auth/v2/permissions", tags=["auth-v2-permissions-admin"])
logger = logging.getLogger(__name__)

RESOURCE_CODE_RE = re.compile(r"^[a-z0-9_.-]+$")
GLOBAL_RESOURCE_CODE = "global"


class ResourceCreateRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=191)
    parent_id: Optional[int] = None
    sort_order: int = 0
    meta: Optional[Dict[str, Any]] = None
    is_active: int = 1


class ResourcePatchRequest(BaseModel):
    expected_modified_at: Optional[int] = None
    name: Optional[str] = None
    parent_id: Optional[int] = None
    sort_order: Optional[int] = None
    meta: Optional[Dict[str, Any]] = None
    is_active: Optional[int] = None


class RolePermissionPutRequest(BaseModel):
    role_id: int
    resource_id: int
    can_view: int = 0
    can_add: int = 0
    can_edit: int = 0
    can_delete: int = 0
    can_super: int = 0
    is_active: int = 1
    expected_modified_at: Optional[int] = None


class RolePermissionDeleteRequest(BaseModel):
    role_id: int
    resource_id: int
    expected_modified_at: Optional[int] = None


class PairRolePutRequest(BaseModel):
    position_id: int
    department_id: int
    role_id: int
    is_active: int = 1
    expected_modified_at: Optional[int] = None


def _epoch_seconds(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    return int(value)


def _actor_fields(current_user: CurrentV2User) -> Dict[str, int]:
    return {
        "created_by_user_id": int(current_user.user_id),
        "modified_by_user_id": int(current_user.user_id),
        "created_by_employee_id": int(current_user.employee_id),
        "modified_by_employee_id": int(current_user.employee_id),
    }


def _modified_actor_fields(current_user: CurrentV2User) -> Dict[str, int]:
    return {
        "modified_by_user_id": int(current_user.user_id),
        "modified_by_employee_id": int(current_user.employee_id),
    }


def _normalize_flag(value: Any) -> int:
    return 1 if int(value or 0) == 1 else 0


def _normalize_meta(meta: Optional[Dict[str, Any]]) -> str | None:
    if meta is None:
        return None
    return json.dumps(meta, separators=(",", ":"), ensure_ascii=True)


def _validate_resource_code(code: str) -> str:
    value = code.strip().lower()
    if not value:
        raise AuthV2Error(AUTH_BAD_REQUEST, "Resource code is required", 400)
    if not RESOURCE_CODE_RE.match(value):
        raise AuthV2Error(AUTH_BAD_REQUEST, "Invalid resource code format", 400)
    if value.startswith(".") or value.endswith("."):
        raise AuthV2Error(AUTH_BAD_REQUEST, "Resource code cannot start or end with '.'", 400)
    if ".." in value:
        raise AuthV2Error(AUTH_BAD_REQUEST, "Resource code cannot contain consecutive dots", 400)
    return value


def _serialize_resource_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "code": str(row["code"]),
        "name": str(row["name"]),
        "parent_id": int(row["parent_id"]) if row.get("parent_id") is not None else None,
        "sort_order": int(row.get("sort_order") or 0),
        "meta": row.get("meta"),
        "is_active": int(row.get("is_active") or 0),
        "modified_at": _epoch_seconds(row.get("modified_at")),
    }


def _admin_log(
    *,
    request_id_value: str,
    current_user: CurrentV2User,
    endpoint: str,
    operation: str,
    target: Dict[str, Any],
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
    result: str,
) -> None:
    logger.info(
        "AUTHZ_ADMIN request_id=%s actor_user_id=%s actor_employee_id=%s endpoint=%s operation=%s target=%s before=%s after=%s result=%s",
        request_id_value,
        int(current_user.user_id),
        int(current_user.employee_id),
        endpoint,
        operation,
        json.dumps(target, separators=(",", ":"), ensure_ascii=True),
        json.dumps(before or {}, separators=(",", ":"), ensure_ascii=True),
        json.dumps(after or {}, separators=(",", ":"), ensure_ascii=True),
        result,
    )


async def _require_expected_match(expected_modified_at: int, row: Dict[str, Any], field_name: str = "modified_at") -> None:
    actual = _epoch_seconds(row.get(field_name))
    if int(expected_modified_at) != int(actual):
        raise AuthV2Error(AUTH_BAD_REQUEST, "Version conflict", 409, details={"actual_modified_at": actual})


async def _resource_by_id_for_update(central_db: AsyncSession, resource_id: int) -> Optional[Dict[str, Any]]:
    result = await central_db.execute(
        text(
            """
            SELECT id, code, name, parent_id, sort_order, meta, is_active, modified_at
            FROM rbac_resource_v2
            WHERE id = :resource_id
            LIMIT 1
            FOR UPDATE
            """
        ),
        {"resource_id": int(resource_id)},
    )
    row = result.fetchone()
    return dict(row._mapping) if row else None


async def _resource_exists(central_db: AsyncSession, resource_id: int) -> Optional[Dict[str, Any]]:
    result = await central_db.execute(
        text(
            """
            SELECT id, code, parent_id
            FROM rbac_resource_v2
            WHERE id = :resource_id
            LIMIT 1
            """
        ),
        {"resource_id": int(resource_id)},
    )
    row = result.fetchone()
    return dict(row._mapping) if row else None


async def _validate_no_cycle(
    central_db: AsyncSession,
    *,
    resource_id: int,
    parent_id: int | None,
) -> None:
    if parent_id is None:
        return
    if int(parent_id) == int(resource_id):
        raise AuthV2Error(AUTH_BAD_REQUEST, "Resource parent cannot be itself", 400)

    seen: set[int] = set()
    cursor: Optional[int] = int(parent_id)
    while cursor is not None:
        if cursor in seen:
            raise AuthV2Error(AUTH_BAD_REQUEST, "Resource parent cycle detected", 400)
        seen.add(cursor)
        if cursor == int(resource_id):
            raise AuthV2Error(AUTH_BAD_REQUEST, "Resource parent cycle detected", 400)
        row = await _resource_exists(central_db, cursor)
        if row is None:
            raise AuthV2Error(AUTH_BAD_REQUEST, "Parent resource not found", 400)
        next_parent = row.get("parent_id")
        cursor = int(next_parent) if next_parent is not None else None


async def _resource_code_for_id(central_db: AsyncSession, resource_id: int) -> Optional[str]:
    result = await central_db.execute(
        text(
            """
            SELECT code
            FROM rbac_resource_v2
            WHERE id = :resource_id
            LIMIT 1
            """
        ),
        {"resource_id": int(resource_id)},
    )
    row = result.fetchone()
    if row is None:
        return None
    return str(row._mapping.get("code") or "")


async def _role_exists(central_db: AsyncSession, role_id: int) -> bool:
    result = await central_db.execute(
        text(
            """
            SELECT id
            FROM rbac_role
            WHERE id = :role_id
            LIMIT 1
            """
        ),
        {"role_id": int(role_id)},
    )
    return result.fetchone() is not None


async def _master_id_exists(central_db: AsyncSession, *, table: str, row_id: int) -> bool:
    result = await central_db.execute(
        text(
            f"""
            SELECT id
            FROM {table}
            WHERE id = :row_id
            LIMIT 1
            """
        ),
        {"row_id": int(row_id)},
    )
    return result.fetchone() is not None


@router.get("/resources")
async def list_resources(
    request: Request,
    include_inactive: bool = Query(True),
    current_user: CurrentV2User = Depends(require_v2_super_auth),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    rid = request_id(request)
    try:
        where_clause = "" if include_inactive else "WHERE is_active = 1"
        result = await central_db.execute(
            text(
                f"""
                SELECT id, code, name, parent_id, sort_order, meta, is_active, modified_at
                FROM rbac_resource_v2
                {where_clause}
                ORDER BY sort_order ASC, name ASC, id ASC
                """
            )
        )
        rows = [_serialize_resource_row(dict(row._mapping)) for row in result.fetchall()]
        children: Dict[int | None, List[Dict[str, Any]]] = {}
        for row in rows:
            children.setdefault(row["parent_id"], []).append(row)
        for item_list in children.values():
            item_list.sort(key=lambda item: (int(item["sort_order"]), str(item["name"]).lower(), int(item["id"])))

        def _build(parent_id: int | None) -> List[Dict[str, Any]]:
            return [{**item, "children": _build(item["id"])} for item in children.get(parent_id, [])]

        data = {"resources": rows, "tree": _build(None)}
        _admin_log(
            request_id_value=rid,
            current_user=current_user,
            endpoint="/resources",
            operation="list",
            target={"include_inactive": bool(include_inactive)},
            before=None,
            after={"count": len(rows)},
            result="success",
        )
        return success_json_response(data, request_id_value=rid, message="Resources fetched")
    except AuthV2Error:
        raise
    except Exception:
        raise AuthV2Error(AUTH_SERVICE_UNAVAILABLE, "Auth v2 service unavailable", 503)


@router.post("/resources")
async def create_resource(
    payload: ResourceCreateRequest,
    request: Request,
    current_user: CurrentV2User = Depends(require_v2_super_auth),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    rid = request_id(request)
    code = _validate_resource_code(payload.code)
    parent_id = int(payload.parent_id) if payload.parent_id is not None else None
    if parent_id is not None and await _resource_exists(central_db, parent_id) is None:
        raise AuthV2Error(AUTH_BAD_REQUEST, "Parent resource not found", 400)

    actor = _actor_fields(current_user)
    try:
        async with central_db.begin():
            now = datetime.utcnow()
            await central_db.execute(
                text(
                    """
                    INSERT INTO rbac_resource_v2 (
                        code, name, parent_id, sort_order, meta, is_active,
                        created_at, modified_at,
                        created_by_user_id, modified_by_user_id,
                        created_by_employee_id, modified_by_employee_id
                    ) VALUES (
                        :code, :name, :parent_id, :sort_order, :meta, :is_active,
                        :created_at, :modified_at,
                        :created_by_user_id, :modified_by_user_id,
                        :created_by_employee_id, :modified_by_employee_id
                    )
                    """
                ),
                {
                    "code": code,
                    "name": payload.name.strip(),
                    "parent_id": parent_id,
                    "sort_order": int(payload.sort_order),
                    "meta": _normalize_meta(payload.meta),
                    "is_active": _normalize_flag(payload.is_active),
                    "created_at": now,
                    "modified_at": now,
                    **actor,
                },
            )

            row_result = await central_db.execute(
                text(
                    """
                    SELECT id, code, name, parent_id, sort_order, meta, is_active, modified_at
                    FROM rbac_resource_v2
                    WHERE code = :code
                    LIMIT 1
                    """
                ),
                {"code": code},
            )
            row = row_result.fetchone()
            if row is None:
                raise AuthV2Error(AUTH_BAD_REQUEST, "Failed to create resource", 500)
            created = _serialize_resource_row(dict(row._mapping))

        _admin_log(
            request_id_value=rid,
            current_user=current_user,
            endpoint="/resources",
            operation="create",
            target={"code": code},
            before=None,
            after=created,
            result="success",
        )
        return success_json_response({"resource": created}, request_id_value=rid, message="Resource created")
    except AuthV2Error:
        raise
    except Exception as exc:
        if "Duplicate entry" in str(exc):
            raise AuthV2Error(AUTH_BAD_REQUEST, "Resource code already exists", 400)
        raise AuthV2Error(AUTH_SERVICE_UNAVAILABLE, "Auth v2 service unavailable", 503)


@router.patch("/resources/{resource_id}")
async def patch_resource(
    resource_id: int,
    payload: ResourcePatchRequest,
    request: Request,
    current_user: CurrentV2User = Depends(require_v2_super_auth),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    rid = request_id(request)
    if payload.expected_modified_at is None:
        raise AuthV2Error(AUTH_BAD_REQUEST, "expected_modified_at is required", 400)

    updates: Dict[str, Any] = {}
    if payload.name is not None:
        updates["name"] = payload.name.strip()
    if payload.sort_order is not None:
        updates["sort_order"] = int(payload.sort_order)
    if payload.meta is not None:
        updates["meta"] = _normalize_meta(payload.meta)
    if payload.is_active is not None:
        updates["is_active"] = _normalize_flag(payload.is_active)
    if payload.parent_id is not None:
        updates["parent_id"] = int(payload.parent_id)

    try:
        async with central_db.begin():
            existing = await _resource_by_id_for_update(central_db, resource_id)
            if existing is None:
                raise AuthV2Error(AUTH_BAD_REQUEST, "Resource not found", 404)
            before = _serialize_resource_row(existing)
            await _require_expected_match(payload.expected_modified_at, existing)

            if "parent_id" in updates:
                await _validate_no_cycle(central_db, resource_id=int(resource_id), parent_id=updates["parent_id"])

            if updates:
                updates.update(_modified_actor_fields(current_user))
                updates["modified_at"] = datetime.utcnow()
                set_clause = ", ".join([f"{key} = :{key}" for key in updates.keys()])
                await central_db.execute(
                    text(
                        f"""
                        UPDATE rbac_resource_v2
                        SET {set_clause}
                        WHERE id = :resource_id
                        """
                    ),
                    {**updates, "resource_id": int(resource_id)},
                )

            refreshed = await _resource_by_id_for_update(central_db, resource_id)
            if refreshed is None:
                raise AuthV2Error(AUTH_BAD_REQUEST, "Resource not found", 404)
            after = _serialize_resource_row(refreshed)

        _admin_log(
            request_id_value=rid,
            current_user=current_user,
            endpoint=f"/resources/{resource_id}",
            operation="patch",
            target={"resource_id": int(resource_id)},
            before=before,
            after=after,
            result="success",
        )
        return success_json_response({"resource": after}, request_id_value=rid, message="Resource updated")
    except AuthV2Error:
        raise
    except Exception:
        raise AuthV2Error(AUTH_SERVICE_UNAVAILABLE, "Auth v2 service unavailable", 503)


@router.get("/roles")
async def list_roles(
    request: Request,
    include_inactive: bool = Query(True),
    current_user: CurrentV2User = Depends(require_v2_super_auth),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    rid = request_id(request)
    try:
        where_clause = "" if include_inactive else "WHERE is_active = 1"
        result = await central_db.execute(
            text(
                f"""
                SELECT id, code, name, is_active, modified_at
                FROM rbac_role
                {where_clause}
                ORDER BY code ASC, id ASC
                """
            )
        )
        rows = [
            {
                "id": int(row._mapping["id"]),
                "code": str(row._mapping.get("code") or ""),
                "name": str(row._mapping.get("name") or ""),
                "is_active": int(row._mapping.get("is_active") or 0),
                "modified_at": _epoch_seconds(row._mapping.get("modified_at")),
            }
            for row in result.fetchall()
        ]
        _admin_log(
            request_id_value=rid,
            current_user=current_user,
            endpoint="/roles",
            operation="list",
            target={"include_inactive": bool(include_inactive)},
            before=None,
            after={"count": len(rows)},
            result="success",
        )
        return success_json_response({"roles": rows}, request_id_value=rid, message="Roles fetched")
    except Exception:
        raise AuthV2Error(AUTH_SERVICE_UNAVAILABLE, "Auth v2 service unavailable", 503)


@router.get("/role-permissions")
async def list_role_permissions(
    request: Request,
    role_id: Optional[int] = Query(None),
    resource_id: Optional[int] = Query(None),
    current_user: CurrentV2User = Depends(require_v2_super_auth),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    rid = request_id(request)
    filters = ["1 = 1"]
    params: Dict[str, Any] = {}
    if role_id is not None:
        filters.append("rp.role_id = :role_id")
        params["role_id"] = int(role_id)
    if resource_id is not None:
        filters.append("rp.resource_id = :resource_id")
        params["resource_id"] = int(resource_id)

    try:
        result = await central_db.execute(
            text(
                f"""
                SELECT
                    rp.id,
                    rp.role_id,
                    rr.code AS role_code,
                    rr.name AS role_name,
                    rp.resource_id,
                    res.code AS resource_code,
                    res.name AS resource_name,
                    rp.can_view, rp.can_add, rp.can_edit, rp.can_delete, rp.can_super,
                    rp.is_active,
                    rp.modified_at
                FROM rbac_role_permission_v2 rp
                JOIN rbac_role rr ON rr.id = rp.role_id
                JOIN rbac_resource_v2 res ON res.id = rp.resource_id
                WHERE {" AND ".join(filters)}
                ORDER BY rr.code ASC, res.code ASC
                """
            ),
            params,
        )
        rows = []
        for row in result.fetchall():
            row_map = row._mapping
            rows.append(
                {
                    "id": int(row_map["id"]),
                    "role_id": int(row_map["role_id"]),
                    "role_code": str(row_map.get("role_code") or ""),
                    "role_name": str(row_map.get("role_name") or ""),
                    "resource_id": int(row_map["resource_id"]),
                    "resource_code": str(row_map.get("resource_code") or ""),
                    "resource_name": str(row_map.get("resource_name") or ""),
                    "can_view": _normalize_flag(row_map.get("can_view")),
                    "can_add": _normalize_flag(row_map.get("can_add")),
                    "can_edit": _normalize_flag(row_map.get("can_edit")),
                    "can_delete": _normalize_flag(row_map.get("can_delete")),
                    "can_super": _normalize_flag(row_map.get("can_super")),
                    "is_active": _normalize_flag(row_map.get("is_active")),
                    "modified_at": _epoch_seconds(row_map.get("modified_at")),
                }
            )
        _admin_log(
            request_id_value=rid,
            current_user=current_user,
            endpoint="/role-permissions",
            operation="list",
            target={"role_id": role_id, "resource_id": resource_id},
            before=None,
            after={"count": len(rows)},
            result="success",
        )
        return success_json_response({"items": rows}, request_id_value=rid, message="Role permissions fetched")
    except Exception:
        raise AuthV2Error(AUTH_SERVICE_UNAVAILABLE, "Auth v2 service unavailable", 503)


@router.put("/role-permissions")
async def put_role_permissions(
    payload: RolePermissionPutRequest,
    request: Request,
    current_user: CurrentV2User = Depends(require_v2_super_auth),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    rid = request_id(request)
    if payload.expected_modified_at is None:
        raise AuthV2Error(AUTH_BAD_REQUEST, "expected_modified_at is required", 400)

    try:
        if not await _role_exists(central_db, payload.role_id):
            raise AuthV2Error(AUTH_BAD_REQUEST, "Role not found", 400)
        resource_code = await _resource_code_for_id(central_db, payload.resource_id)
        if not resource_code:
            raise AuthV2Error(AUTH_BAD_REQUEST, "Resource not found", 400)
        if _normalize_flag(payload.can_super) == 1 and resource_code != GLOBAL_RESOURCE_CODE:
            raise AuthV2Error(AUTH_BAD_REQUEST, "can_super is only valid for global resource", 400)

        actor_create = _actor_fields(current_user)
        actor_modify = _modified_actor_fields(current_user)
        async with central_db.begin():
            existing_result = await central_db.execute(
                text(
                    """
                    SELECT id, role_id, resource_id, can_view, can_add, can_edit, can_delete, can_super, is_active, modified_at
                    FROM rbac_role_permission_v2
                    WHERE role_id = :role_id
                      AND resource_id = :resource_id
                    LIMIT 1
                    FOR UPDATE
                    """
                ),
                {"role_id": int(payload.role_id), "resource_id": int(payload.resource_id)},
            )
            existing_row = existing_result.fetchone()
            now = datetime.utcnow()

            if existing_row is None:
                if int(payload.expected_modified_at) != 0:
                    raise AuthV2Error(AUTH_BAD_REQUEST, "Version conflict", 409, details={"actual_modified_at": 0})
                await central_db.execute(
                    text(
                        """
                        INSERT INTO rbac_role_permission_v2 (
                            role_id, resource_id, can_view, can_add, can_edit, can_delete, can_super, is_active,
                            created_at, modified_at,
                            created_by_user_id, modified_by_user_id,
                            created_by_employee_id, modified_by_employee_id
                        ) VALUES (
                            :role_id, :resource_id, :can_view, :can_add, :can_edit, :can_delete, :can_super, :is_active,
                            :created_at, :modified_at,
                            :created_by_user_id, :modified_by_user_id,
                            :created_by_employee_id, :modified_by_employee_id
                        )
                        """
                    ),
                    {
                        "role_id": int(payload.role_id),
                        "resource_id": int(payload.resource_id),
                        "can_view": _normalize_flag(payload.can_view),
                        "can_add": _normalize_flag(payload.can_add),
                        "can_edit": _normalize_flag(payload.can_edit),
                        "can_delete": _normalize_flag(payload.can_delete),
                        "can_super": _normalize_flag(payload.can_super),
                        "is_active": _normalize_flag(payload.is_active),
                        "created_at": now,
                        "modified_at": now,
                        **actor_create,
                    },
                )
                before = None
            else:
                existing = dict(existing_row._mapping)
                before = {
                    "id": int(existing["id"]),
                    "role_id": int(existing["role_id"]),
                    "resource_id": int(existing["resource_id"]),
                    "can_view": _normalize_flag(existing.get("can_view")),
                    "can_add": _normalize_flag(existing.get("can_add")),
                    "can_edit": _normalize_flag(existing.get("can_edit")),
                    "can_delete": _normalize_flag(existing.get("can_delete")),
                    "can_super": _normalize_flag(existing.get("can_super")),
                    "is_active": _normalize_flag(existing.get("is_active")),
                    "modified_at": _epoch_seconds(existing.get("modified_at")),
                }
                await _require_expected_match(payload.expected_modified_at, existing)
                await central_db.execute(
                    text(
                        """
                        UPDATE rbac_role_permission_v2
                        SET can_view = :can_view,
                            can_add = :can_add,
                            can_edit = :can_edit,
                            can_delete = :can_delete,
                            can_super = :can_super,
                            is_active = :is_active,
                            modified_at = :modified_at,
                            modified_by_user_id = :modified_by_user_id,
                            modified_by_employee_id = :modified_by_employee_id
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": int(existing["id"]),
                        "can_view": _normalize_flag(payload.can_view),
                        "can_add": _normalize_flag(payload.can_add),
                        "can_edit": _normalize_flag(payload.can_edit),
                        "can_delete": _normalize_flag(payload.can_delete),
                        "can_super": _normalize_flag(payload.can_super),
                        "is_active": _normalize_flag(payload.is_active),
                        "modified_at": now,
                        **actor_modify,
                    },
                )

            result = await central_db.execute(
                text(
                    """
                    SELECT id, role_id, resource_id, can_view, can_add, can_edit, can_delete, can_super, is_active, modified_at
                    FROM rbac_role_permission_v2
                    WHERE role_id = :role_id
                      AND resource_id = :resource_id
                    LIMIT 1
                    """
                ),
                {"role_id": int(payload.role_id), "resource_id": int(payload.resource_id)},
            )
            updated_row = result.fetchone()
            if updated_row is None:
                raise AuthV2Error(AUTH_BAD_REQUEST, "Failed to upsert role permission", 500)
            updated = dict(updated_row._mapping)
            after = {
                "id": int(updated["id"]),
                "role_id": int(updated["role_id"]),
                "resource_id": int(updated["resource_id"]),
                "can_view": _normalize_flag(updated.get("can_view")),
                "can_add": _normalize_flag(updated.get("can_add")),
                "can_edit": _normalize_flag(updated.get("can_edit")),
                "can_delete": _normalize_flag(updated.get("can_delete")),
                "can_super": _normalize_flag(updated.get("can_super")),
                "is_active": _normalize_flag(updated.get("is_active")),
                "modified_at": _epoch_seconds(updated.get("modified_at")),
            }

        _admin_log(
            request_id_value=rid,
            current_user=current_user,
            endpoint="/role-permissions",
            operation="put",
            target={"role_id": int(payload.role_id), "resource_id": int(payload.resource_id)},
            before=before,
            after=after,
            result="success",
        )
        return success_json_response({"item": after}, request_id_value=rid, message="Role permission upserted")
    except AuthV2Error:
        raise
    except Exception:
        raise AuthV2Error(AUTH_SERVICE_UNAVAILABLE, "Auth v2 service unavailable", 503)


@router.delete("/role-permissions")
async def delete_role_permissions(
    payload: RolePermissionDeleteRequest,
    request: Request,
    current_user: CurrentV2User = Depends(require_v2_super_auth),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    rid = request_id(request)
    if payload.expected_modified_at is None:
        raise AuthV2Error(AUTH_BAD_REQUEST, "expected_modified_at is required", 400)

    try:
        if not await _role_exists(central_db, payload.role_id):
            raise AuthV2Error(AUTH_BAD_REQUEST, "Role not found", 400)
        if not await _master_id_exists(central_db, table="employee_position", row_id=payload.position_id):
            raise AuthV2Error(AUTH_BAD_REQUEST, "Position not found", 400)
        if not await _master_id_exists(central_db, table="employee_department", row_id=payload.department_id):
            raise AuthV2Error(AUTH_BAD_REQUEST, "Department not found", 400)

        async with central_db.begin():
            result = await central_db.execute(
                text(
                    """
                    SELECT id, role_id, resource_id, is_active, modified_at
                    FROM rbac_role_permission_v2
                    WHERE role_id = :role_id
                      AND resource_id = :resource_id
                    LIMIT 1
                    FOR UPDATE
                    """
                ),
                {"role_id": int(payload.role_id), "resource_id": int(payload.resource_id)},
            )
            row = result.fetchone()
            if row is None:
                raise AuthV2Error(AUTH_BAD_REQUEST, "Role permission not found", 404)
            existing = dict(row._mapping)
            before = {
                "id": int(existing["id"]),
                "role_id": int(existing["role_id"]),
                "resource_id": int(existing["resource_id"]),
                "is_active": _normalize_flag(existing.get("is_active")),
                "modified_at": _epoch_seconds(existing.get("modified_at")),
            }
            await _require_expected_match(payload.expected_modified_at, existing)
            now = datetime.utcnow()
            await central_db.execute(
                text(
                    """
                    UPDATE rbac_role_permission_v2
                    SET is_active = 0,
                        modified_at = :modified_at,
                        modified_by_user_id = :modified_by_user_id,
                        modified_by_employee_id = :modified_by_employee_id
                    WHERE id = :id
                    """
                ),
                {"id": int(existing["id"]), "modified_at": now, **_modified_actor_fields(current_user)},
            )
            after = {**before, "is_active": 0, "modified_at": int(now.replace(tzinfo=timezone.utc).timestamp())}

        _admin_log(
            request_id_value=rid,
            current_user=current_user,
            endpoint="/role-permissions",
            operation="delete",
            target={"role_id": int(payload.role_id), "resource_id": int(payload.resource_id)},
            before=before,
            after=after,
            result="success",
        )
        return success_json_response({"item": after}, request_id_value=rid, message="Role permission disabled")
    except AuthV2Error:
        raise
    except Exception:
        raise AuthV2Error(AUTH_SERVICE_UNAVAILABLE, "Auth v2 service unavailable", 503)


@router.get("/position-department-roles")
async def list_position_department_roles(
    request: Request,
    include_inactive: bool = Query(True),
    current_user: CurrentV2User = Depends(require_v2_super_auth),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    rid = request_id(request)
    where_clause = "" if include_inactive else "WHERE pdr.is_active = 1"
    try:
        result = await central_db.execute(
            text(
                f"""
                SELECT
                    pdr.id,
                    pdr.position_id,
                    ep.position AS position,
                    pdr.department_id,
                    ed.department AS department,
                    pdr.role_id,
                    rr.code AS role_code,
                    rr.name AS role_name,
                    pdr.is_active,
                    pdr.modified_at
                FROM rbac_position_department_role_v2 pdr
                JOIN rbac_role rr ON rr.id = pdr.role_id
                LEFT JOIN employee_position ep ON ep.id = pdr.position_id
                LEFT JOIN employee_department ed ON ed.id = pdr.department_id
                {where_clause}
                ORDER BY pdr.position_id ASC, pdr.department_id ASC, rr.code ASC
                """
            )
        )
        rows = []
        for row in result.fetchall():
            row_map = row._mapping
            rows.append(
                {
                    "id": int(row_map["id"]),
                    "position_id": int(row_map["position_id"]),
                    "position": row_map.get("position"),
                    "department_id": int(row_map["department_id"]),
                    "department": row_map.get("department"),
                    "role_id": int(row_map["role_id"]),
                    "role_code": str(row_map.get("role_code") or ""),
                    "role_name": str(row_map.get("role_name") or ""),
                    "is_active": _normalize_flag(row_map.get("is_active")),
                    "modified_at": _epoch_seconds(row_map.get("modified_at")),
                }
            )
        _admin_log(
            request_id_value=rid,
            current_user=current_user,
            endpoint="/position-department-roles",
            operation="list",
            target={"include_inactive": bool(include_inactive)},
            before=None,
            after={"count": len(rows)},
            result="success",
        )
        return success_json_response({"items": rows}, request_id_value=rid, message="Position-department roles fetched")
    except Exception:
        raise AuthV2Error(AUTH_SERVICE_UNAVAILABLE, "Auth v2 service unavailable", 503)


@router.put("/position-department-roles")
async def put_position_department_roles(
    payload: PairRolePutRequest,
    request: Request,
    current_user: CurrentV2User = Depends(require_v2_super_auth),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    rid = request_id(request)
    if payload.expected_modified_at is None:
        raise AuthV2Error(AUTH_BAD_REQUEST, "expected_modified_at is required", 400)

    try:
        async with central_db.begin():
            result = await central_db.execute(
                text(
                    """
                    SELECT id, position_id, department_id, role_id, is_active, modified_at
                    FROM rbac_position_department_role_v2
                    WHERE position_id = :position_id
                      AND department_id = :department_id
                      AND role_id = :role_id
                    LIMIT 1
                    FOR UPDATE
                    """
                ),
                {
                    "position_id": int(payload.position_id),
                    "department_id": int(payload.department_id),
                    "role_id": int(payload.role_id),
                },
            )
            row = result.fetchone()
            now = datetime.utcnow()
            if row is None:
                if int(payload.expected_modified_at) != 0:
                    raise AuthV2Error(AUTH_BAD_REQUEST, "Version conflict", 409, details={"actual_modified_at": 0})
                await central_db.execute(
                    text(
                        """
                        INSERT INTO rbac_position_department_role_v2 (
                            position_id, department_id, role_id, is_active,
                            created_at, modified_at,
                            created_by_user_id, modified_by_user_id,
                            created_by_employee_id, modified_by_employee_id
                        ) VALUES (
                            :position_id, :department_id, :role_id, :is_active,
                            :created_at, :modified_at,
                            :created_by_user_id, :modified_by_user_id,
                            :created_by_employee_id, :modified_by_employee_id
                        )
                        """
                    ),
                    {
                        "position_id": int(payload.position_id),
                        "department_id": int(payload.department_id),
                        "role_id": int(payload.role_id),
                        "is_active": _normalize_flag(payload.is_active),
                        "created_at": now,
                        "modified_at": now,
                        **_actor_fields(current_user),
                    },
                )
                before = None
            else:
                existing = dict(row._mapping)
                before = {
                    "id": int(existing["id"]),
                    "position_id": int(existing["position_id"]),
                    "department_id": int(existing["department_id"]),
                    "role_id": int(existing["role_id"]),
                    "is_active": _normalize_flag(existing.get("is_active")),
                    "modified_at": _epoch_seconds(existing.get("modified_at")),
                }
                await _require_expected_match(payload.expected_modified_at, existing)
                await central_db.execute(
                    text(
                        """
                        UPDATE rbac_position_department_role_v2
                        SET is_active = :is_active,
                            modified_at = :modified_at,
                            modified_by_user_id = :modified_by_user_id,
                            modified_by_employee_id = :modified_by_employee_id
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": int(existing["id"]),
                        "is_active": _normalize_flag(payload.is_active),
                        "modified_at": now,
                        **_modified_actor_fields(current_user),
                    },
                )

            refreshed_result = await central_db.execute(
                text(
                    """
                    SELECT id, position_id, department_id, role_id, is_active, modified_at
                    FROM rbac_position_department_role_v2
                    WHERE position_id = :position_id
                      AND department_id = :department_id
                      AND role_id = :role_id
                    LIMIT 1
                    """
                ),
                {
                    "position_id": int(payload.position_id),
                    "department_id": int(payload.department_id),
                    "role_id": int(payload.role_id),
                },
            )
            refreshed = refreshed_result.fetchone()
            if refreshed is None:
                raise AuthV2Error(AUTH_BAD_REQUEST, "Failed to upsert mapping", 500)
            updated = dict(refreshed._mapping)
            after = {
                "id": int(updated["id"]),
                "position_id": int(updated["position_id"]),
                "department_id": int(updated["department_id"]),
                "role_id": int(updated["role_id"]),
                "is_active": _normalize_flag(updated.get("is_active")),
                "modified_at": _epoch_seconds(updated.get("modified_at")),
            }

        _admin_log(
            request_id_value=rid,
            current_user=current_user,
            endpoint="/position-department-roles",
            operation="put",
            target={
                "position_id": int(payload.position_id),
                "department_id": int(payload.department_id),
                "role_id": int(payload.role_id),
            },
            before=before,
            after=after,
            result="success",
        )
        return success_json_response({"item": after}, request_id_value=rid, message="Mapping upserted")
    except AuthV2Error:
        raise
    except Exception:
        raise AuthV2Error(AUTH_SERVICE_UNAVAILABLE, "Auth v2 service unavailable", 503)


@router.delete("/position-department-roles/{mapping_id}")
async def delete_position_department_role(
    mapping_id: int,
    request: Request,
    expected_modified_at: Optional[int] = Query(None),
    current_user: CurrentV2User = Depends(require_v2_super_auth),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    rid = request_id(request)
    if expected_modified_at is None:
        raise AuthV2Error(AUTH_BAD_REQUEST, "expected_modified_at is required", 400)
    try:
        async with central_db.begin():
            result = await central_db.execute(
                text(
                    """
                    SELECT id, position_id, department_id, role_id, is_active, modified_at
                    FROM rbac_position_department_role_v2
                    WHERE id = :mapping_id
                    LIMIT 1
                    FOR UPDATE
                    """
                ),
                {"mapping_id": int(mapping_id)},
            )
            row = result.fetchone()
            if row is None:
                raise AuthV2Error(AUTH_BAD_REQUEST, "Mapping not found", 404)
            existing = dict(row._mapping)
            before = {
                "id": int(existing["id"]),
                "position_id": int(existing["position_id"]),
                "department_id": int(existing["department_id"]),
                "role_id": int(existing["role_id"]),
                "is_active": _normalize_flag(existing.get("is_active")),
                "modified_at": _epoch_seconds(existing.get("modified_at")),
            }
            await _require_expected_match(expected_modified_at, existing)
            now = datetime.utcnow()
            await central_db.execute(
                text(
                    """
                    UPDATE rbac_position_department_role_v2
                    SET is_active = 0,
                        modified_at = :modified_at,
                        modified_by_user_id = :modified_by_user_id,
                        modified_by_employee_id = :modified_by_employee_id
                    WHERE id = :id
                    """
                ),
                {"id": int(mapping_id), "modified_at": now, **_modified_actor_fields(current_user)},
            )
            after = {**before, "is_active": 0, "modified_at": int(now.replace(tzinfo=timezone.utc).timestamp())}

        _admin_log(
            request_id_value=rid,
            current_user=current_user,
            endpoint=f"/position-department-roles/{mapping_id}",
            operation="delete",
            target={"id": int(mapping_id)},
            before=before,
            after=after,
            result="success",
        )
        return success_json_response({"item": after}, request_id_value=rid, message="Mapping disabled")
    except AuthV2Error:
        raise
    except Exception:
        raise AuthV2Error(AUTH_SERVICE_UNAVAILABLE, "Auth v2 service unavailable", 503)


@router.get("/effective/{employee_id}")
async def get_effective_authorization(
    employee_id: int,
    request: Request,
    current_user: CurrentV2User = Depends(require_v2_super_auth),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    rid = request_id(request)
    try:
        resolved = await AuthorizationResolver(main_db, central_db).resolve_employee_authorization(int(employee_id))
        _admin_log(
            request_id_value=rid,
            current_user=current_user,
            endpoint=f"/effective/{employee_id}",
            operation="resolve",
            target={"employee_id": int(employee_id)},
            before=None,
            after={
                "role_count": len(resolved.get("roles", [])),
                "permission_count": len(resolved.get("permissions", [])),
                "is_super": bool(resolved.get("is_super", False)),
            },
            result="success",
        )
        return success_json_response({"effective": resolved}, request_id_value=rid, message="Effective authz resolved")
    except AuthV2Error:
        raise
    except Exception:
        raise AuthV2Error(AUTH_SERVICE_UNAVAILABLE, "Auth v2 service unavailable", 503)
