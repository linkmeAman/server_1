# Architecture

## Services

This repository contains multiple services, but this document covers the Python backend at `backend/python/server_1`.

## Canonical Python Layout

```text
app/
  api/
    v1/
      router.py
  core/
    database.py
    middleware.py
    models.py
    prism_cache.py
    prism_guard.py
    prism_pdp.py
    response.py
    security.py
    settings.py
    sql_gateway.py
    sqlgw_policy_store.py
    sqlgw_schema.py
  modules/
    auth/
    employee_events_v1/
    example/
    geosearch/
    google_calendar_v1/
    llm/
    orders/
    prism/
    query_gateway/
    sqlgw_admin/
    users/
  shared/
```

## Routing Model

The backend now uses explicit FastAPI routes only.

Canonical route registration lives in:

- `app/api/v1/router.py`

## Database Model

- Main business database: default SQLAlchemy bind
- Central/auth database: separate bind for auth and PRISM-related identity data
- Sync SQLAlchemy engine is used for some legacy/read paths
- Async SQLAlchemy sessions are used for auth-v2 and newer request flows

## Security Model

- Auth uses PASETO-based tokens
- PRISM enforces permission checks
- Redis is optional but expected for PRISM caching in production

## Migration State

The old compatibility layers were removed:

- `controllers/`
- `core/`
- `api/`

Only canonical `app/*` imports should be used from now on.
