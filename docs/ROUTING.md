# Routing

## Explicit API Router

Canonical explicit router:

- `app/api/v1/router.py`

This file mounts the current API modules under their own prefixes.

## Current Explicit Modules

- `app/modules/auth/router.py`
- `app/modules/auth/legacy_router.py`
- `app/modules/users/router.py`
- `app/modules/prism/router.py`
- `app/modules/example/router.py`
- `app/modules/geosearch/router.py`
- `app/modules/llm/router.py`
- `app/modules/query_gateway/router.py`
- `app/modules/sqlgw_admin/router.py`
- `app/modules/orders/router.py`
- `app/modules/employee_events_v1/router.py`
- `app/modules/google_calendar_v1/router.py`

## Guidance

- Use explicit FastAPI routers for all new work.
- Do not add new imports to removed compatibility paths.
- Do not rely on any `/py/*` controller/function routes. They were removed.

