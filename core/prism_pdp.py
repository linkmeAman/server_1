"""PRISM — Policy Decision Point (PDP)

Implements the 5-step evaluation algorithm from PRISM-HLD.md:

  STEP 1  Collect all applicable policies (identity + boundary + resource)
  STEP 2  Build ABAC evaluation context (user attrs + resource attrs + request)
  STEP 3  Evaluate every statement (action ∩ resource ∩ conditions)
  STEP 4  Decision ordering:
            a. Explicit Deny in identity policy              → DENY (final)
            b. Allow in identity  AND  (no boundary OR boundary also allows) → ALLOW
            c. Allow in resource policy (principal matches)  → ALLOW
            d. Default                                        → DENY
  STEP 5  Always log to prism_access_logs

Usage:
    from core.prism_pdp import PDPRequest, PDPResult, evaluate

    req = PDPRequest(
        user_id=42,
        action="employee:update",
        resource_type="employee",
        resource_id="17",
        request_context={"sourceIp": "1.2.3.4"},
    )
    result = await evaluate(req, db)
"""

from __future__ import annotations

import fnmatch
import ipaddress
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public request / result types
# ---------------------------------------------------------------------------

@dataclass
class PDPRequest:
    """Single authorization question."""
    user_id: int
    action: str                    # e.g. "employee:update"
    resource_type: str             # e.g. "employee"
    resource_id: str = "*"        # e.g. "17" or "*" for collection-level
    request_context: Dict[str, Any] = field(default_factory=dict)
    # If the caller already resolved PRISM user attributes, pass them in to
    # avoid an extra DB round-trip inside the PDP.
    pre_loaded_user_attrs: Optional[Dict[str, Any]] = None


@dataclass
class PDPResult:
    """PDP decision + audit trail."""
    decision: str                   # "Allow" or "Deny"
    reason: str                     # Human-readable explanation
    matched_policy_id: Optional[int] = None
    matched_statement_id: Optional[int] = None
    evaluated_policies: int = 0


# ---------------------------------------------------------------------------
# Internal data helpers
# ---------------------------------------------------------------------------

def _rows(result) -> List[dict]:
    return [dict(r._mapping) for r in result.fetchall()]


def _row(result) -> Optional[dict]:
    r = result.fetchone()
    return dict(r._mapping) if r else None


# ---------------------------------------------------------------------------
# STEP 1 — Collect policies
# ---------------------------------------------------------------------------

async def _collect_policies(user_id: int, db: AsyncSession) -> Dict[str, List[dict]]:
    """Return identity, boundary, and resource-side policies for user_id."""
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1a. Direct user-attached policies (inline)
    user_policies = _rows(await db.execute(
        text("""
            SELECT p.id, p.name, p.type, p.effect_default,
                   ps.id AS stmt_id, ps.sid, ps.effect,
                   ps.actions, ps.resources, ps.conditions, ps.priority
            FROM   prism_user_policies up
            JOIN   prism_policies      p  ON p.id = up.policy_id AND p.is_active = 1
            JOIN   prism_policy_statements ps ON ps.policy_id = p.id AND ps.is_active = 1
            WHERE  up.user_id = :uid
              AND (up.expires_at IS NULL OR up.expires_at > :now)
        """),
        {"uid": user_id, "now": now_iso},
    ))

    # 1b. Role-attached policies (via active, non-expired roles)
    role_policies = _rows(await db.execute(
        text("""
            SELECT p.id, p.name, p.type, p.effect_default,
                   ps.id AS stmt_id, ps.sid, ps.effect,
                   ps.actions, ps.resources, ps.conditions, ps.priority
            FROM   prism_user_roles   ur
            JOIN   prism_role_policies rp ON rp.role_id = ur.role_id
            JOIN   prism_policies      p  ON p.id = rp.policy_id AND p.is_active = 1
            JOIN   prism_policy_statements ps ON ps.policy_id = p.id AND ps.is_active = 1
            WHERE  ur.user_id = :uid
              AND (ur.expires_at IS NULL OR ur.expires_at > :now)
        """),
        {"uid": user_id, "now": now_iso},
    ))

    # 1c. Permission boundaries (hard cap — if present, Allow needs both identity
    #     AND boundary to grant access)
    boundary_policies = _rows(await db.execute(
        text("""
            SELECT p.id, p.name, p.type, p.effect_default,
                   ps.id AS stmt_id, ps.sid, ps.effect,
                   ps.actions, ps.resources, ps.conditions, ps.priority
            FROM   prism_user_permission_boundaries ub
            JOIN   prism_policies      p  ON p.id = ub.policy_id AND p.is_active = 1
            JOIN   prism_policy_statements ps ON ps.policy_id = p.id AND ps.is_active = 1
            WHERE  ub.user_id = :uid
        """),
        {"uid": user_id},
    ))

    return {
        "identity": user_policies + role_policies,
        "boundary": boundary_policies,
        "resource":  [],   # Phase 5: resource-based policies require resourceId index
    }


