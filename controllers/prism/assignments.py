"""PRISM — Role/Policy Assignment Management
Handles all attach/detach operations for roles → users, policies → roles/users,
and permission boundary management.

Routes:
  POST   /prism/assignments/user-roles              assign a role to a user
  DELETE /prism/assignments/user-roles/{id}         revoke a user-role assignment
  GET    /prism/assignments/user-roles/{user_id}    list roles for a user

  POST   /prism/assignments/role-policies           attach a policy to a role
  DELETE /prism/assignments/role-policies           detach a policy from a role

  POST   /prism/assignments/user-policies           attach an inline policy to a user
  DELETE /prism/assignments/user-policies           detach an inline policy from a user

  POST   /prism/assignments/boundaries              set permission boundary for a user
  GET    /prism/assignments/boundaries/{user_id}    get boundary for a user
  DELETE /prism/assignments/boundaries/{user_id}    remove boundary for a user
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from core.database_v2 import central_session_context
from core.prism_cache import (
    invalidate_prism_cache,
    invalidate_prism_cache_for_policy,
    invalidate_prism_cache_for_role,
)

router = APIRouter(prefix="/prism/assignments", tags=["PRISM — Assignments"])


# ── Helper ─────────────────────────────────────────────────────────────────

def _row(result) -> Optional[dict]:
    row = result.fetchone()
    return dict(row._mapping) if row else None


def _rows(result) -> list[dict]:
    return [dict(r._mapping) for r in result.fetchall()]


# ── Schemas ────────────────────────────────────────────────────────────────

class UserRoleAssign(BaseModel):
    user_id: int
    role_id: int
    assigned_by: Optional[int] = None
    expires_at: Optional[datetime] = Field(None, description="Omit for permanent assignment")


class RolePolicyAttach(BaseModel):
    role_id: int
    policy_id: int
    attached_by: Optional[int] = None


class UserPolicyAttach(BaseModel):
    user_id: int
    policy_id: int
    attached_by: Optional[int] = None


class PermissionBoundarySet(BaseModel):
    user_id: int
    policy_id: int
    set_by: Optional[int] = None


# ── User ↔ Role ────────────────────────────────────────────────────────────

@router.get("/user-roles/{user_id}")
async def get_user_roles(user_id: int):
    """List all active roles assigned to a user (excludes expired assignments)."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        rows = _rows(await db.execute(
            text(
                "SELECT ur.id, r.id as role_id, r.name, r.type, r.is_active, "
                "ur.assigned_by, ur.expires_at, ur.created_at "
                "FROM prism_user_roles ur "
                "JOIN prism_roles r ON r.id = ur.role_id "
                "WHERE ur.user_id = :user_id "
                "AND (ur.expires_at IS NULL OR ur.expires_at > NOW()) "
                "AND r.is_active = 1 "
                "ORDER BY r.name"
            ),
            {"user_id": user_id},
        ))
    return {"user_id": user_id, "roles": rows, "total": len(rows)}


