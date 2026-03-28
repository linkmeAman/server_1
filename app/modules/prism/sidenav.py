"""PRISM — Sidenav Config endpoint

GET  /prism/sidenav/config   — any authenticated user (require_any_caller)
PUT  /prism/sidenav/config   — supreme user only (require_prism_caller)

Stores a singleton row (id=1) in prism_sidenav_config containing the full
JSON-serialised nav config produced by the Sidenav Manager UI.
All users fetch this on login so every browser sees the same navigation layout.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.database import central_session_context
from core.prism_guard import CallerContext, require_prism_caller, require_any_caller
from sqlalchemy import text

router = APIRouter(prefix="/prism/sidenav", tags=["PRISM · Sidenav"])

_SINGLETON_ID = 1


# ── Schemas ─────────────────────────────────────────────────────────────────

class NavConfigResponse(BaseModel):
    """Sidenav config as stored — returned to both supreme and regular users."""
    items: list = Field(default_factory=list, description="Array of ManagedNavItem objects")
    version: int = Field(default=0, description="Monotonically increasing save counter")
    updated_by_user_id: int = Field(default=0)
    updated_at: Any = Field(default=None)


class NavConfigSaveRequest(BaseModel):
    items: list = Field(..., description="Full array of ManagedNavItem objects to persist")


# ── GET /prism/sidenav/config ───────────────────────────────────────────────

@router.get("/config", response_model=NavConfigResponse, status_code=200)
async def get_sidenav_config(
    caller: CallerContext = Depends(require_any_caller),
) -> NavConfigResponse:
    """Return the current sidenav config for all authenticated users.

    If no config has been saved yet (no DB row), returns version=0 and
    an empty items list — the frontend falls back to its built-in defaults.
    """
    async with central_session_context() as db:
        row = await db.execute(
            text(
                "SELECT config_json, version, updated_by_user_id, updated_at "
                "FROM prism_sidenav_config WHERE id = :id"
            ),
            {"id": _SINGLETON_ID},
        )
        result = row.fetchone()

    if result is None:
        # No config saved yet — frontend will use in-code defaults
        return NavConfigResponse(items=[], version=0, updated_by_user_id=0, updated_at=None)

    try:
        items = json.loads(result.config_json)
    except (json.JSONDecodeError, TypeError):
        items = []

    return NavConfigResponse(
        items=items,
        version=result.version,
        updated_by_user_id=result.updated_by_user_id,
        updated_at=result.updated_at,
    )


# ── PUT /prism/sidenav/config ───────────────────────────────────────────────

@router.put("/config", response_model=NavConfigResponse, status_code=200)
async def save_sidenav_config(
    body: NavConfigSaveRequest,
    caller: CallerContext = Depends(require_prism_caller),
) -> NavConfigResponse:
    """Persist the sidenav config (supreme users only).

    Uses INSERT ... ON DUPLICATE KEY UPDATE on id=1 so the very first save
    creates the row and all subsequent saves update it atomically.
    The version counter is incremented on every write.
    """
    if not isinstance(body.items, list):
        raise HTTPException(status_code=422, detail="items must be an array")

    try:
        config_json = json.dumps(body.items, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"items is not JSON-serialisable: {exc}")

    now = datetime.utcnow()

    async with central_session_context() as db:
        await db.execute(
            text(
                """
                INSERT INTO prism_sidenav_config
                    (id, config_json, updated_by_user_id, version, updated_at)
                VALUES
                    (:id, :config_json, :user_id, 1, :now)
                ON DUPLICATE KEY UPDATE
                    config_json        = VALUES(config_json),
                    updated_by_user_id = VALUES(updated_by_user_id),
                    version            = version + 1,
                    updated_at         = VALUES(updated_at)
                """
            ),
            {
                "id": _SINGLETON_ID,
                "config_json": config_json,
                "user_id": caller.user_id,
                "now": now,
            },
        )
        await db.commit()

        # Re-fetch to return the authoritative saved version
        row = await db.execute(
            text(
                "SELECT config_json, version, updated_by_user_id, updated_at "
                "FROM prism_sidenav_config WHERE id = :id"
            ),
            {"id": _SINGLETON_ID},
        )
        saved = row.fetchone()

    return NavConfigResponse(
        items=body.items,
        version=saved.version if saved else 1,
        updated_by_user_id=caller.user_id,
        updated_at=saved.updated_at if saved else now,
    )
