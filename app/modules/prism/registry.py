"""PRISM — Resource & Action Registry
Catalog management for resource types and their allowed actions.
Drives UI permission tree dropdowns and policy statement builder.

Routes:
  GET    /prism/registry/resources           list all resource types (tree-ready)
  POST   /prism/registry/resources           register a new resource type
  PATCH  /prism/registry/resources/{code}    update a resource type
  DELETE /prism/registry/resources/{code}    deactivate a resource type

  GET    /prism/registry/actions             list all actions (optionally filtered by resource)
  POST   /prism/registry/actions             register a new action
  PATCH  /prism/registry/actions/{code}      update an action
  DELETE /prism/registry/actions/{code}      deactivate an action
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.database import central_session_context

router = APIRouter(prefix="/prism/registry", tags=["PRISM — Registry"])


# ── Schemas ────────────────────────────────────────────────────────────────

class ResourceCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=128, description="Dot-notation code, e.g. 'reports.top_summary'")
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = None
    parent_code: Optional[str] = Field(None, max_length=128)
    sort_order: int = Field(10, ge=0)


class ResourceUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=128)
    description: Optional[str] = None
    parent_code: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class ActionCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=128, description="Format: '{resource_code}:{verb}', e.g. 'employee:read'")
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = None
    resource_code: str = Field(..., max_length=128)
    sort_order: int = Field(10, ge=0)


class ActionUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=128)
    description: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


# ── Helper ─────────────────────────────────────────────────────────────────

def _row(result) -> Optional[dict]:
    row = result.fetchone()
    return dict(row._mapping) if row else None


def _rows(result) -> list[dict]:
    return [dict(r._mapping) for r in result.fetchall()]


# ── Resource Registry ──────────────────────────────────────────────────────

@router.get("/resources")
async def list_resources(active_only: bool = Query(True)):
    """List all registered resource types, ordered for UI tree rendering."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        where = "WHERE is_active = 1" if active_only else ""
        resources = _rows(await db.execute(
            text(
                f"SELECT id, code, name, description, parent_code, sort_order, is_active, created_at "
                f"FROM prism_resource_registry {where} "
                f"ORDER BY (parent_code IS NOT NULL), sort_order, code"
            )
        ))
    return {"resources": resources, "total": len(resources)}


@router.post("/resources", status_code=201)
async def register_resource(payload: ResourceCreate):
    """Register a new resource type in the catalog."""
    # TODO: Guard — require supreme user session
    # Validate parent exists if specified
    async with central_session_context() as db:
        if payload.parent_code:
            parent = _row(await db.execute(
                text("SELECT code FROM prism_resource_registry WHERE code = :code AND is_active = 1"),
                {"code": payload.parent_code},
            ))
            if not parent:
                raise HTTPException(
                    status_code=400,
                    detail=f"Parent resource '{payload.parent_code}' does not exist or is inactive",
                )

        existing = _row(await db.execute(
            text("SELECT code FROM prism_resource_registry WHERE code = :code"),
            {"code": payload.code},
        ))
        if existing:
            raise HTTPException(status_code=409, detail=f"Resource code '{payload.code}' already registered")

        result = await db.execute(
            text(
                "INSERT INTO prism_resource_registry (code, name, description, parent_code, sort_order, is_active) "
                "VALUES (:code, :name, :description, :parent_code, :sort_order, 1)"
            ),
            {
                "code": payload.code,
                "name": payload.name,
                "description": payload.description,
                "parent_code": payload.parent_code,
                "sort_order": payload.sort_order,
            },
        )
        await db.commit()
        new_id = result.lastrowid

    return {"id": new_id, "code": payload.code, "name": payload.name}