@router.post("/user-roles", status_code=201)
async def assign_role_to_user(payload: UserRoleAssign):
    """Assign a role to a user.  Optionally time-bound via expires_at."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        # Verify role exists and is active
        role = _row(await db.execute(
            text("SELECT id FROM prism_roles WHERE id = :id AND is_active = 1"),
            {"id": payload.role_id},
        ))
        if not role:
            raise HTTPException(status_code=404, detail="Role not found or inactive")

        # Check for duplicate (allow if previous is expired)
        existing = _row(await db.execute(
            text(
                "SELECT id, expires_at FROM prism_user_roles "
                "WHERE user_id = :user_id AND role_id = :role_id"
            ),
            {"user_id": payload.user_id, "role_id": payload.role_id},
        ))
        if existing:
            # If existing assignment is still active, reject
            exp = existing.get("expires_at")
            if exp is None or (isinstance(exp, datetime) and exp > datetime.utcnow()):
                raise HTTPException(
                    status_code=409,
                    detail="User already has this role assigned (revoke first to reassign)",
                )
            # Expired — update instead of inserting duplicate
            await db.execute(
                text(
                    "UPDATE prism_user_roles SET assigned_by = :assigned_by, "
                    "expires_at = :expires_at, created_at = NOW() "
                    "WHERE id = :id"
                ),
                {"assigned_by": payload.assigned_by, "expires_at": payload.expires_at, "id": existing["id"]},
            )
            await db.commit()
            await invalidate_prism_cache(payload.user_id)
            return {"id": existing["id"], "user_id": payload.user_id, "role_id": payload.role_id, "reassigned": True}

        result = await db.execute(
            text(
                "INSERT INTO prism_user_roles (user_id, role_id, assigned_by, expires_at) "
                "VALUES (:user_id, :role_id, :assigned_by, :expires_at)"
            ),
            {
                "user_id": payload.user_id,
                "role_id": payload.role_id,
                "assigned_by": payload.assigned_by,
                "expires_at": payload.expires_at,
            },
        )
        await db.commit()

    await invalidate_prism_cache(payload.user_id)
    return {"id": result.lastrowid, "user_id": payload.user_id, "role_id": payload.role_id}


@router.delete("/user-roles/{assignment_id}")
async def revoke_user_role(assignment_id: int):
    """Revoke a user-role assignment by assignment ID."""
    # TODO: Guard — require supreme user session
    affected_user_id: Optional[int] = None
    async with central_session_context() as db:
        assignment = _row(await db.execute(
            text("SELECT id, user_id FROM prism_user_roles WHERE id = :id"),
            {"id": assignment_id},
        ))
        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found")

        affected_user_id = assignment.get("user_id")
        await db.execute(
            text("DELETE FROM prism_user_roles WHERE id = :id"),
            {"id": assignment_id},
        )
        await db.commit()

    if affected_user_id is not None:
        await invalidate_prism_cache(affected_user_id)
    return {"revoked": True, "id": assignment_id}


# ── Role ↔ Policy ──────────────────────────────────────────────────────────

@router.post("/role-policies", status_code=201)
async def attach_policy_to_role(payload: RolePolicyAttach):
    """Attach a policy to a role."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        role = _row(await db.execute(
            text("SELECT id FROM prism_roles WHERE id = :id AND is_active = 1"),
            {"id": payload.role_id},
        ))
        if not role:
            raise HTTPException(status_code=404, detail="Role not found or inactive")

        policy = _row(await db.execute(
            text("SELECT id FROM prism_policies WHERE id = :id AND is_active = 1"),
            {"id": payload.policy_id},
        ))
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found or inactive")

        existing = _row(await db.execute(
            text("SELECT id FROM prism_role_policies WHERE role_id = :rid AND policy_id = :pid"),
            {"rid": payload.role_id, "pid": payload.policy_id},
        ))
        if existing:
            raise HTTPException(status_code=409, detail="Policy already attached to this role")

        result = await db.execute(
            text(
                "INSERT INTO prism_role_policies (role_id, policy_id, attached_by) "
                "VALUES (:role_id, :policy_id, :attached_by)"
            ),
            {"role_id": payload.role_id, "policy_id": payload.policy_id, "attached_by": payload.attached_by},
        )
        await db.commit()
        await invalidate_prism_cache_for_role(payload.role_id, db)

    return {"id": result.lastrowid, "role_id": payload.role_id, "policy_id": payload.policy_id}


@router.delete("/role-policies")
async def detach_policy_from_role(role_id: int, policy_id: int):
    """Detach a policy from a role."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        existing = _row(await db.execute(
            text("SELECT id FROM prism_role_policies WHERE role_id = :rid AND policy_id = :pid"),
            {"rid": role_id, "pid": policy_id},
        ))
        if not existing:
            raise HTTPException(status_code=404, detail="Attachment not found")

        await db.execute(
            text("DELETE FROM prism_role_policies WHERE role_id = :rid AND policy_id = :pid"),
            {"rid": role_id, "pid": policy_id},
        )
        await db.commit()
        await invalidate_prism_cache_for_role(role_id, db)

    return {"detached": True, "role_id": role_id, "policy_id": policy_id}


# ── User ↔ Inline Policy ───────────────────────────────────────────────────

@router.post("/user-policies", status_code=201)
async def attach_policy_to_user(payload: UserPolicyAttach):
    """Attach an inline policy directly to a user."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        policy = _row(await db.execute(
            text("SELECT id FROM prism_policies WHERE id = :id AND is_active = 1"),
            {"id": payload.policy_id},
        ))
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found or inactive")

        existing = _row(await db.execute(
            text("SELECT id FROM prism_user_policies WHERE user_id = :uid AND policy_id = :pid"),
            {"uid": payload.user_id, "pid": payload.policy_id},
        ))
        if existing:
            raise HTTPException(status_code=409, detail="Policy already attached to this user")

        result = await db.execute(
            text(
                "INSERT INTO prism_user_policies (user_id, policy_id, attached_by) "
                "VALUES (:user_id, :policy_id, :attached_by)"
            ),
            {"user_id": payload.user_id, "policy_id": payload.policy_id, "attached_by": payload.attached_by},
        )
        await db.commit()

    await invalidate_prism_cache(payload.user_id)
    return {"id": result.lastrowid, "user_id": payload.user_id, "policy_id": payload.policy_id}


