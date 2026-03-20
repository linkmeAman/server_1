"""PRISM — Role Registry
CRUD for roles.  All mutations require a supreme-user session.

Routes:
  GET    /prism/roles             list all roles (active by default)
  POST   /prism/roles             create a new custom role
  GET    /prism/roles/{id}        get role detail + attached policy list
  PATCH  /prism/roles/{id}        update role name / description
  DELETE /prism/roles/{id}        deactivate a role (soft delete; system roles blocked)
"""

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from core.database_v2 import central_session_context

router = APIRouter(prefix="/prism/roles", tags=["PRISM — Roles"])


# ── Pydantic schemas ───────────────────────────────────────────────────────

class RoleCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=128)
    description: Optional[str] = None


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=128)
    description: Optional[str] = None
    is_active: Optional[bool] = None


# ── Helper ─────────────────────────────────────────────────────────────────

def _row(result) -> Optional[dict]:
    row = result.fetchone()
    return dict(row._mapping) if row else None


def _rows(result) -> list[dict]:
    return [dict(r._mapping) for r in result.fetchall()]


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("")
async def list_roles(
    active_only: bool = Query(True, description="Return only active roles"),
    type: Optional[str] = Query(None, description="Filter by type: system | custom"),
):
    """List all roles.  Defaults to active roles only."""
    # TODO: Guard — require supreme user session (add auth dependency here)
    async with central_session_context() as db:
        where = "WHERE 1=1"
        params: dict = {}
        if active_only:
            where += " AND is_active = 1"
        if type:
            if type not in ("system", "custom"):
                raise HTTPException(status_code=400, detail="type must be 'system' or 'custom'")
            where += " AND type = :type"
            params["type"] = type

        result = await db.execute(
            text(f"SELECT id, name, description, type, is_active, created_at, modified_at FROM prism_roles {where} ORDER BY type, name"),
            params,
        )
        roles = _rows(result)

    return {"roles": roles, "total": len(roles)}


@router.post("", status_code=201)
async def create_role(payload: RoleCreate):
    """Create a new custom role."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        # Duplicate name check
        existing = _row(await db.execute(
            text("SELECT id FROM prism_roles WHERE name = :name"),
            {"name": payload.name},
        ))
        if existing:
            raise HTTPException(status_code=409, detail=f"Role '{payload.name}' already exists")

        result = await db.execute(
            text(
                "INSERT INTO prism_roles (name, description, type, is_active) "
                "VALUES (:name, :description, 'custom', 1)"
            ),
            {"name": payload.name, "description": payload.description},
        )
        await db.commit()
        new_id = result.lastrowid

    return {"id": new_id, "name": payload.name, "type": "custom"}


@router.get("/{role_id}")
async def get_role(role_id: int):
    """Get role detail including attached policies."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        role = _row(await db.execute(
            text("SELECT id, name, description, type, is_active, created_at, modified_at FROM prism_roles WHERE id = :id"),
            {"id": role_id},
        ))
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")

        # Attached policies
        policies = _rows(await db.execute(
            text(
                "SELECT p.id, p.name, p.type, p.is_active, rp.attached_at "
                "FROM prism_role_policies rp "
                "JOIN prism_policies p ON p.id = rp.policy_id "
                "WHERE rp.role_id = :role_id "
                "ORDER BY p.name"
            ),
            {"role_id": role_id},
        ))

    role["policies"] = policies
    return role


@router.patch("/{role_id}")
async def update_role(role_id: int, payload: RoleUpdate):
    """Update role name, description, or active status.  System roles cannot be renamed."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        role = _row(await db.execute(
            text("SELECT id, type FROM prism_roles WHERE id = :id"),
            {"id": role_id},
        ))
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        if role["type"] == "system" and payload.name:
            raise HTTPException(status_code=403, detail="System role names cannot be changed")

        updates: dict = {}
        if payload.name is not None:
            updates["name"] = payload.name
        if payload.description is not None:
            updates["description"] = payload.description
        if payload.is_active is not None:
            updates["is_active"] = int(payload.is_active)

        if not updates:
            raise HTTPException(status_code=400, detail="Nothing to update")

        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["id"] = role_id
        await db.execute(text(f"UPDATE prism_roles SET {set_clause} WHERE id = :id"), updates)
        await db.commit()

    return {"updated": True, "id": role_id}


@router.delete("/{role_id}")
async def deactivate_role(role_id: int):
    """Deactivate a role (soft delete).  System roles cannot be deactivated."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        role = _row(await db.execute(
            text("SELECT id, type FROM prism_roles WHERE id = :id"),
            {"id": role_id},
        ))
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        if role["type"] == "system":
            raise HTTPException(status_code=403, detail="System roles cannot be deactivated")

        await db.execute(
            text("UPDATE prism_roles SET is_active = 0 WHERE id = :id"),
            {"id": role_id},
        )
        await db.commit()

    return {"deactivated": True, "id": role_id}
