"""PRISM combined router — aggregates all sub-module routers.

All routes in this module require an active supreme-user Bearer token.
The guard dependency is applied once here via include_router(dependencies=[...])
so individual sub-routers stay clean.
"""

from fastapi import APIRouter, Depends

from controllers.prism.assignments import router as assignments_router
from controllers.prism.attributes import router as attributes_router
from controllers.prism.evaluate import router as evaluate_router
from controllers.prism.logs import router as logs_router
from controllers.prism.policies import router as policies_router
from controllers.prism.registry import router as registry_router
from controllers.prism.roles import router as roles_router
from core.prism_guard import require_prism_caller

# Single shared guard dependency injected across every PRISM endpoint
_guard = [Depends(require_prism_caller)]

router = APIRouter()

router.include_router(roles_router,        dependencies=_guard)
router.include_router(policies_router,     dependencies=_guard)
router.include_router(assignments_router,  dependencies=_guard)
router.include_router(attributes_router,   dependencies=_guard)
router.include_router(registry_router,     dependencies=_guard)
router.include_router(logs_router,         dependencies=_guard)
router.include_router(evaluate_router,     dependencies=_guard)