# ---------------------------------------------------------------------------
# STEP 2 — Build ABAC attribute context
# ---------------------------------------------------------------------------

async def _build_context(
    user_id: int,
    resource_type: str,
    resource_id: str,
    request_ctx: Dict[str, Any],
    db: AsyncSession,
    pre_loaded: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Return the combined attribute context used in condition evaluation."""
    # User attributes from prism_user_attributes
    if pre_loaded is not None:
        user_attrs = pre_loaded
    else:
        rows = _rows(await db.execute(
            text(
                "SELECT attr_key, attr_value, attr_type "
                "FROM prism_user_attributes "
                "WHERE user_id = :uid AND is_active = 1"
            ),
            {"uid": user_id},
        ))
        user_attrs = {r["attr_key"]: _cast_attr(r["attr_value"], r["attr_type"])
                      for r in rows}
    user_attrs.setdefault("id", str(user_id))

    # Resource attributes (if available)
    res_rows = _rows(await db.execute(
        text(
            "SELECT attr_key, attr_value, attr_type "
            "FROM prism_resource_attributes "
            "WHERE resource_type = :rt AND resource_id = :rid AND is_active = 1"
        ),
        {"rt": resource_type, "rid": resource_id},
    ))
    resource_attrs = {r["attr_key"]: _cast_attr(r["attr_value"], r["attr_type"])
                      for r in res_rows}
    resource_attrs.setdefault("type", resource_type)
    resource_attrs.setdefault("id", resource_id)

    return {
        "user":     user_attrs,
        "resource": resource_attrs,
        "request":  request_ctx,
    }


def _cast_attr(value: str, attr_type: str) -> Any:
    """Cast stored string attribute to its natural Python type."""
    if attr_type == "number":
        try:
            return float(value) if "." in value else int(value)
        except (ValueError, TypeError):
            return value
    if attr_type == "boolean":
        return value.lower() in ("true", "1", "yes")
    if attr_type in ("json", "list"):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


# ---------------------------------------------------------------------------
# STEP 3 — Statement matching
# ---------------------------------------------------------------------------

def _decode_json_field(value: Any) -> Any:
    """Decode a JSON field that may already be a Python object or a JSON string."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value or []


def _action_matches(patterns: List[str], action: str) -> bool:
    """Return True if `action` matches any pattern in `patterns`.

    Patterns support:
      * "employee:*"  — fnmatch style glob
      * "employee:update" — exact
      * "*"            — everything
    """
    for pat in patterns:
        if fnmatch.fnmatch(action, pat):
            return True
    return False


def _interpolate_resource(template: str, ctx: Dict[str, Any]) -> str:
    """Replace ${key:subkey} placeholders with context values.

    Examples:
      "employee:${user:id}"   → "employee:42"
      "doc:${resource:owner}" → "doc:7"
    """
    def _replace(m: re.Match) -> str:
        ns, key = m.group(1), m.group(2)
        ns_data = ctx.get(ns, {})
        return str(ns_data.get(key, m.group(0)))  # leave unresolved if missing

    return re.sub(r"\$\{(\w+):(\w+)\}", _replace, template)


def _resource_matches(patterns: List[str], resource_type: str,
                      resource_id: str, ctx: Dict[str, Any]) -> bool:
    """Return True if the resource descriptor matches any pattern.

    Patterns are in the form "type:id" or "type:*", e.g.:
      "employee:*"
      "employee:42"
      "*"
      "employee:${user:id}"
    """
    full_resource = f"{resource_type}:{resource_id}"
    for raw_pat in patterns:
        pat = _interpolate_resource(raw_pat, ctx)
        if fnmatch.fnmatch(full_resource, pat):
            return True
        # Also allow type-only wildcard: "employee" → "employee:*"
        if ":" not in pat and fnmatch.fnmatch(resource_type, pat):
            return True
    return False


def _conditions_pass(conditions: Any, ctx: Dict[str, Any]) -> bool:
    """Evaluate IAM-style condition block.

    conditions is a dict like:
    {
        "StringEquals": { "user:department": "engineering" },
        "IpAddress":    { "request:sourceIp": "10.0.0.0/8" }
    }
    All top-level operators are AND-combined.
    Each operator's key/value pairs are AND-combined.
    IfExists suffix makes the condition True when the attribute is absent.
    """
    if not conditions:
        return True
    if isinstance(conditions, str):
        try:
            conditions = json.loads(conditions)
        except (ValueError, TypeError):
            return True  # malformed conditions — don't block

    for operator, kvs in conditions.items():
        if not isinstance(kvs, dict):
            continue
        if_exists = operator.endswith("IfExists")
        base_op = operator[:-len("IfExists")] if if_exists else operator

        for key_path, expected in kvs.items():
            actual = _resolve_path(key_path, ctx)
            if actual is None:
                if if_exists:
                    continue  # attribute absent → condition vacuously true
                return False  # required attribute missing → condition fails

            if not _apply_operator(base_op, actual, expected):
                return False

    return True


def _resolve_path(key_path: str, ctx: Dict[str, Any]) -> Any:
    """Resolve "namespace:key" from context dict, e.g. "user:department"."""
    if ":" in key_path:
        ns, key = key_path.split(":", 1)
        return ctx.get(ns, {}).get(key)
    # No namespace — search all top-level namespaces
    for ns_data in ctx.values():
        if isinstance(ns_data, dict) and key_path in ns_data:
            return ns_data[key_path]
    return None


def _apply_operator(operator: str, actual: Any, expected: Any) -> bool:
    """Apply a single IAM-style condition operator."""
    # Normalize for case-insensitive operator matching
    op = operator.strip()

    # ── String operators ──────────────────────────────────────────────────
    if op == "StringEquals":
        return _multi(lambda a, e: str(a) == str(e), actual, expected)
    if op == "StringNotEquals":
        return _multi(lambda a, e: str(a) != str(e), actual, expected)
    if op in ("StringEqualsIgnoreCase",):
        return _multi(lambda a, e: str(a).lower() == str(e).lower(), actual, expected)
    if op == "StringLike":
        return _multi(lambda a, e: fnmatch.fnmatch(str(a), str(e)), actual, expected)
    if op == "StringNotLike":
        return _multi(lambda a, e: not fnmatch.fnmatch(str(a), str(e)), actual, expected)

    # ── Numeric operators ─────────────────────────────────────────────────
    if op == "NumericEquals":
        return _multi(lambda a, e: _num(a) == _num(e), actual, expected)
    if op == "NumericNotEquals":
        return _multi(lambda a, e: _num(a) != _num(e), actual, expected)
    if op == "NumericLessThan":
        return _multi(lambda a, e: _num(a) < _num(e), actual, expected)
    if op == "NumericLessThanEquals":
        return _multi(lambda a, e: _num(a) <= _num(e), actual, expected)
    if op == "NumericGreaterThan":
        return _multi(lambda a, e: _num(a) > _num(e), actual, expected)
    if op == "NumericGreaterThanEquals":
        return _multi(lambda a, e: _num(a) >= _num(e), actual, expected)

    # ── Date operators ────────────────────────────────────────────────────
    if op == "DateEquals":
        return _multi(lambda a, e: _dt(a) == _dt(e), actual, expected)
    if op == "DateNotEquals":
        return _multi(lambda a, e: _dt(a) != _dt(e), actual, expected)
    if op == "DateLessThan":
        return _multi(lambda a, e: _dt(a) < _dt(e), actual, expected)
    if op == "DateLessThanEquals":
        return _multi(lambda a, e: _dt(a) <= _dt(e), actual, expected)
    if op == "DateGreaterThan":
        return _multi(lambda a, e: _dt(a) > _dt(e), actual, expected)
    if op == "DateGreaterThanEquals":
        return _multi(lambda a, e: _dt(a) >= _dt(e), actual, expected)

    # ── Bool operator ─────────────────────────────────────────────────────
    if op == "Bool":
        def _bool_eq(a: Any, e: Any) -> bool:
            def _b(v: Any) -> bool:
                if isinstance(v, bool): return v
                return str(v).lower() in ("true", "1", "yes")
            return _b(a) == _b(e)
        return _multi(_bool_eq, actual, expected)

    # ── IP address operators ──────────────────────────────────────────────
    if op == "IpAddress":
        return _multi(_ip_in_cidr, actual, expected)
    if op == "NotIpAddress":
        return _multi(lambda a, e: not _ip_in_cidr(a, e), actual, expected)

    # ── ARN-like (glob over resource ARN strings) ─────────────────────────
    if op == "ArnLike":
        return _multi(lambda a, e: fnmatch.fnmatch(str(a), str(e)), actual, expected)
    if op == "ArnNotLike":
        return _multi(lambda a, e: not fnmatch.fnmatch(str(a), str(e)), actual, expected)

    # ── Null check ────────────────────────────────────────────────────────
    if op == "Null":
        # expected is "true" or "false"
        is_null = actual is None
        expect_null = str(expected).lower() in ("true", "1")
        return is_null == expect_null

    # Unknown operator — log and allow (fail-open for forward compatibility)
    logger.warning("PRISM PDP: unknown condition operator '%s', treating as pass", op)
    return True


def _multi(fn, actual: Any, expected: Any) -> bool:
    """ForAnyValue semantics: actual and/or expected may be lists.
    Returns True if ANY (actual, expected) pair satisfies fn.
    """
    actuals  = actual   if isinstance(actual,   list) else [actual]
    expecteds = expected if isinstance(expected, list) else [expected]
    return any(fn(a, e) for a in actuals for e in expecteds)


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v))
    except (ValueError, TypeError):
        return datetime.min


