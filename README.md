# MARKX Python Backend

Canonical Python backend layout is now rooted at `app/`.

Use Python 3.10+ for local development. From the workspace root you can prepare
this service with:

```bash
bash scripts/setup-dev.sh python
bash scripts/dev-python.sh
```

## Structure

```text
backend/python/server_1/
  app/
    api/v1/router.py
    core/
    modules/
    shared/
  alembic/
  docs/
  routes/
  scripts/
  tests/
  main.py
```

## Key points

- Explicit FastAPI routes are registered from `app/api/v1/router.py`.
- Shared runtime utilities live in `app/core`.
- Domain code lives in `app/modules`.
- Dynamic `/py/*` routing was removed.
- Legacy `controllers/`, `core/`, and `api/` compatibility packages were removed.

## Main docs

- `docs/ARCHITECTURE.md`
- `docs/AUTH_AND_SECURITY.md`
- `docs/ROUTING.md`
- `docs/TESTING_ON_SERVER.md`
- `docs/FOLDER_MIGRATION_PLAN.md`

## Local run

```bash
cd backend/python/server_1
python -m uvicorn main:app --host 127.0.0.1 --port 8010
```

## Server run

Use your `systemd` unit to run `uvicorn main:app` from the project root with the project `.env` loaded.
