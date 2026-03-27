"""Router registry for auth v2 endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.auth.handlers.check_contact import router as check_contact_router
from app.modules.auth.handlers.internal_password_changed import router as internal_password_changed_router
from app.modules.auth.handlers.login_employee import router as login_employee_router
from app.modules.auth.handlers.onboarding import router as onboarding_router
from app.modules.auth.handlers.logout import router as logout_router
from app.modules.auth.handlers.me import router as me_router
from app.modules.auth.handlers.permissions_admin import router as permissions_admin_router
from app.modules.auth.handlers.refresh import router as refresh_router
from app.modules.auth.handlers.verify_identity import router as verify_identity_router
from app.modules.auth.handlers.select_role import router as select_role_router

router = APIRouter()

router.include_router(check_contact_router)
router.include_router(onboarding_router)
router.include_router(login_employee_router)
router.include_router(verify_identity_router)
router.include_router(select_role_router)
router.include_router(refresh_router)
router.include_router(logout_router)
router.include_router(me_router)
router.include_router(internal_password_changed_router)
router.include_router(permissions_admin_router)