def _ip_in_cidr(ip_str: Any, cidr_str: Any) -> bool:
    try:
        ip   = ipaddress.ip_address(str(ip_str).strip())
        net  = ipaddress.ip_network(str(cidr_str).strip(), strict=False)
        return ip in net
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# STEP 3 full — evaluate a single statement against the current request
# ---------------------------------------------------------------------------

def _statement_matches(
    stmt: dict,
    action: str,
    resource_type: str,
    resource_id: str,
    ctx: Dict[str, Any],
) -> Tuple[bool, str]:
    """Return (matches: bool, effect: "Allow"|"Deny")."""
    actions    = _decode_json_field(stmt.get("actions"))
    resources  = _decode_json_field(stmt.get("resources"))
    conditions = _decode_json_field(stmt.get("conditions"))

    if not isinstance(actions,   list): actions   = [actions]
    if not isinstance(resources, list): resources = [resources]

    if not _action_matches(actions, action):
        return False, stmt.get("effect", "Deny")
    if not _resource_matches(resources, resource_type, resource_id, ctx):
        return False, stmt.get("effect", "Deny")
    if not _conditions_pass(conditions, ctx):
        return False, stmt.get("effect", "Deny")

    return True, stmt.get("effect", "Deny")


# ---------------------------------------------------------------------------
# STEP 4 — Decision algorithm
# ---------------------------------------------------------------------------

