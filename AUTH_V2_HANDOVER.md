# Auth V2 / RBAC V2 Handover

Last updated: March 2, 2026

This document captures all RBAC/auth v2 implementation work completed in this branch so development can continue later without re-discovery.

## 1) Scope and Safety Guarantees

Primary objective implemented:
- New auth flow under `/auth/v2/*` in parallel with existing legacy auth.

Safety guarantees currently in code:
- Legacy `POST /login` flow is untouched.
- Legacy tables (`contact`, `employee`, `user`) are not altered by v2 migrations.
- Existing `auth_identity` schema is not altered by v2 migrations.
- V2 router registration is feature-gated at registration time by `AUTH_V2_ENABLED`.

## 2) Implemented File Layout

New v2 module structure:

```text
controllers/auth_v2/
├── __init__.py
├── constants.py
├── dependencies.py
├── router.py
├── handlers/
│   ├── __init__.py
│   ├── check_contact.py
│   ├── login_employee.py
│   ├── refresh.py
│   ├── logout.py
│   ├── me.py
│   └── internal_password_changed.py
├── services/
│   ├── __init__.py
│   ├── audit.py
│   ├── common.py
│   ├── device_fingerprint.py
│   ├── keyring.py
│   ├── session_revocation.py
│   └── token_service.py
└── schemas/
    ├── __init__.py
    └── models.py
```

Other new/updated infrastructure:
- `core/database_v2.py` (async dual-session module for v2 only)
- `alembic/` + `alembic.ini` (new migration stack)
- `scripts/backfill_auth_employee_user_map.py`
- `api/v1/router.py` (feature-gated v2 router registration)
- `main.py` (app-level `AuthV2Error` handler)
- `.env.example`, `core/settings.py` (new config keys)

## 3) Migration Impact

Command:

```bash
alembic -c alembic.ini upgrade head
```

Creates only these new tables in central DB:
1. `auth_refresh_token_v2`
2. `auth_employee_user_map`
3. `rbac_role`
4. `rbac_employee_role`
5. `auth_lock_state_v2`
6. `auth_audit_event_v2`

Alembic metadata table:
- `alembic_version` (created/updated by Alembic)

No `ALTER TABLE` statements for legacy tables are present in v2 revisions.

Revision files:
- `alembic/versions/20260302_001_create_auth_refresh_token_v2.py`
- `alembic/versions/20260302_002_create_auth_employee_user_map.py`
- `alembic/versions/20260302_003_create_rbac_role.py`
- `alembic/versions/20260302_004_create_rbac_employee_role.py`
- `alembic/versions/20260302_005_create_auth_lock_state_v2.py`
- `alembic/versions/20260302_006_create_auth_audit_event_v2.py`

## 4) What Happens to `auth_identity`

`auth_identity` status:
- Not modified by v2 migrations.
- Still used by legacy `/login` flow.
- Also reused by v2 `login-employee` for bcrypt sidecar checks and optional plaintext-to-bcrypt migration.
- V2 refresh/logout/session lifecycle does not use `auth_identity.refresh_token`; it uses `auth_refresh_token_v2`.

## 5) New Settings

Added in `core/settings.py` and `.env.example`:
- DB URLs for v2 async module:
  - `DATABASE_MAIN_URL`
  - `DATABASE_CENTRAL_URL`
- V2 auth settings:
  - `AUTH_V2_ENABLED`
  - `AUTH_V2_ISSUER`
  - `AUTH_V2_AUDIENCE`
  - `AUTH_V2_ACCESS_TOKEN_MINUTES`
  - `AUTH_V2_REFRESH_TOKEN_DAYS`
  - `AUTH_V2_TOKEN_VERSION`
  - `AUTH_V2_CURRENT_KID`
  - `AUTH_V2_SIGNING_KEYS_JSON`
  - `AUTH_V2_REFRESH_HASH_PEPPER`
  - `AUTH_V2_TIMING_FLOOR_MS`
  - `AUTH_V2_TIMING_JITTER_MIN_MS`
  - `AUTH_V2_TIMING_JITTER_MAX_MS`
  - `AUTH_V2_RATE_LIMIT_IP_10M`
  - `AUTH_V2_RATE_LIMIT_IP_MOBILE_10M`
  - `AUTH_V2_RATE_LIMIT_MOBILE_GLOBAL_10M`
  - `AUTH_V2_LOGIN_FAIL_THRESHOLD`
  - `AUTH_V2_LOGIN_FAIL_WINDOW_MINUTES`
  - `AUTH_V2_LOGIN_COOLDOWN_MINUTES`

## 6) Feature Gate Behavior

Router registration (`api/v1/router.py`):
- Always include legacy auth router.
- Include `controllers.auth_v2.router` only when `AUTH_V2_ENABLED=True`.

This keeps v2 code deployed but dark until explicitly enabled.

## 7) V2 Endpoints Implemented

Public v2:
- `POST /auth/v2/check-contact`
- `POST /auth/v2/login-employee`
- `POST /auth/v2/refresh`
- `POST /auth/v2/logout`
- `GET /auth/v2/me`

Internal v2:
- `POST /internal/auth/v2/events/password-changed`

## 8) Error Envelope Contract (v2)

V2 error responses are produced in the exact shape:

```json
{
  "success": false,
  "error": "AUTH_*",
  "message": "...",
  "data": {
    "request_id": "...",
    "details": {}
  },
  "timestamp": "..."
}
```

Implementation points:
- `controllers/auth_v2/services/common.py` (`error_json_response`)
- `main.py` (`AuthV2Error` exception handler)

