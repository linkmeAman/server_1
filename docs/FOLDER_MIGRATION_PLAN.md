# Python Backend Folder Migration Plan

## Goal
Move from mixed legacy layout (`controllers/`, `api/`, `routes/`, `core/`) to a clear application package rooted at `app/` while keeping production-safe compatibility during migration.

## Canonical Structure (Target)

```
backend/python/server_1/
  app/
    api/
      v1/
        router.py
    modules/
      auth/
        handlers/
        schemas/
        services/
        constants.py
        dependencies.py
        legacy_router.py
        router.py
      example/
        router.py
        service.py
      geosearch/
        router.py
        service.py
      llm/
        router.py
        service.py
      orders/
        router.py
      users/
        handlers/
        schemas/
        constants.py
        router.py
      prism/
        assignments.py
        attributes.py
        evaluate.py
        logs.py
        policies.py
        registry.py
        roles.py
        sidenav.py
        router.py
      query_gateway/
        router.py
      sqlgw_admin/
        router.py
    core/
      database.py
      exceptions.py
      loader.py
      middleware.py
      models.py
      prism_cache.py
      prism_guard.py
      prism_pdp.py
      response.py
      router.py
      security.py
      settings.py
      sql_gateway.py
      sqlgw_policy_store.py
      sqlgw_schema.py
    shared/
      response_normalization.py
  controllers/             # temporary compatibility layer (to be removed)
  api/                     # temporary compatibility layer (to be removed)
```

## Migration Strategy
1. Create canonical files under `app/`.
2. Move one bounded module at a time.
3. Replace legacy files with thin wrappers importing canonical modules.
4. Switch runtime imports (`main.py`) to canonical paths.
5. Remove wrappers only after all internal imports are migrated and tested.

## Completed
### Phase 1
- Created canonical package roots:
  - `app/`
  - `app/api/v1/`
  - `app/modules/prism/`
- Migrated PRISM module to canonical path:
  - `app/modules/prism/*`
- Migrated API router to canonical path:
  - `app/api/v1/router.py`
- Updated runtime imports to canonical paths in `main.py`.
- Added compatibility wrappers:
  - `controllers/prism/*.py` -> imports from `app.modules.prism.*`
  - `api/v1/router.py` -> imports from `app.api.v1.router`

### Phase 2
- Migrated auth-v2 module to canonical path:
  - `app/modules/auth/*` (handlers, schemas, services, router, constants, dependencies)
- Replaced auth imports across Python codebase:
  - `controllers.auth.*` -> `app.modules.auth.*`
- Added recursive compatibility wrappers:
  - `controllers/auth/**/*.py` -> imports from `app.modules.auth.*`
- Updated API v1 router to use canonical auth router:
  - `app/api/v1/router.py` now imports `app.modules.auth.router`

### Phase 3
- Migrated users module to canonical path:
  - `app/modules/users/*` (handlers, schemas, router, constants)
- Replaced runtime router import:
  - `app/api/v1/router.py` now imports `app.modules.users.router`
- Added compatibility wrappers:
  - `controllers/users/**/*.py` -> imports from `app.modules.users.*`

### Phase 4
- Migrated employee events and Google Calendar modules to canonical paths:
  - `app/modules/employee_events_v1/*`
  - `app/modules/google_calendar_v1/*`
- Replaced runtime router imports:
  - `app/api/v1/router.py` now imports canonical event/calendar routers
- Added compatibility wrappers:
  - `controllers/employee_events_v1/**/*.py` -> imports from `app.modules.employee_events_v1.*`
  - `controllers/google_calendar_v1/**/*.py` -> imports from `app.modules.google_calendar_v1.*`

### Phase 5
- Migrated remaining live explicit router modules to canonical paths:
  - `app/modules/auth/legacy_router.py`
  - `app/modules/example/*`
  - `app/modules/geosearch/*`
  - `app/modules/llm/*`
  - `app/modules/query_gateway/router.py`
  - `app/modules/sqlgw_admin/router.py`
  - `app/modules/orders/router.py`
- Created shared response normalizer:
  - `app/shared/response_normalization.py`
- Replaced runtime router imports:
  - `app/api/v1/router.py` now imports canonical auth/example/geosearch/llm/query/sqlgw/orders routers
- Added compatibility wrappers:
  - `controllers/api/*.py`, `controllers/internal/sqlgw_admin.py`, `controllers/orders.py`
  - `controllers/example.py`, `controllers/geosearch.py`, `controllers/llm.py`
- Updated cross-module runtime import:
  - `routes/ai_query.py` now imports `app.modules.llm.service`

### Phase 6
- Migrated reusable runtime utilities to canonical path:
  - `app/core/*`
- Replaced runtime imports across application code:
  - `main.py`, `app/**/*`, `scripts/**/*`, `alembic/env.py`
  - `core.*` -> `app.core.*`
- Added compatibility wrappers:
  - `core/*.py` -> aliases to `app.core.*`
- Removed eager router exports from module package roots:
  - `app/modules/*/__init__.py` no longer imports `router`
- Updated router imports to explicit module paths where needed:
  - `app/api/v1/router.py` now imports `employee_events_v1.router` and `google_calendar_v1.router` directly

## Next Phases
- Phase 7: retire legacy wrappers and remove deprecated import paths.

## Phase 7 Progress
- Migrated test suite consumers to canonical imports and patch targets:
  - `controllers.*` -> `app.modules.*`
  - `core.*` -> `app.core.*`
- Decoupled the dynamic runtime from the legacy `controllers/` directory:
  - `app/core/loader.py` now resolves dynamic controllers only from the explicit
    canonical registry.
  - `app/core/router.py` lists controllers from the canonical registry instead of
    scanning the old filesystem layout.
- Legacy wrapper folders still remain for backward compatibility, but they are no
  longer on the main runtime or test path.

## Phase 8 Progress
- Retired the `api/` compatibility package from the repo.
- Retired the `core/` compatibility package from the repo.
- Canonical runtime now depends on:
  - `app/api/*`
  - `app/core/*`
  - `app/modules/*`
- Remaining legacy surface:
  - `controllers/` only, pending final retirement.

## Rules During Migration
- New feature work should import from `app.*` only.
- Legacy paths remain supported temporarily via wrappers.
- Do not remove wrapper files until all imports are migrated and verified.
