"""PRISM combined router — aggregates all sub-module routers.

Management routes require an active supreme-user Bearer token.
The guard dependency is applied once here via include_router(dependencies=[...])
so individual sub-routers stay clean.

Exceptions (per-endpoint auth):
  - evaluate_router: GET /me/permissions uses require_any_caller (any authenticated
    employee may fetch their own snapshot); POST / uses require_prism_caller (supreme only).
  - sidenav_router: GET uses require_any_caller, PUT uses require_prism_caller.
"""

from fastapi import APIRouter, Depends

from app.modules.prism.assignments import router as assignments_router
from app.modules.prism.attributes import router as attributes_router
from app.modules.prism.evaluate import router as evaluate_router
from app.modules.prism.logs import router as logs_router
from app.modules.prism.policies import router as policies_router
from app.modules.prism.registry import router as registry_router
from app.modules.prism.roles import router as roles_router
from app.modules.prism.sidenav import router as sidenav_router
from core.prism_guard import require_prism_caller

# Shared guard dependency for management-only endpoints
_guard = [Depends(require_prism_caller)]

router = APIRouter()

router.include_router(roles_router,        dependencies=_guard)
router.include_router(policies_router,     dependencies=_guard)
router.include_router(assignments_router,  dependencies=_guard)
router.include_router(attributes_router,   dependencies=_guard)
router.include_router(registry_router,     dependencies=_guard)
router.include_router(logs_router,         dependencies=_guard)
# Evaluate uses per-endpoint auth — each handler declares require_any_caller or
# require_prism_caller individually, so no blanket guard is applied here.
router.include_router(evaluate_router)
# Sidenav uses per-endpoint auth (GET = any caller, PUT = supreme only)
router.include_router(sidenav_router)
