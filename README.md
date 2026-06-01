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
- Universal realtime notifications live in `app/modules/notifications` and expose SSE, DB-backed recent events, read/clear state, and delivery preferences under `/api/notifications/v1`.
- Dynamic `/py/*` routing was removed.
- Legacy `controllers/`, `core/`, and `api/` compatibility packages were removed.

## Main docs

- `docs/ARCHITECTURE.md`
- `docs/AUTH_AND_SECURITY.md`
- `docs/NL2SQL_BACKEND_INTEGRATION.md`
- `docs/NL2SQL_API_INTEGRATION_FLOW.md`
- `docs/NOTIFICATION_SYSTEM.md`
- `docs/REPORT_PLATFORM_BACKEND_CHANGES.md`
- `docs/ROUTING.md`
- `docs/TESTING_ON_SERVER.md`
- `docs/FOLDER_MIGRATION_PLAN.md`

## Local run

```bash
cd backend/python/server_1
python -m uvicorn main:app --host 127.0.0.1 --port 8010
```

## Migrations

Run Alembic before starting code that depends on new tables:

```bash
alembic upgrade head
```

By default, Alembic upgrades against the central database (`pf_central`).
Tenant notification migrations are schema-qualified and will still write to the
main tenant DB resolved from `DATABASE_MAIN_URL` or `DB_NAME`. Set that main DB
to `pf_TickleRight_9210` or the equivalent tenant DSN before running upgrades.

Use the main DB as the Alembic connection target only when a migration is
explicitly meant to run there:

```bash
alembic -x db_target=main upgrade head
```

The notification system requires migrations `20260526_011_notifications.py`
and `20260527_012_notification_rules_followup.py`, which create:

- `notification_event`
- `notification_user_state`
- `notification_user_preference`
- `notification_delivery_rule`
- `notification_dispatch_ledger`

The backend production workflow runs `alembic upgrade head` before restarting
`py-server-1`. If migration fails, `set -e` stops the deployment and leaves the
running service untouched.

## Server run

Use your `systemd` unit to run `uvicorn main:app` from the project root with the project `.env` loaded.
