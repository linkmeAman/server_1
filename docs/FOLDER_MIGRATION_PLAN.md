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
    core/                  # planned migration target for current core/
    shared/                # planned for common DTO/helpers
  controllers/             # temporary compatibility layer (to be removed)
  api/                     # temporary compatibility layer (to be removed)
```

## Migration Strategy
1. Create canonical files under `app/`.
2. Move one bounded module at a time.
3. Replace legacy files with thin wrappers importing canonical modules.
4. Switch runtime imports (`main.py`) to canonical paths.
5. Remove wrappers only after all internal imports are migrated and tested.

## Completed In This Change (Phase 1)
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

## Next Phases
- Phase 2: move `controllers/auth` to `app/modules/auth`.
- Phase 3: move `controllers/users` to `app/modules/users`.
- Phase 4: move `controllers/employee_events_v1` and `controllers/google_calendar_v1` to `app/modules/*`.
- Phase 5: move reusable runtime utilities from `core/` into `app/core/` (with compatibility wrappers).
- Phase 6: retire legacy wrappers and remove deprecated import paths.

## Rules During Migration
- New feature work should import from `app.*` only.
- Legacy paths remain supported temporarily via wrappers.
- Do not remove wrapper files until all imports are migrated and verified.