def _decide(
    identity_stmts: List[Tuple[dict, str]],  # (stmt, effect) matched pairs
    boundary_stmts: List[Tuple[dict, str]],
    has_boundary: bool,
) -> Tuple[str, str, Optional[dict]]:
    """Return (decision, reason, winning_stmt) following the 5-step algorithm."""

    # 4a — Explicit Deny in identity policy overrides everything
    for stmt, effect in identity_stmts:
        if effect == "Deny":
            return "Deny", f"Explicit Deny in statement sid={stmt.get('sid', stmt.get('stmt_id'))}", stmt

    # 4b — Allow in identity, gated by boundary
    identity_allows = [(s, e) for s, e in identity_stmts if e == "Allow"]
    if identity_allows:
        if not has_boundary:
            s, _ = identity_allows[0]
            return "Allow", "Allow in identity policy (no boundary)", s
        # Boundary exists — it must also produce an Allow
        boundary_allows = [s for s, e in boundary_stmts if e == "Allow"]
        if boundary_allows:
            s, _ = identity_allows[0]
            return "Allow", "Allow in identity policy AND boundary policy", s
        return "Deny", "Identity policy grants Allow but permission boundary does not", None

    # 4c — Resource-based policies (Phase 5; not yet populated)
    # (no resource_stmts in current iteration)

    # 4d — Default Deny
    return "Deny", "No matching Allow statement found (default deny)", None


