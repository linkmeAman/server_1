"""PRISM — Policy & Statement CRUD
All mutations require a supreme-user session.

Routes:
  GET    /prism/policies                        list policies
  POST   /prism/policies                        create policy
  GET    /prism/policies/{id}                   get policy + all statements
  PATCH  /prism/policies/{id}                   update policy metadata
  DELETE /prism/policies/{id}                   deactivate policy

  POST   /prism/policies/{id}/statements        add a statement to a policy
  PATCH  /prism/policies/{id}/statements/{sid}  update a statement
  DELETE /prism/policies/{id}/statements/{sid}  remove a statement
  GET    /prism/policies/{id}/versions          list version history
"""

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.database import central_session_context
from app.core.prism_cache import invalidate_prism_cache_for_policy

router = APIRouter(prefix="/prism/policies", tags=["PRISM — Policies"])


# ── Pydantic schemas ───────────────────────────────────────────────────────

POLICY_TYPES = ("identity", "resource", "permission_boundary", "scp")
STATEMENT_EFFECTS = ("Allow", "Deny")


class PolicyCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=128)
    description: Optional[str] = None
    type: str = Field("identity", description="identity | resource | permission_boundary | scp")
    created_by: Optional[int] = None


class PolicyUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=128)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class StatementCreate(BaseModel):
    sid: Optional[str] = Field(None, max_length=128, description="Human-readable label")
    effect: str = Field(..., description="Allow | Deny")
    actions: list[str] = Field(..., min_length=1, description='e.g. ["employee:read", "report:*"]')
    resources: list[str] = Field(..., min_length=1, description='e.g. ["employee:*"]')
    conditions: Optional[dict[str, Any]] = None
    not_actions: Optional[list[str]] = None
    not_resources: Optional[list[str]] = None
    priority: int = Field(0, ge=0)


class StatementUpdate(BaseModel):
    sid: Optional[str] = Field(None, max_length=128)
    effect: Optional[str] = None
    actions: Optional[list[str]] = None
    resources: Optional[list[str]] = None
    conditions: Optional[dict[str, Any]] = None
    not_actions: Optional[list[str]] = None
    not_resources: Optional[list[str]] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None


# ── Helpers ────────────────────────────────────────────────────────────────

def _row(result) -> Optional[dict]:
    row = result.fetchone()
    return dict(row._mapping) if row else None


def _rows(result) -> list[dict]:
    return [dict(r._mapping) for r in result.fetchall()]


def _validate_effect(effect: str) -> None:
    if effect not in STATEMENT_EFFECTS:
        raise HTTPException(status_code=400, detail=f"effect must be one of: {STATEMENT_EFFECTS}")


def _validate_policy_type(ptype: str) -> None:
    if ptype not in POLICY_TYPES:
        raise HTTPException(status_code=400, detail=f"type must be one of: {POLICY_TYPES}")


def _parse_statement(row: dict) -> dict:
    """Deserialize JSON fields on a statement row."""
    for field in ("actions_json", "resources_json", "conditions_json", "not_actions_json", "not_resources_json"):
        if row.get(field) is not None:
            row[field] = json.loads(row[field])
    return row


async def _snapshot_policy_version(db, policy_id: int, new_version: int, changed_by: Optional[int], reason: Optional[str]) -> None:
    """Capture a version snapshot into prism_policy_versions."""
    statements = _rows(await db.execute(
        text(
            "SELECT id, sid, effect, actions_json, resources_json, conditions_json, "
            "not_actions_json, not_resources_json, priority, is_active "
            "FROM prism_policy_statements WHERE policy_id = :policy_id ORDER BY priority DESC, id"
        ),
        {"policy_id": policy_id},
    ))
    document = {"version": new_version, "statements": statements}
    await db.execute(
        text(
            "INSERT INTO prism_policy_versions (policy_id, version, document_json, changed_by, change_reason) "
            "VALUES (:policy_id, :version, :document_json, :changed_by, :change_reason)"
        ),
        {
            "policy_id": policy_id,
            "version": new_version,
            "document_json": json.dumps(document),
            "changed_by": changed_by,
            "change_reason": reason,
        },
    )
    await db.execute(
        text("UPDATE prism_policies SET version = :version WHERE id = :id"),
        {"version": new_version, "id": policy_id},
    )


# ── Policy endpoints ───────────────────────────────────────────────────────

