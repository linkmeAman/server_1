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

from core.database_v2 import central_session_context
from core.prism_guard import CallerContext, require_prism_caller
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