@router.patch("/resources/{code}")
async def update_resource(code: str, payload: ResourceUpdate):
    """Update a registered resource type."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        existing = _row(await db.execute(
            text("SELECT id FROM prism_resource_registry WHERE code = :code"),
            {"code": code},
        ))
        if not existing:
            raise HTTPException(status_code=404, detail="Resource not found")

        updates: dict = {}
        if payload.name is not None:
            updates["name"] = payload.name
        if payload.description is not None:
            updates["description"] = payload.description
        if payload.parent_code is not None:
            updates["parent_code"] = payload.parent_code
        if payload.sort_order is not None:
            updates["sort_order"] = payload.sort_order
        if payload.is_active is not None:
            updates["is_active"] = int(payload.is_active)

        if not updates:
            raise HTTPException(status_code=400, detail="Nothing to update")

        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["code"] = code
        await db.execute(
            text(f"UPDATE prism_resource_registry SET {set_clause} WHERE code = :code"),
            updates,
        )
        await db.commit()

    return {"updated": True, "code": code}


@router.delete("/resources/{code}")
async def deactivate_resource(code: str):
    """Deactivate a resource type (soft delete)."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        existing = _row(await db.execute(
            text("SELECT id FROM prism_resource_registry WHERE code = :code"),
            {"code": code},
        ))
        if not existing:
            raise HTTPException(status_code=404, detail="Resource not found")

        await db.execute(
            text("UPDATE prism_resource_registry SET is_active = 0 WHERE code = :code"),
            {"code": code},
        )
        await db.commit()

    return {"deactivated": True, "code": code}


# ── Action Registry ────────────────────────────────────────────────────────

@router.get("/actions")
async def list_actions(
    resource_code: Optional[str] = Query(None, description="Filter by resource code"),
    active_only: bool = Query(True),
):
    """List all registered actions, optionally filtered by resource."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        where = "WHERE 1=1"
        params: dict = {}
        if active_only:
            where += " AND is_active = 1"
        if resource_code:
            where += " AND resource_code = :resource_code"
            params["resource_code"] = resource_code

        actions = _rows(await db.execute(
            text(
                f"SELECT id, code, name, description, resource_code, sort_order, is_active, created_at "
                f"FROM prism_action_registry {where} ORDER BY resource_code, sort_order, code"
            ),
            params,
        ))
    return {"actions": actions, "total": len(actions)}


@router.post("/actions", status_code=201)
async def register_action(payload: ActionCreate):
    """Register a new action for a resource type."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        # Validate resource exists
        resource = _row(await db.execute(
            text("SELECT code FROM prism_resource_registry WHERE code = :code AND is_active = 1"),
            {"code": payload.resource_code},
        ))
        if not resource:
            raise HTTPException(
                status_code=400,
                detail=f"Resource '{payload.resource_code}' does not exist or is inactive",
            )

        existing = _row(await db.execute(
            text("SELECT code FROM prism_action_registry WHERE code = :code"),
            {"code": payload.code},
        ))
        if existing:
            raise HTTPException(status_code=409, detail=f"Action code '{payload.code}' already registered")

        result = await db.execute(
            text(
                "INSERT INTO prism_action_registry (code, name, description, resource_code, sort_order, is_active) "
                "VALUES (:code, :name, :description, :resource_code, :sort_order, 1)"
            ),
            {
                "code": payload.code,
                "name": payload.name,
                "description": payload.description,
                "resource_code": payload.resource_code,
                "sort_order": payload.sort_order,
            },
        )
        await db.commit()
        new_id = result.lastrowid

    return {"id": new_id, "code": payload.code, "resource_code": payload.resource_code}


@router.patch("/actions/{code}")
async def update_action(code: str, payload: ActionUpdate):
    """Update a registered action."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        existing = _row(await db.execute(
            text("SELECT id FROM prism_action_registry WHERE code = :code"),
            {"code": code},
        ))
        if not existing:
            raise HTTPException(status_code=404, detail="Action not found")

        updates: dict = {}
        if payload.name is not None:
            updates["name"] = payload.name
        if payload.description is not None:
            updates["description"] = payload.description
        if payload.sort_order is not None:
            updates["sort_order"] = payload.sort_order
        if payload.is_active is not None:
            updates["is_active"] = int(payload.is_active)

        if not updates:
            raise HTTPException(status_code=400, detail="Nothing to update")

        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["code"] = code
        await db.execute(
            text(f"UPDATE prism_action_registry SET {set_clause} WHERE code = :code"),
            updates,
        )
        await db.commit()

    return {"updated": True, "code": code}


@router.delete("/actions/{code}")
async def deactivate_action(code: str):
    """Deactivate an action (soft delete)."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        existing = _row(await db.execute(
            text("SELECT id FROM prism_action_registry WHERE code = :code"),
            {"code": code},
        ))
        if not existing:
            raise HTTPException(status_code=404, detail="Action not found")

        await db.execute(
            text("UPDATE prism_action_registry SET is_active = 0 WHERE code = :code"),
            {"code": code},
        )
        await db.commit()

    return {"deactivated": True, "code": code}

