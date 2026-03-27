"""Router registry for user management endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.users.handlers.manage import router as manage_router

router = APIRouter()
router.include_router(manage_router)