## 9) Constants and Allowed Revoke Reasons

Central constants file:
- `controllers/auth_v2/constants.py`

Allowed revoke reasons implemented exactly:
- `logout`
- `replay`
- `password_change`
- `employee_inactive`
- `session_family_wipe`

## 10) Token and Keyring Notes

- PASETO library used: `pyseto` (same as existing codebase).
- V2 tokens use footer `kid` and claim `auth_ver=2`.
- Keyring source: `AUTH_V2_SIGNING_KEYS_JSON`.
- Rotation helpers: `get_current_key()`, `get_key_for_kid()`.
- Footer note documented in code: PASETO footer is authenticated but not encrypted.

## 11) Backfill Script

File:
- `scripts/backfill_auth_employee_user_map.py`

Usage:

```bash
python scripts/backfill_auth_employee_user_map.py --dry-run --batch-size=500
python scripts/backfill_auth_employee_user_map.py --batch-size=500 --sleep-ms=50
```

Behavior:
- Reads legacy `employee`/`contact` from main DB.
- Writes to central `auth_employee_user_map`.
- Idempotent upsert semantics.
- Conflict detection (employee->multiple users or user->multiple employees) with skip+log.
- Final summary counts printed.

## 12) Testing Status

Added test modules:
- `tests/test_auth_v2_check_contact.py`
- `tests/test_auth_v2_login_employee.py`
- `tests/test_auth_v2_refresh.py`
- `tests/test_auth_v2_logout.py`
- `tests/test_auth_v2_me.py`
- `tests/test_auth_v2_dependency.py`
- `tests/test_auth_v2_key_rotation.py`
- `tests/test_auth_v2_internal_password_changed.py`
- `tests/test_auth_v2_central_db_down_matrix.py`
- `tests/test_auth_v2_legacy_login_regression.py`
- shared helper: `tests/auth_v2_test_utils.py`

Current local execution blockers observed in environment:
- `httpx` missing (required by `fastapi.testclient`).
- `pydantic_settings` missing.

## 13) Migration Troubleshooting Notes

### Issue A: `python -m alembic ...` fails
Cause:
- Local folder `alembic/` shadows module path.
Fix:
- Use CLI binary:

```bash
alembic -c alembic.ini upgrade head
```

### Issue B: `1045 Access denied for user ...`
Observed pattern:
- URL prints correctly, but DB auth fails for async connector.
Likely causes:
- Wrong effective password in env precedence.
- TCP auth mismatch (`localhost` socket vs async TCP).

Recommended checks:

```bash
python - <<'PY'
from core.database_v2 import get_central_async_engine
print(get_central_async_engine().url.render_as_string(hide_password=True))
PY

mysql --protocol=TCP -h 127.0.0.1 -u developer -p -D pf_central -e "SELECT 1;"
```

## 14) Resume Checklist

When resuming work:
1. Activate venv and install missing deps.
2. Confirm `DATABASE_CENTRAL_URL`/`DB_*` values and DB permissions.
3. Apply Alembic migrations on non-production DB first.
4. Run backfill dry-run and inspect conflicts.
5. Enable `AUTH_V2_ENABLED=True` only in test env.
6. Smoke test `/auth/v2/*` and verify legacy `/login` unchanged.
7. Run auth v2 tests after dependency install.

## 15) Important Legacy Compatibility Notes

- Legacy `/login`, `/refresh`, `/logout`, `/forgot-password`, `/reset-password` remain under `controllers/api/auth.py`.
- Existing sync DB module `core/database.py` remains untouched for legacy paths.
- V2 async DB path is isolated in `core/database_v2.py`.

## 16) AuthZ V2 Expansion (March 6, 2026)

Implemented additive authz expansion artifacts:
- New migrations:
  - `20260306_007_create_rbac_resource_v2.py`
  - `20260306_008_create_rbac_role_permission_v2.py`
  - `20260306_009_create_rbac_position_department_role_v2.py`
  - `20260306_010_seed_rbac_resource_v2_defaults.py`
- Shared resolver: `controllers/auth_v2/services/authorization.py`
- Auth flow integration updates:
  - `check-contact`: returns `position_id`, `position`, `department_id`, `department` per employee
  - `login-employee`: resolves effective roles/permissions and emits expanded response + token claims
  - `refresh`: recomputes effective roles/permissions on every call; does not trust old token claims
  - `me`: token-only, backward-compatible defaults for missing permission claims
- Internal super-admin APIs:
  - `controllers/auth_v2/handlers/permissions_admin.py`
  - prefix: `/internal/auth/v2/permissions`
- Bootstrap script:
  - `scripts/auth_v2/bootstrap_auth_v2_super_admin.py`
- Manifest + validator:
  - `authz/resources_manifest.yaml`
  - `scripts/auth_v2/validate_authz_manifest.py`
  - CI hook: `.github/workflows/authz-manifest.yml`
- Added authz-focused tests under `tests/auth_v2/`.

## 17) Legacy Schema Protection

This release remains strictly additive with respect to legacy schema.

No migration in this release may modify the structure of existing legacy tables, including but not limited to:
- `employee`
- `contact`
- `user`
- `rbac_role`
- `rbac_employee_role`

Legacy tables may be read from, and existing supported rows may be referenced/upserted where current schema already allows, but their columns, constraints, indexes, and data shape must remain unchanged.

All Alembic revisions for this authz expansion target only newly introduced authz tables and seed data.

Migration review gate:
- verify no `op.alter_column`/`op.add_column`/`op.drop_column` against legacy tables
- verify DDL targets only new authz artifacts
- rollback drops only newly introduced authz artifacts