@router.get("")
async def list_policies(
    active_only: bool = Query(True),
    type: Optional[str] = Query(None, description="Filter by policy type"),
):
    """List all policies."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        where = "WHERE 1=1"
        params: dict = {}
        if active_only:
            where += " AND is_active = 1"
        if type:
            _validate_policy_type(type)
            where += " AND type = :type"
            params["type"] = type

        result = await db.execute(
            text(
                f"SELECT id, name, description, type, version, is_active, created_by, created_at, modified_at "
                f"FROM prism_policies {where} ORDER BY type, name"
            ),
            params,
        )
        policies = _rows(result)

    return {"policies": policies, "total": len(policies)}


@router.post("", status_code=201)
async def create_policy(payload: PolicyCreate):
    """Create a new policy document (no statements yet)."""
    # TODO: Guard — require supreme user session
    _validate_policy_type(payload.type)

    async with central_session_context() as db:
        existing = _row(await db.execute(
            text("SELECT id FROM prism_policies WHERE name = :name"),
            {"name": payload.name},
        ))
        if existing:
            raise HTTPException(status_code=409, detail=f"Policy '{payload.name}' already exists")

        result = await db.execute(
            text(
                "INSERT INTO prism_policies (name, description, type, version, created_by, is_active) "
                "VALUES (:name, :description, :type, 1, :created_by, 1)"
            ),
            {
                "name": payload.name,
                "description": payload.description,
                "type": payload.type,
                "created_by": payload.created_by,
            },
        )
        await db.commit()
        new_id = result.lastrowid

    return {"id": new_id, "name": payload.name, "type": payload.type, "version": 1}


@router.get("/{policy_id}")
async def get_policy(policy_id: int):
    """Get full policy definition including all active statements."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        policy = _row(await db.execute(
            text(
                "SELECT id, name, description, type, version, is_active, created_by, created_at, modified_at "
                "FROM prism_policies WHERE id = :id"
            ),
            {"id": policy_id},
        ))
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")

        raw_statements = _rows(await db.execute(
            text(
                "SELECT id, sid, effect, actions_json, resources_json, conditions_json, "
                "not_actions_json, not_resources_json, priority, is_active, created_at "
                "FROM prism_policy_statements WHERE policy_id = :policy_id "
                "ORDER BY priority DESC, id"
            ),
            {"policy_id": policy_id},
        ))
        statements = [_parse_statement(s) for s in raw_statements]

    policy["statements"] = statements
    return policy


@router.patch("/{policy_id}")
async def update_policy(policy_id: int, payload: PolicyUpdate):
    """Update policy metadata (name, description, active status)."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        policy = _row(await db.execute(
            text("SELECT id FROM prism_policies WHERE id = :id"),
            {"id": policy_id},
        ))
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")

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
        updates["id"] = policy_id
        await db.execute(text(f"UPDATE prism_policies SET {set_clause} WHERE id = :id"), updates)
        await db.commit()

    return {"updated": True, "id": policy_id}


@router.delete("/{policy_id}")
async def deactivate_policy(policy_id: int):
    """Deactivate a policy (soft delete)."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        policy = _row(await db.execute(
            text("SELECT id FROM prism_policies WHERE id = :id"),
            {"id": policy_id},
        ))
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")

        await db.execute(
            text("UPDATE prism_policies SET is_active = 0 WHERE id = :id"),
            {"id": policy_id},
        )
        await db.commit()

    return {"deactivated": True, "id": policy_id}