# ---------------------------------------------------------------------------
# STEP 5 — Audit log
# ---------------------------------------------------------------------------

async def _log_decision(
    user_id: int,
    action: str,
    resource_type: str,
    resource_id: str,
    decision: str,
    reason: str,
    matched_policy_id: Optional[int],
    matched_statement_id: Optional[int],
    request_ctx: Dict[str, Any],
    db: AsyncSession,
) -> None:
    try:
        await db.execute(
            text("""
                INSERT INTO prism_access_logs
                    (user_id, action, resource_type, resource_id,
                     decision, deny_reason, matched_policy_id,
                     matched_statement_id, request_context, created_at)
                VALUES
                    (:uid, :action, :rt, :rid,
                     :decision, :reason, :pol_id,
                     :stmt_id, :req_ctx, NOW())
            """),
            {
                "uid":     user_id,
                "action":  action,
                "rt":      resource_type,
                "rid":     resource_id,
                "decision": decision,
                "reason":  reason,
                "pol_id":  matched_policy_id,
                "stmt_id": matched_statement_id,
                "req_ctx": json.dumps(request_ctx) if request_ctx else None,
            },
        )
    except Exception as exc:
        # Logging failure must never affect the PDP decision
        logger.error("PRISM: failed to write access log: %s", exc)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def evaluate(req: PDPRequest, db: AsyncSession) -> PDPResult:
    """Evaluate one authorization request and return a PDPResult.

    The caller is responsible for:
    - Providing an active central-DB session (`db`)
    - Committing/rolling back after calling this function (or letting the
      session context manager handle it)
    """
    # ── STEP 1: collect policies ──────────────────────────────────────────
    policy_buckets = await _collect_policies(req.user_id, db)

    total_policies = (
        len({s["id"] for s in policy_buckets["identity"]}) +
        len({s["id"] for s in policy_buckets["boundary"]})
    )

    # ── STEP 2: build attribute context ───────────────────────────────────
    ctx = await _build_context(
        req.user_id,
        req.resource_type,
        req.resource_id,
        req.request_context,
        db,
        req.pre_loaded_user_attrs,
    )

    # ── STEP 3: evaluate statements ───────────────────────────────────────
    identity_matched: List[Tuple[dict, str]] = []
    for stmt in policy_buckets["identity"]:
        matched, effect = _statement_matches(
            stmt, req.action, req.resource_type, req.resource_id, ctx
        )
        if matched:
            identity_matched.append((stmt, effect))

    boundary_matched: List[Tuple[dict, str]] = []
    for stmt in policy_buckets["boundary"]:
        matched, effect = _statement_matches(
            stmt, req.action, req.resource_type, req.resource_id, ctx
        )
        if matched:
            boundary_matched.append((stmt, effect))

    has_boundary = len(policy_buckets["boundary"]) > 0

    # ── STEP 4: decide ────────────────────────────────────────────────────
    decision, reason, winning_stmt = _decide(
        identity_matched, boundary_matched, has_boundary
    )

    matched_policy_id   = winning_stmt["id"]      if winning_stmt else None
    matched_statement_id = winning_stmt["stmt_id"] if winning_stmt else None

    # ── STEP 5: log ───────────────────────────────────────────────────────
    await _log_decision(
        req.user_id, req.action, req.resource_type, req.resource_id,
        decision, reason,
        matched_policy_id, matched_statement_id,
        req.request_context, db,
    )

    return PDPResult(
        decision=decision,
        reason=reason,
        matched_policy_id=matched_policy_id,
        matched_statement_id=matched_statement_id,
        evaluated_policies=total_policies,
    )
