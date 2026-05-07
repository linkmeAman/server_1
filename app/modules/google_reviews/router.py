"""Google Reviews v1 router — aggregates all handlers."""

from __future__ import annotations

from fastapi import APIRouter

from .handlers.analytics import router as analytics_router
from .handlers.locations import router as locations_router
from .handlers.reviews import router as reviews_router
from .handlers.sync import router as sync_router
from .handlers.trends import router as trends_router

router = APIRouter(prefix="/api/google-reviews/v1", tags=["google-reviews-v1"])

router.include_router(locations_router)
router.include_router(sync_router)
router.include_router(reviews_router)
router.include_router(analytics_router)
router.include_router(trends_router)
