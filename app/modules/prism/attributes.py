"""PRISM — ABAC Attribute Management
Manage user-level and resource-level ABAC attributes used by the PDP
for condition evaluation.

user:* attributes  → sourced from employee_table sync at login + manual overrides
resource:* attrs   → set per resource instance, evaluated at runtime (never cached)

Routes:
  GET    /prism/attributes/users/{user_id}                       list all user attributes
  POST   /prism/attributes/users                                 set (upsert) a user attribute
  DELETE /prism/attributes/users/{user_id}/{key}                 delete a user attribute

  GET    /prism/attributes/resources/{resource_type}/{resource_id}   list resource attributes
  POST   /prism/attributes/resources                                  set (upsert) a resource attribute
  DELETE /prism/attributes/resources/{resource_type}/{resource_id}/{key}  delete a resource attribute

  POST   /prism/attributes/users/{user_id}/sync-from-employee    sync employee_table attrs (triggers re-sync)
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.database import central_session_context
from app.core.prism_cache import load_employee_sync_attributes

router = APIRouter(prefix="/prism/attributes", tags=["PRISM — ABAC Attributes"])


# ── Schemas ────────────────────────────────────────────────────────────────

VALID_USER_ATTR_SOURCES = ("manual", "derived", "employee_table")


class UserAttributeSet(BaseModel):
    user_id: int
    key: str = Field(..., min_length=1, max_length=128)
    value: str = Field(..., max_length=512)
    source: str = Field("manual", description="manual | derived | employee_table")


class ResourceAttributeSet(BaseModel):
    resource_type: str = Field(..., min_length=1, max_length=64)
    resource_id: str = Field(..., min_length=1, max_length=64)
    key: str = Field(..., min_length=1, max_length=128)
    value: str = Field(..., max_length=512)


# ── Helper ─────────────────────────────────────────────────────────────────

def _row(result) -> Optional[dict]:
    row = result.fetchone()
    return dict(row._mapping) if row else None


def _rows(result) -> list[dict]:
    return [dict(r._mapping) for r in result.fetchall()]


# ── User Attribute Endpoints ───────────────────────────────────────────────

@router.get("/users/{user_id}")
async def get_user_attributes(user_id: int, source: Optional[str] = None):
    """List all ABAC attributes for a user, optionally filtered by source."""
    # TODO: Guard — require supreme user session
    if source and source not in VALID_USER_ATTR_SOURCES:
        raise HTTPException(status_code=400, detail=f"source must be one of {VALID_USER_ATTR_SOURCES}")

    async with central_session_context() as db:
        where = "WHERE user_id = :user_id"
        params: dict = {"user_id": user_id}
        if source:
            where += " AND source = :source"
            params["source"] = source

        attrs = _rows(await db.execute(
            text(f"SELECT id, user_id, `key`, value, source, updated_at FROM prism_user_attributes {where} ORDER BY `key`"),
            params,
        ))

    return {"user_id": user_id, "attributes": attrs, "total": len(attrs)}


@router.post("/users", status_code=201)
async def set_user_attribute(payload: UserAttributeSet):
    """Set (upsert) a single ABAC attribute on a user."""
    # TODO: Guard — require supreme user session
    if payload.source not in VALID_USER_ATTR_SOURCES:
        raise HTTPException(status_code=400, detail=f"source must be one of {VALID_USER_ATTR_SOURCES}")

    async with central_session_context() as db:
        existing = _row(await db.execute(
            text("SELECT id FROM prism_user_attributes WHERE user_id = :uid AND `key` = :key"),
            {"uid": payload.user_id, "key": payload.key},
        ))
        if existing:
            await db.execute(
                text(
                    "UPDATE prism_user_attributes SET value = :value, source = :source, updated_at = NOW() "
                    "WHERE user_id = :uid AND `key` = :key"
                ),
                {"value": payload.value, "source": payload.source, "uid": payload.user_id, "key": payload.key},
            )
            attr_id = existing["id"]
        else:
            result = await db.execute(
                text(
                    "INSERT INTO prism_user_attributes (user_id, `key`, value, source) "
                    "VALUES (:user_id, :key, :value, :source)"
                ),
                {"user_id": payload.user_id, "key": payload.key, "value": payload.value, "source": payload.source},
            )
            attr_id = result.lastrowid
        await db.commit()

    return {"id": attr_id, "user_id": payload.user_id, "key": payload.key, "upserted": True}


@router.delete("/users/{user_id}/{key}")
async def delete_user_attribute(user_id: int, key: str):
    """Delete a specific user attribute by key."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        existing = _row(await db.execute(
            text("SELECT id FROM prism_user_attributes WHERE user_id = :uid AND `key` = :key"),
            {"uid": user_id, "key": key},
        ))
        if not existing:
            raise HTTPException(status_code=404, detail="Attribute not found")

        await db.execute(
            text("DELETE FROM prism_user_attributes WHERE user_id = :uid AND `key` = :key"),
            {"uid": user_id, "key": key},
        )
        await db.commit()

    return {"deleted": True, "user_id": user_id, "key": key}


