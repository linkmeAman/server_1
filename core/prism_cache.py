"""PRISM — Redis cache layer (Phase 3)

Cache key  : prism:perms:{user_id}
TTL        : PRISM_CACHE_TTL_SECONDS (default 300 s = 5 min)

Cache entry (JSON):
{
    "user_id"               : 42,
    "static_allows"         : ["*", "employee:*"],   # global-resource Allow patterns
    "static_denies"         : ["system:*"],           # global-resource Deny patterns
    "needs_full_pdp"        : false,  # True → any conditional / resource-scoped stmt
    "has_boundary"          : false,  # True → boundary policy exists
    "cached_at"             : "2026-03-20T12:00:00Z"
}

PDP fast-path (when needs_full_pdp=False AND has_boundary=False):
  1. static_denies pattern match → Deny  (skip DB)
  2. static_allows pattern match → Allow (skip DB)
  3. No match                    → Deny  (default deny, skip DB)

Public API
----------
  init_redis(url)                     — call from app lifespan startup
  close_redis()                       — call from app lifespan shutdown
  build_prism_cache(user_id)          — self-contained, call as BackgroundTask at login
  get_prism_cache(user_id)            — returns dict or None
  invalidate_prism_cache(user_id)     — wipe single user key
  invalidate_prism_cache_for_role(role_id, db)    — wipe all users that have the role
  invalidate_prism_cache_for_policy(policy_id, db) — wipe all users that own the policy
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import central_session_context, main_session_context
from core.settings import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redis client management
# ---------------------------------------------------------------------------

try:
    import redis.asyncio as aioredis  # type: ignore[import]
    _REDIS_AVAILABLE = True
except ImportError:
    aioredis = None  # type: ignore[assignment]
    _REDIS_AVAILABLE = False

_redis_client: Any = None  # redis.asyncio.Redis or None


async def init_redis(url: str | None = None) -> None:
    """Initialize the global async Redis client.  Called from app lifespan startup."""
    global _redis_client
    if not _REDIS_AVAILABLE:
        logger.warning("PRISM cache: redis[asyncio] not installed — cache disabled")
        return
    settings = get_settings()
    redis_url = url or settings.REDIS_URL
    try:
        _redis_client = aioredis.from_url(redis_url, decode_responses=True)
        await _redis_client.ping()
        logger.info("PRISM cache: Redis connected → %s", redis_url.split("@")[-1])
    except Exception as exc:
        logger.warning("PRISM cache: Redis unavailable (%s) — PDP will run without cache", exc)
        _redis_client = None


async def close_redis() -> None:
    """Close the Redis connection pool.  Called from app lifespan shutdown."""
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.aclose()
        except Exception:
            pass
        _redis_client = None


def _client() -> Any:
    return _redis_client


def _cache_key(user_id: int) -> str:
    return f"prism:perms:{user_id}"


# ---------------------------------------------------------------------------
# Low-level get/set/delete
# ---------------------------------------------------------------------------

async def get_prism_cache(user_id: int) -> Optional[Dict[str, Any]]:
    """Return the cached PRISM permissions dict, or None on miss/unavailability."""
    client = _client()
    if client is None:
        return None
    try:
        raw = await client.get(_cache_key(user_id))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.debug("PRISM cache: get error for user_id=%s: %s", user_id, exc)
        return None


async def _set_prism_cache(user_id: int, data: Dict[str, Any]) -> None:
    client = _client()
    if client is None:
        return
    settings = get_settings()
    try:
        await client.set(
            _cache_key(user_id),
            json.dumps(data),
            ex=settings.PRISM_CACHE_TTL_SECONDS,
        )
    except Exception as exc:
        logger.debug("PRISM cache: set error for user_id=%s: %s", user_id, exc)


async def invalidate_prism_cache(user_id: int) -> None:
    """Delete the permissions cache for a single user."""
    client = _client()
    if client is None:
        # Even when Redis is unavailable, still revoke auth sessions.
        try:
            from controllers.auth.constants import REVOKE_REASON_SESSION_FAMILY_WIPE
            from controllers.auth.services.session_revocation import revoke_all_sessions_for_user

            async with central_session_context() as db:
                await revoke_all_sessions_for_user(int(user_id), REVOKE_REASON_SESSION_FAMILY_WIPE, db)
                await db.commit()
        except Exception as exc:
            logger.debug("PRISM cache: session revoke error for user_id=%s: %s", user_id, exc)
        return
    try:
        deleted = await client.delete(_cache_key(user_id))
        if deleted:
            logger.debug("PRISM cache: invalidated user_id=%s", user_id)
    except Exception as exc:
        logger.debug("PRISM cache: invalidate error for user_id=%s: %s", user_id, exc)

    # Force re-auth on permission mutations affecting this user.
    try:
        from controllers.auth.constants import REVOKE_REASON_SESSION_FAMILY_WIPE
        from controllers.auth.services.session_revocation import revoke_all_sessions_for_user

        async with central_session_context() as db:
            await revoke_all_sessions_for_user(int(user_id), REVOKE_REASON_SESSION_FAMILY_WIPE, db)
            await db.commit()
    except Exception as exc:
        logger.debug("PRISM cache: session revoke error for user_id=%s: %s", user_id, exc)


# ---------------------------------------------------------------------------
# Bulk invalidation helpers (used by PRISM mutation handlers)
# ---------------------------------------------------------------------------

async def invalidate_prism_cache_for_role(role_id: int, db: AsyncSession) -> None:
    """Invalidate cache for every user that currently has the specified role."""
    client = _client()
    if client is None:
        return
    try:
        rows = await db.execute(
            text(
                "SELECT DISTINCT user_id FROM prism_user_roles WHERE role_id = :rid"
            ),
            {"rid": role_id},
        )
        user_ids = [r[0] for r in rows.fetchall()]
        if user_ids:
            keys = [_cache_key(uid) for uid in user_ids]
            await client.delete(*keys)
            logger.debug("PRISM cache: invalidated %d users for role_id=%s", len(user_ids), role_id)
            try:
                from controllers.auth.constants import REVOKE_REASON_SESSION_FAMILY_WIPE
                from controllers.auth.services.session_revocation import revoke_all_sessions_for_user

                for uid in user_ids:
                    await revoke_all_sessions_for_user(int(uid), REVOKE_REASON_SESSION_FAMILY_WIPE, db)
                await db.commit()
            except Exception as revoke_exc:
                logger.debug(
                    "PRISM cache: session revoke error for role_id=%s: %s",
                    role_id,
                    revoke_exc,
                )
    except Exception as exc:
        logger.debug("PRISM cache: bulk-role invalidate error role_id=%s: %s", role_id, exc)


async def invalidate_prism_cache_for_policy(policy_id: int, db: AsyncSession) -> None:
    """Invalidate cache for every user that has the policy (directly or via role)."""
    client = _client()
    if client is None:
        return
    try:
        user_ids: set[int] = set()

        # a. Direct user → policy attachments
        direct = await db.execute(
            text("SELECT DISTINCT user_id FROM prism_user_policies WHERE policy_id = :pid"),
            {"pid": policy_id},
        )
        for row in direct.fetchall():
            user_ids.add(row[0])

        # b. Users via roles → policy
        roles_with_policy = await db.execute(
            text("SELECT DISTINCT role_id FROM prism_role_policies WHERE policy_id = :pid"),
            {"pid": policy_id},
        )
        role_ids = [r[0] for r in roles_with_policy.fetchall()]
        if role_ids:
            for role_id_val in role_ids:
                users_in_role = await db.execute(
                    text(
                        "SELECT DISTINCT user_id FROM prism_user_roles WHERE role_id = :rid"
                    ),
                    {"rid": role_id_val},
                )
                for row in users_in_role.fetchall():
                    user_ids.add(row[0])

        if user_ids:
            keys = [_cache_key(uid) for uid in user_ids]
            await client.delete(*keys)
            logger.debug(
                "PRISM cache: invalidated %d users for policy_id=%s", len(user_ids), policy_id
            )
            try:
                from controllers.auth.constants import REVOKE_REASON_SESSION_FAMILY_WIPE
                from controllers.auth.services.session_revocation import revoke_all_sessions_for_user

                for uid in user_ids:
                    await revoke_all_sessions_for_user(int(uid), REVOKE_REASON_SESSION_FAMILY_WIPE, db)
                await db.commit()
            except Exception as revoke_exc:
                logger.debug(
                    "PRISM cache: session revoke error for policy_id=%s: %s",
                    policy_id,
                    revoke_exc,
                )
    except Exception as exc:
        logger.debug("PRISM cache: bulk-policy invalidate error policy_id=%s: %s", policy_id, exc)


async def sync_prism_employee_attrs(user_id: int, contact_id: int) -> None:
    """Background task: pull employee fields from main DB and upsert into
    prism_user_attributes (source='employee_table').

    Called at successful login so the PDP has up-to-date ABAC context for
    conditions such as user:department, user:designation, etc.
    Failures are silenced — this is best-effort and never blocks the caller.
    """
    EMPLOYEE_FIELDS = (
        "department",
        "designation",
        "cost_center",
        "clearance_level",
        "employment_type",
    )
    try:
        async with main_session_context() as main_db:
            result = await main_db.execute(
                text(
                    "SELECT department, designation, cost_center, "
                    "clearance_level, employment_type "
                    "FROM employee WHERE contact_id = :cid LIMIT 1"
                ),
                {"cid": contact_id},
            )
            row = result.fetchone()
            if row is None:
                logger.debug(
                    "sync_prism_employee_attrs: no employee for contact_id=%s", contact_id
                )
                return
            emp = dict(row._mapping)

        async with central_session_context() as db:
            for field in EMPLOYEE_FIELDS:
                val = emp.get(field)
                if val is None:
                    continue
                existing = (await db.execute(
                    text(
                        "SELECT id FROM prism_user_attributes "
                        "WHERE user_id = :uid AND `key` = :key"
                    ),
                    {"uid": user_id, "key": field},
                )).fetchone()
                if existing:
                    await db.execute(
                        text(
                            "UPDATE prism_user_attributes "
                            "SET value = :value, source = 'employee_table', updated_at = NOW() "
                            "WHERE user_id = :uid AND `key` = :key"
                        ),
                        {"value": str(val), "uid": user_id, "key": field},
                    )
                else:
                    await db.execute(
                        text(
                            "INSERT INTO prism_user_attributes (user_id, `key`, value, source) "
                            "VALUES (:uid, :key, :value, 'employee_table')"
                        ),
                        {"uid": user_id, "key": field, "value": str(val)},
                    )
            await db.commit()

        logger.debug(
            "sync_prism_employee_attrs: synced user_id=%s contact_id=%s", user_id, contact_id
        )
    except Exception:
        logger.exception(
            "sync_prism_employee_attrs failed user_id=%s contact_id=%s", user_id, contact_id
        )


# ---------------------------------------------------------------------------
# Cache build (called at login time as a BackgroundTask)
# ---------------------------------------------------------------------------

def _is_global_resource(resources: List[Any]) -> bool:
    """Return True if all resource patterns are wildcards — no specific resource filter.

    Global patterns: "*", "*:*", "type:*"
    Non-global:      "employee:42", "employee:${user:id}", "doc:${resource:id}"
    """
    if not resources:
        return True  # empty list = implicit wildcard
    for r in resources:
        r = str(r)
        if "${" in r:
            return False  # variable interpolation = resource-scoped
        # Accept: "*", "something:*" (type wildcard)
        if r == "*" or r.endswith(":*"):
            continue
        # Reject: anything else (specific resource ID)
        return False
    return True


async def _compute_cache_data(user_id: int, db: AsyncSession) -> Dict[str, Any]:
    """Query DB and return the cache payload for user_id."""
    now_iso = datetime.now(timezone.utc).isoformat()

    # ── collect all policy statements for this user ───────────────────────
    # Direct user-attached policies
    # prism_user_policies has no expires_at column
    direct_stmts = await db.execute(
        text("""
            SELECT ps.effect,
                   ps.actions_json,
                   ps.resources_json,
                   ps.conditions_json
            FROM   prism_user_policies up
            JOIN   prism_policies      p  ON p.id = up.policy_id AND p.is_active = 1
            JOIN   prism_policy_statements ps ON ps.policy_id = p.id AND ps.is_active = 1
            WHERE  up.user_id = :uid
        """),
        {"uid": user_id},
    )

    # Role-sourced policies
    role_stmts = await db.execute(
        text("""
            SELECT ps.effect,
                   ps.actions_json,
                   ps.resources_json,
                   ps.conditions_json
            FROM   prism_user_roles    ur
            JOIN   prism_role_policies rp ON rp.role_id = ur.role_id
            JOIN   prism_policies      p  ON p.id = rp.policy_id AND p.is_active = 1
            JOIN   prism_policy_statements ps ON ps.policy_id = p.id AND ps.is_active = 1
            WHERE  ur.user_id = :uid
              AND (ur.expires_at IS NULL OR ur.expires_at > :now)
        """),
        {"uid": user_id, "now": now_iso},
    )

    all_stmts = [
        *(dict(r._mapping) for r in direct_stmts.fetchall()),
        *(dict(r._mapping) for r in role_stmts.fetchall()),
    ]

    # ── check if user has a permission boundary ───────────────────────────
    boundary_row = await db.execute(
        text(
            "SELECT id FROM prism_user_permission_boundaries WHERE user_id = :uid LIMIT 1"
        ),
        {"uid": user_id},
    )
    has_boundary = boundary_row.fetchone() is not None

    # ── classify statements ───────────────────────────────────────────────
    static_allows: List[str] = []
    static_denies: List[str] = []
    needs_full_pdp = has_boundary  # boundary always requires full PDP

    for stmt in all_stmts:
        try:
            actions    = json.loads(stmt.get("actions_json")    or "[]")
            resources  = json.loads(stmt.get("resources_json")  or "[]")
            conditions = stmt.get("conditions_json")
            effect     = stmt.get("effect", "Deny")
        except (ValueError, TypeError):
            needs_full_pdp = True
            continue

        has_conditions = bool(
            conditions and conditions.strip() not in ("null", "{}", "[]", "")
        )
        resource_global = _is_global_resource(resources if isinstance(resources, list) else [])

        if has_conditions or not resource_global:
            needs_full_pdp = True
        else:
            # Plain global statement — safe to cache
            target = static_allows if effect == "Allow" else static_denies
            for action in (actions if isinstance(actions, list) else [actions]):
                if action and action not in target:
                    target.append(str(action))

    return {
        "user_id":         user_id,
        "static_allows":   static_allows,
        "static_denies":   static_denies,
        "needs_full_pdp":  needs_full_pdp,
        "has_boundary":    has_boundary,
        "cached_at":       datetime.now(timezone.utc).isoformat(),
    }


async def build_prism_cache(user_id: int) -> None:
    """Compute and store the PRISM permissions cache for `user_id`.

    Self-contained: opens its own central DB session.
    Errors are logged but never raised — safe to run as a BackgroundTask.

    Typical call sites:
      - login_employee handler (after successful commit)
      - select_role handler (after successful commit)
    """
    try:
        async with central_session_context() as db:
            data = await _compute_cache_data(user_id, db)
        await _set_prism_cache(user_id, data)
        logger.debug(
            "PRISM cache: built for user_id=%s "
            "(static_allows=%d, static_denies=%d, needs_full_pdp=%s, has_boundary=%s)",
            user_id,
            len(data["static_allows"]),
            len(data["static_denies"]),
            data["needs_full_pdp"],
            data["has_boundary"],
        )
    except Exception as exc:
        logger.warning("PRISM cache: build failed for user_id=%s: %s", user_id, exc)