@router.get("/user-policies/{user_id}")
async def get_user_policies(user_id: int):
    """List all inline policies attached directly to a user."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        rows = _rows(await db.execute(
            text(
                "SELECT up.id, up.policy_id, p.name, p.type, p.is_active, "
                "up.attached_by, up.created_at as attached_at "
                "FROM prism_user_policies up "
                "JOIN prism_policies p ON p.id = up.policy_id "
                "WHERE up.user_id = :user_id "
                "ORDER BY p.name"
            ),
            {"user_id": user_id},
        ))
    return {"user_id": user_id, "policies": rows, "total": len(rows)}


@router.delete("/user-policies")
async def detach_policy_from_user(user_id: int, policy_id: int):
    """Detach an inline policy from a user."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        existing = _row(await db.execute(
            text("SELECT id FROM prism_user_policies WHERE user_id = :uid AND policy_id = :pid"),
            {"uid": user_id, "pid": policy_id},
        ))
        if not existing:
            raise HTTPException(status_code=404, detail="Attachment not found")

        await db.execute(
            text("DELETE FROM prism_user_policies WHERE user_id = :uid AND policy_id = :pid"),
            {"uid": user_id, "pid": policy_id},
        )
        await db.commit()

    await invalidate_prism_cache(user_id)
    return {"detached": True, "user_id": user_id, "policy_id": policy_id}


# ── Permission Boundaries ──────────────────────────────────────────────────

@router.post("/boundaries", status_code=201)
async def set_permission_boundary(payload: PermissionBoundarySet):
    """Set or replace the permission boundary for a user.
    
    Boundaries are the hard cap on effective permissions.
    Even if roles grant more, this policy hard-limits what the user can ever do.
    Can only be set/changed by super-admins.
    """
    # TODO: Guard — require super-admin check specifically (not just any supreme user)
    async with central_session_context() as db:
        policy = _row(await db.execute(
            text("SELECT id, type FROM prism_policies WHERE id = :id AND is_active = 1"),
            {"id": payload.policy_id},
        ))
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found or inactive")
        if policy["type"] not in ("permission_boundary", "identity"):
            raise HTTPException(
                status_code=400,
                detail="Boundary policy must be of type 'permission_boundary' or 'identity'",
            )

        existing = _row(await db.execute(
            text("SELECT id FROM prism_user_permission_boundaries WHERE user_id = :uid"),
            {"uid": payload.user_id},
        ))
        if existing:
            await db.execute(
                text(
                    "UPDATE prism_user_permission_boundaries "
                    "SET policy_id = :policy_id, set_by = :set_by, set_at = NOW() "
                    "WHERE user_id = :user_id"
                ),
                {"policy_id": payload.policy_id, "set_by": payload.set_by, "user_id": payload.user_id},
            )
        else:
            await db.execute(
                text(
                    "INSERT INTO prism_user_permission_boundaries (user_id, policy_id, set_by) "
                    "VALUES (:user_id, :policy_id, :set_by)"
                ),
                {"user_id": payload.user_id, "policy_id": payload.policy_id, "set_by": payload.set_by},
            )
        await db.commit()

    await invalidate_prism_cache(payload.user_id)
    return {"user_id": payload.user_id, "boundary_policy_id": payload.policy_id}


@router.get("/boundaries/{user_id}")
async def get_permission_boundary(user_id: int):
    """Get the active permission boundary for a user."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        boundary = _row(await db.execute(
            text(
                "SELECT b.user_id, b.policy_id, p.name as policy_name, p.type as policy_type, "
                "b.set_by, b.set_at "
                "FROM prism_user_permission_boundaries b "
                "JOIN prism_policies p ON p.id = b.policy_id "
                "WHERE b.user_id = :user_id"
            ),
            {"user_id": user_id},
        ))
    if not boundary:
        return {"user_id": user_id, "boundary": None}
    return {"user_id": user_id, "boundary": boundary}


@router.delete("/boundaries/{user_id}")
async def remove_permission_boundary(user_id: int):
    """Remove the permission boundary from a user (restores uncapped permissions)."""
    # TODO: Guard — require super-admin check specifically
    async with central_session_context() as db:
        existing = _row(await db.execute(
            text("SELECT id FROM prism_user_permission_boundaries WHERE user_id = :uid"),
            {"uid": user_id},
        ))
        if not existing:
            raise HTTPException(status_code=404, detail="No boundary set for this user")

        await db.execute(
            text("DELETE FROM prism_user_permission_boundaries WHERE user_id = :uid"),
            {"uid": user_id},
        )
        await db.commit()

    await invalidate_prism_cache(user_id)
    return {"removed": True, "user_id": user_id}