@router.post("/users/{user_id}/sync-from-employee")
async def sync_user_attributes_from_employee(user_id: int, contact_id: int):
    """Pull employee record from main DB and upsert as employee_table attributes.

    This bridges the split-DB identity gap:
      central DB → user_id → (needs contact_id to resolve)
      main DB    → employee.contact_id → department, designation, etc.

    Caller must supply contact_id (resolved from user session at login time).
    The PDP and login service call this automatically; this endpoint allows
    manual re-sync by an admin.
    """
    # TODO: Guard — require supreme user session or internal service call only
    attrs = await load_employee_sync_attributes(contact_id)
    if not attrs:
        raise HTTPException(status_code=404, detail=f"No employee record for contact_id={contact_id}")

    synced: list[str] = []
    async with central_session_context() as db:
        for attr_key, value in attrs.items():
            existing = _row(await db.execute(
                text("SELECT id FROM prism_user_attributes WHERE user_id = :uid AND `key` = :key"),
                {"uid": user_id, "key": attr_key},
            ))
            if existing:
                await db.execute(
                    text(
                        "UPDATE prism_user_attributes SET value = :value, source = 'employee_table', updated_at = NOW() "
                        "WHERE user_id = :uid AND `key` = :key"
                    ),
                    {"value": value, "uid": user_id, "key": attr_key},
                )
            else:
                await db.execute(
                    text(
                        "INSERT INTO prism_user_attributes (user_id, `key`, value, source) "
                        "VALUES (:uid, :key, :value, 'employee_table')"
                    ),
                    {"uid": user_id, "key": attr_key, "value": value},
                )
            synced.append(attr_key)
        await db.commit()

    return {"user_id": user_id, "contact_id": contact_id, "synced_attributes": synced}


# ── Resource Attribute Endpoints ───────────────────────────────────────────

@router.get("/resources/{resource_type}/{resource_id}")
async def get_resource_attributes(resource_type: str, resource_id: str):
    """List all ABAC attributes for a specific resource instance."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        attrs = _rows(await db.execute(
            text(
                "SELECT id, resource_type, resource_id, `key`, value, updated_at "
                "FROM prism_resource_attributes "
                "WHERE resource_type = :rtype AND resource_id = :rid "
                "ORDER BY `key`"
            ),
            {"rtype": resource_type, "rid": resource_id},
        ))
    return {"resource_type": resource_type, "resource_id": resource_id, "attributes": attrs}


@router.post("/resources", status_code=201)
async def set_resource_attribute(payload: ResourceAttributeSet):
    """Set (upsert) a single ABAC attribute on a resource instance."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        existing = _row(await db.execute(
            text(
                "SELECT id FROM prism_resource_attributes "
                "WHERE resource_type = :rtype AND resource_id = :rid AND `key` = :key"
            ),
            {"rtype": payload.resource_type, "rid": payload.resource_id, "key": payload.key},
        ))
        if existing:
            await db.execute(
                text(
                    "UPDATE prism_resource_attributes SET value = :value, updated_at = NOW() "
                    "WHERE resource_type = :rtype AND resource_id = :rid AND `key` = :key"
                ),
                {
                    "value": payload.value,
                    "rtype": payload.resource_type,
                    "rid": payload.resource_id,
                    "key": payload.key,
                },
            )
            attr_id = existing["id"]
        else:
            result = await db.execute(
                text(
                    "INSERT INTO prism_resource_attributes (resource_type, resource_id, `key`, value) "
                    "VALUES (:rtype, :rid, :key, :value)"
                ),
                {
                    "rtype": payload.resource_type,
                    "rid": payload.resource_id,
                    "key": payload.key,
                    "value": payload.value,
                },
            )
            attr_id = result.lastrowid
        await db.commit()

    return {
        "id": attr_id,
        "resource_type": payload.resource_type,
        "resource_id": payload.resource_id,
        "key": payload.key,
        "upserted": True,
    }


@router.delete("/resources/{resource_type}/{resource_id}/{key}")
async def delete_resource_attribute(resource_type: str, resource_id: str, key: str):
    """Delete a specific resource attribute."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        existing = _row(await db.execute(
            text(
                "SELECT id FROM prism_resource_attributes "
                "WHERE resource_type = :rtype AND resource_id = :rid AND `key` = :key"
            ),
            {"rtype": resource_type, "rid": resource_id, "key": key},
        ))
        if not existing:
            raise HTTPException(status_code=404, detail="Attribute not found")

        await db.execute(
            text(
                "DELETE FROM prism_resource_attributes "
                "WHERE resource_type = :rtype AND resource_id = :rid AND `key` = :key"
            ),
            {"rtype": resource_type, "rid": resource_id, "key": key},
        )
        await db.commit()

    return {"deleted": True, "resource_type": resource_type, "resource_id": resource_id, "key": key}

