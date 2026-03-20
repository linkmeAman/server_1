"""PRISM combined router — aggregates all sub-module routers."""

from fastapi import APIRouter

from controllers.prism.roles import router as roles_router
from controllers.prism.policies import router as policies_router
from controllers.prism.assignments import router as assignments_router
from controllers.prism.attributes import router as attributes_router
from controllers.prism.registry import router as registry_router

router = APIRouter()

router.include_router(roles_router)
router.include_router(policies_router)
router.include_router(assignments_router)
router.include_router(attributes_router)
router.include_router(registry_router)