@router.get("/{policy_id}/versions")
async def list_policy_versions(policy_id: int):
    """List all version snapshots for a policy."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        policy = _row(await db.execute(
            text("SELECT id, name, version FROM prism_policies WHERE id = :id"),
            {"id": policy_id},
        ))
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")

        versions = _rows(await db.execute(
            text(
                "SELECT id, version, changed_by, changed_at, change_reason "
                "FROM prism_policy_versions WHERE policy_id = :policy_id ORDER BY version DESC"
            ),
            {"policy_id": policy_id},
        ))

    return {"policy_id": policy_id, "current_version": policy["version"], "history": versions}


# ── Statement endpoints ────────────────────────────────────────────────────

@router.post("/{policy_id}/statements", status_code=201)
async def add_statement(policy_id: int, payload: StatementCreate, changed_by: Optional[int] = Query(None)):
    """Add a new Allow/Deny statement to a policy.  Snapshots a new version."""
    # TODO: Guard — require supreme user session
    _validate_effect(payload.effect)

    async with central_session_context() as db:
        policy = _row(await db.execute(
            text("SELECT id, version FROM prism_policies WHERE id = :id AND is_active = 1"),
            {"id": policy_id},
        ))
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found or inactive")

        result = await db.execute(
            text(
                "INSERT INTO prism_policy_statements "
                "(policy_id, sid, effect, actions_json, resources_json, conditions_json, "
                "not_actions_json, not_resources_json, priority, is_active) "
                "VALUES (:policy_id, :sid, :effect, :actions_json, :resources_json, :conditions_json, "
                ":not_actions_json, :not_resources_json, :priority, 1)"
            ),
            {
                "policy_id": policy_id,
                "sid": payload.sid,
                "effect": payload.effect,
                "actions_json": json.dumps(payload.actions),
                "resources_json": json.dumps(payload.resources),
                "conditions_json": json.dumps(payload.conditions) if payload.conditions else None,
                "not_actions_json": json.dumps(payload.not_actions) if payload.not_actions else None,
                "not_resources_json": json.dumps(payload.not_resources) if payload.not_resources else None,
                "priority": payload.priority,
            },
        )
        stmt_id = result.lastrowid
        new_version = policy["version"] + 1
        await _snapshot_policy_version(db, policy_id, new_version, changed_by, f"Statement {stmt_id} added")
        await db.commit()
        await invalidate_prism_cache_for_policy(policy_id, db)

    return {"id": stmt_id, "policy_id": policy_id, "new_version": new_version}


@router.patch("/{policy_id}/statements/{statement_id}")
async def update_statement(policy_id: int, statement_id: int, payload: StatementUpdate, changed_by: Optional[int] = Query(None)):
    """Update an existing statement.  Snapshots a new version."""
    # TODO: Guard — require supreme user session
    if payload.effect is not None:
        _validate_effect(payload.effect)

    async with central_session_context() as db:
        stmt = _row(await db.execute(
            text("SELECT id FROM prism_policy_statements WHERE id = :id AND policy_id = :policy_id"),
            {"id": statement_id, "policy_id": policy_id},
        ))
        if not stmt:
            raise HTTPException(status_code=404, detail="Statement not found")

        policy = _row(await db.execute(
            text("SELECT id, version FROM prism_policies WHERE id = :id"),
            {"id": policy_id},
        ))

        updates: dict = {}
        if payload.sid is not None:
            updates["sid"] = payload.sid
        if payload.effect is not None:
            updates["effect"] = payload.effect
        if payload.actions is not None:
            updates["actions_json"] = json.dumps(payload.actions)
        if payload.resources is not None:
            updates["resources_json"] = json.dumps(payload.resources)
        if payload.conditions is not None:
            updates["conditions_json"] = json.dumps(payload.conditions)
        if payload.not_actions is not None:
            updates["not_actions_json"] = json.dumps(payload.not_actions)
        if payload.not_resources is not None:
            updates["not_resources_json"] = json.dumps(payload.not_resources)
        if payload.priority is not None:
            updates["priority"] = payload.priority
        if payload.is_active is not None:
            updates["is_active"] = int(payload.is_active)

        if not updates:
            raise HTTPException(status_code=400, detail="Nothing to update")

        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["id"] = statement_id
        await db.execute(text(f"UPDATE prism_policy_statements SET {set_clause} WHERE id = :id"), updates)

        new_version = policy["version"] + 1
        await _snapshot_policy_version(db, policy_id, new_version, changed_by, f"Statement {statement_id} updated")
        await db.commit()
        await invalidate_prism_cache_for_policy(policy_id, db)

    return {"updated": True, "statement_id": statement_id, "new_version": new_version}


@router.delete("/{policy_id}/statements/{statement_id}")
async def delete_statement(policy_id: int, statement_id: int, changed_by: Optional[int] = Query(None)):
    """Deactivate a statement (soft delete, preserves audit trail)."""
    # TODO: Guard — require supreme user session
    async with central_session_context() as db:
        stmt = _row(await db.execute(
            text("SELECT id FROM prism_policy_statements WHERE id = :id AND policy_id = :policy_id"),
            {"id": statement_id, "policy_id": policy_id},
        ))
        if not stmt:
            raise HTTPException(status_code=404, detail="Statement not found")

        policy = _row(await db.execute(
            text("SELECT id, version FROM prism_policies WHERE id = :id"),
            {"id": policy_id},
        ))

        await db.execute(
            text("UPDATE prism_policy_statements SET is_active = 0 WHERE id = :id"),
            {"id": statement_id},
        )
        new_version = policy["version"] + 1
        await _snapshot_policy_version(db, policy_id, new_version, changed_by, f"Statement {statement_id} removed")
        await db.commit()
        await invalidate_prism_cache_for_policy(policy_id, db)

    return {"deactivated": True, "statement_id": statement_id, "new_version": new_version}

