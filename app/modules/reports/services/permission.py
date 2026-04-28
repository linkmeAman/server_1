"""PRISM-backed report authorization helpers."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.prism_guard import CallerContext
from app.core.prism_pdp import PDPRequest, evaluate
from app.modules.reports.schemas.models import ReportDefinition


class ReportPermissionService:
    """Enforces report access with PRISM as the authority."""

    async def can_view(
        self,
        caller: CallerContext,
        central_db: AsyncSession,
        definition: ReportDefinition,
    ) -> bool:
        return await self._is_allowed_any(
            caller,
            central_db,
            self._view_actions(definition),
            resource_id=definition.slug,
        )

    async def require_view(
        self,
        caller: CallerContext,
        central_db: AsyncSession,
        definition: ReportDefinition,
    ) -> None:
        if await self.can_view(caller, central_db, definition):
            return
        raise HTTPException(status_code=403, detail="Report access denied")

    async def require_manage(
        self,
        caller: CallerContext,
        central_db: AsyncSession,
    ) -> None:
        if await self._is_allowed_any(caller, central_db, ["reports:manage", "reports:write"], resource_id="admin"):
            return
        raise HTTPException(status_code=403, detail="Report management permission required")

    async def action_allowed(
        self,
        caller: CallerContext,
        central_db: AsyncSession,
        *,
        definition: ReportDefinition,
        action_key: str,
        declared_permission: str | None,
    ) -> bool:
        actions = [
            declared_permission,
            f"{definition.prism_resource_code}:action.{action_key}",
        ]
        return await self._is_allowed_any(
            caller,
            central_db,
            [item for item in actions if item],
            resource_id=definition.slug,
        )

    @staticmethod
    def _view_actions(definition: ReportDefinition) -> list[str]:
        actions = [
            f"{definition.prism_resource_code}:view",
            f"{definition.prism_resource_code}:read",
            "reports:view",
            "reports:read",
        ]
        if definition.legacy_view_action:
            actions.append(definition.legacy_view_action)
        return actions

    async def _is_allowed_any(
        self,
        caller: CallerContext,
        central_db: AsyncSession,
        actions: list[str],
        *,
        resource_id: str,
    ) -> bool:
        if caller.is_super:
            return True

        normalized = [action for action in actions if action]
        if not normalized:
            return False

        token_permissions = set()
        raw_permissions = caller.token_claims.get("permissions")
        if isinstance(raw_permissions, list):
            token_permissions = {str(item) for item in raw_permissions if item}

        for action in normalized:
            if action in token_permissions:
                return True
            try:
                result = await evaluate(
                    PDPRequest(
                        user_id=int(caller.user_id),
                        action=action,
                        resource_type="reports",
                        resource_id=resource_id,
                        request_context={
                            "reportSlug": resource_id,
                            "employeeId": caller.employee_id,
                            "contactId": caller.contact_id,
                        },
                    ),
                    central_db,
                )
            except Exception:
                continue
            if result.decision == "Allow":
                return True

        return False
