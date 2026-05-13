"""Central explicit router registry for API v1."""

from fastapi import APIRouter

from app.modules.auth.legacy_router import router as auth_router
from app.modules.auth.router import router as auth_v2_router
from app.modules.employee_events_v1.router import router as employee_events_v1_router
from app.modules.example.router import router as example_router
from app.modules.geosearch.router import router as geosearch_router
from app.modules.google_calendar_v1.router import router as google_calendar_v1_router
from app.modules.google_reviews.router import router as google_reviews_router
from app.modules.workforce.router import router as workforce_router
from app.modules.llm.router import router as llm_router
from app.modules.nl2sql.router import router as nl2sql_router
from app.modules.orders.router import router as orders_router
from app.modules.prism.router import router as prism_router
from app.modules.query_gateway.router import router as query_gateway_router
from app.modules.reports.router import router as reports_router
from app.modules.sqlgw_admin.router import router as sqlgw_admin_router
from app.modules.users.router import router as users_router

api_router = APIRouter()

# 1) Auth routes (root paths)
api_router.include_router(auth_router)
api_router.include_router(auth_v2_router)
api_router.include_router(users_router)

# 2) Legacy-standardized wrapper routes
api_router.include_router(example_router)
api_router.include_router(geosearch_router)
api_router.include_router(workforce_router)
api_router.include_router(llm_router)
api_router.include_router(nl2sql_router)
api_router.include_router(query_gateway_router)
api_router.include_router(employee_events_v1_router)
api_router.include_router(google_calendar_v1_router)
api_router.include_router(google_reviews_router)
api_router.include_router(sqlgw_admin_router)
api_router.include_router(reports_router)

# 3) Orders explicit routes
api_router.include_router(orders_router)

# 4) PRISM — Access Control (roles, policies, assignments, attributes, registry)
api_router.include_router(prism_router)


