"""Central explicit router registry for API v1."""

from fastapi import APIRouter

from controllers.api.auth import router as auth_router
from controllers.auth.router import router as auth_v2_router
from controllers.users.router import router as users_router
from controllers.api.example import router as example_router
from controllers.api.geosearch import router as geosearch_router
from controllers.api.llm import router as llm_router
from controllers.api.query_gateway import router as query_gateway_router
from controllers.employee_events_v1 import router as employee_events_v1_router
from controllers.google_calendar_v1 import router as google_calendar_v1_router
from controllers.internal.sqlgw_admin import router as sqlgw_admin_router
from controllers.orders import router as orders_router
from core.settings import get_settings

api_router = APIRouter()
settings = get_settings()

# 1) Auth routes (root paths)
api_router.include_router(auth_router)
if settings.AUTH_V2_ENABLED:
    api_router.include_router(auth_v2_router)
api_router.include_router(users_router)

# 2) Legacy-standardized wrapper routes
api_router.include_router(example_router)
api_router.include_router(geosearch_router)
api_router.include_router(llm_router)
api_router.include_router(query_gateway_router)
api_router.include_router(employee_events_v1_router)
api_router.include_router(google_calendar_v1_router)
api_router.include_router(sqlgw_admin_router)

# 3) Orders explicit routes
api_router.include_router(orders_router)
