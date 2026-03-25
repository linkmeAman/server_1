"""PRISM — Evaluate endpoint

POST /prism/evaluate

Single-shot PDP call for debugging and Golang-service integration.
Body specifies the user, action, resource, and optional request context.
Returns the PDP decision with tracing details.

This endpoint requires a valid supreme-user Bearer token.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.database import central_session_context
from core.prism_guard import CallerContext, require_prism_caller, require_any_caller
from core.prism_pdp import PDPRequest, PDPResult, evaluate

router = APIRouter(prefix="/prism/evaluate", tags=["PRISM · Evaluate"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class EvaluateRequest(BaseModel):
    """Authorization question submitted to the PDP."""
    user_id: int = Field(..., description="User whose permissions are being evaluated")
    action: str = Field(..., description="Action string, e.g. 'employee:update'")
    resource_type: str = Field(..., description="Resource type, e.g. 'employee'")
    resource_id: str = Field(
        default="*",
        description="Specific resource ID or '*' for collection-level checks",
    )
    request_context: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Optional runtime attributes for condition evaluation — "
            "e.g. {'sourceIp': '10.0.0.1', 'mfaAuthenticated': True}"
        ),
    )


class EvaluateResponse(BaseModel):
    """PDP verdict with audit details."""
    decision: str = Field(..., description="'Allow' or 'Deny'")
    reason: str
    matched_policy_id: Optional[int] = None
    matched_statement_id: Optional[int] = None
    evaluated_policies: int = Field(
        ...,
        description="Number of distinct policies inspected during evaluation",
    )


class MyPermissionsResponse(BaseModel):
    """Snapshot of the caller's PRISM permission cache — used by the frontend."""
    user_id: int
    is_supreme: bool
    static_allows: list = Field(
        default_factory=list,
        description="Action glob patterns that are statically allowed (e.g. ['*', 'employee:*'])",
    )
    static_denies: list = Field(
        default_factory=list,
        description="Action glob patterns that are statically denied (e.g. ['system:*'])",
    )
    needs_full_pdp: bool = Field(
        default=False,
        description="True if any conditional/resource-scoped statement exists — PDP runs per-request",
    )
    has_boundary: bool = Field(
        default=False,
        description="True if a permission boundary caps this user's maximum permissions",
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("", response_model=EvaluateResponse, status_code=200)
async def evaluate_access(
    body: EvaluateRequest,
    caller: CallerContext = Depends(require_prism_caller),
) -> EvaluateResponse:
    """Evaluate a single authorization question via the PRISM PDP.

    **Usage:**
    - For manual debugging: submit a user_id + action + resource and inspect
      which policy matched (or why the default-deny kicked in).
    - For Golang micro-service integration: the Go worker can POST here instead
      of implementing its own PDP logic.

    **Decision semantics:**
    - `Allow` — at least one identity-policy statement matches AND no Deny overrides
      (and if a permission boundary exists, it must also grant Allow).
    - `Deny` — explicit deny OR no matching Allow OR boundary blocks.

    Requires an active supreme-user Bearer token.
    """
    async with central_session_context() as db:
        try:
            result: PDPResult = await evaluate(
                PDPRequest(
                    user_id=body.user_id,
                    action=body.action,
                    resource_type=body.resource_type,
                    resource_id=body.resource_id,
                    request_context=body.request_context,
                ),
                db,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"PDP evaluation error: {exc}")

    return EvaluateResponse(
        decision=result.decision,
        reason=result.reason,
        matched_policy_id=result.matched_policy_id,
        matched_statement_id=result.matched_statement_id,
        evaluated_policies=result.evaluated_policies,
    )


@router.get("/me/permissions", response_model=MyPermissionsResponse, status_code=200)
async def get_my_permissions(
    caller: CallerContext = Depends(require_any_caller),
) -> MyPermissionsResponse:
    """Return the caller's PRISM permission snapshot from the Redis cache.

    Called by the frontend immediately after login and on session revalidation.
    The snapshot drives sidenav filtering and UI-level access gates.

    Supreme users (is_super=True) always receive static_allows=["*"] regardless
    of their policy assignments — they bypass all PRISM enforcement.

    For regular users, returns the pre-resolved cache built at login time.
    If the cache is missing (first login, Redis restart), rebuilds it on demand.
    """
    # Supreme users bypass all PRISM checks — return full allow immediately
    if caller.is_super:
        return MyPermissionsResponse(
            user_id=caller.user_id,
            is_supreme=True,
            static_allows=["*"],
            static_denies=[],
            needs_full_pdp=False,
            has_boundary=False,
        )

    from core.prism_cache import get_prism_cache, build_prism_cache

    cache = await get_prism_cache(caller.user_id)
    if cache is None:
        # Cache miss — build synchronously on demand (rare: first request after login)
        await build_prism_cache(caller.user_id)
        cache = await get_prism_cache(caller.user_id)

    if cache is None:
        # Redis unavailable or user has no policies — safe deny-all
        return MyPermissionsResponse(
            user_id=caller.user_id,
            is_supreme=False,
            static_allows=[],
            static_denies=[],
            needs_full_pdp=False,
            has_boundary=False,
        )

    return MyPermissionsResponse(
        user_id=caller.user_id,
        is_supreme=False,
        static_allows=cache.get("static_allows", []),
        static_denies=cache.get("static_denies", []),
        needs_full_pdp=bool(cache.get("needs_full_pdp", False)),
        has_boundary=bool(cache.get("has_boundary", False)),
    )

