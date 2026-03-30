# Python Backend Folder Migration Status

## Goal
Move the Python backend from the old mixed layout into a single canonical `app/`
package with explicit routers and no dynamic controller discovery.

## Final Canonical Structure

```text
backend/python/server_1/
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
      workforce/
    shared/
      response_normalization.py
  routes/
  scripts/
  tests/
  main.py
```

## Completed Migration

1. Moved API routing to `app/api/v1/router.py`.
2. Moved reusable runtime utilities to `app/core/*`.
3. Moved feature areas to `app/modules/*`.
4. Removed legacy `api/`, `controllers/`, and `core/` compatibility packages.
5. Removed the dynamic `/py/*` controller routing methodology.
6. Standardized the backend on explicit FastAPI routers only.
7. Added the `workforce` module as the neutral employee-management namespace.

## Rules Going Forward

1. Import from `app.*` only.
2. Do not recreate `api/`, `controllers/`, or `core/` at repo root.
3. Do not reintroduce dynamic controller loading or `/py/*` routes.
4. New backend features should live under `app/modules/<feature>/`.

## Validation Checklist

Run these after structural changes:

```bash
python -m compileall app tests main.py routes scripts alembic
python -m pytest tests -q
```
