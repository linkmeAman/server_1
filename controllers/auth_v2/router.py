"""Router registry for auth v2 endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from controllers.auth_v2.handlers.check_contact import router as check_contact_router
from controllers.auth_v2.handlers.internal_password_changed import router as internal_password_changed_router
from controllers.auth_v2.handlers.login_employee import router as login_employee_router
from controllers.auth_v2.handlers.onboarding import router as onboarding_router
from controllers.auth_v2.handlers.logout import router as logout_router
from controllers.auth_v2.handlers.me import router as me_router
from controllers.auth_v2.handlers.permissions_admin import router as permissions_admin_router
from controllers.auth_v2.handlers.refresh import router as refresh_router

router = APIRouter()

router.include_router(check_contact_router)
router.include_router(onboarding_router)
router.include_router(login_employee_router)
router.include_router(refresh_router)
router.include_router(logout_router)
router.include_router(me_router)
router.include_router(internal_password_changed_router)
router.include_router(permissions_admin_router)
