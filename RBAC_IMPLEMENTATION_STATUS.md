# RBAC Implementation Status (Current State)

Last updated: March 11, 2026

This document consolidates the current RBAC/authz implementation in this codebase: what exists, how it was created, how it connects at runtime, and what is still pending.

## 1) Scope Implemented So Far

The RBAC implementation is delivered as part of `auth v2` and includes:

- Role master (`rbac_role`) and direct employee-role mapping (`rbac_employee_role`).
- Resource catalog (`rbac_resource_v2`) with hierarchy support.
- Role-to-resource action flags (`rbac_role_permission_v2`).
- Position+department pair-based role mapping (`rbac_position_department_role_v2`).
- Runtime resolver to compute effective roles and flattened permissions.
- Access/refresh token claim expansion to carry org + RBAC claims.
- Internal super-admin endpoints for RBAC data management.
- Bootstrap and validation scripts (super-admin bootstrap, manifest validator).
- Tests for resolver, admin APIs, migration safety, flow behavior, and outage behavior.

## 2) Current Code Structure

### Core RBAC/Auth V2 module layout

```text
controllers/auth_v2/
├── constants.py
├── dependencies.py
├── router.py
├── schemas/models.py
├── services/
│   ├── authorization.py
│   ├── token_service.py
│   ├── keyring.py
│   ├── session_revocation.py
│   ├── audit.py
│   └── common.py
└── handlers/
    ├── check_contact.py
    ├── login_employee.py
    ├── refresh.py
    ├── logout.py
    ├── me.py
    ├── internal_password_changed.py
    └── permissions_admin.py
```

### Supporting RBAC/authz files

- Migrations: `alembic/versions/20260302_003...20260306_010`.
- Alembic env for central DB only: `alembic/env.py`.
- Resource manifest: `authz/resources_manifest.yaml`.
- Manifest validator: `scripts/auth_v2/validate_authz_manifest.py`.
- Super-admin bootstrap: `scripts/auth_v2/bootstrap_auth_v2_super_admin.py`.
- Employee-user map backfill: `scripts/backfill_auth_employee_user_map.py`.
- CI check: `.github/workflows/authz-manifest.yml`.

## 3) How the Structure Was Created (Migration History)

Revision chain (single line):

`20260302_001 -> 002 -> 003 -> 004 -> 005 -> 006 -> 20260306_007 -> 008 -> 009 -> 010`

### Auth v2 foundation tables (prerequisite for RBAC flow)

1. `20260302_001_create_auth_refresh_token_v2.py`
- Creates `auth_refresh_token_v2` for rotation/replay/session lifecycle.

2. `20260302_002_create_auth_employee_user_map.py`
- Creates `auth_employee_user_map` (employee <-> user linkage for login-employee).

3. `20260302_005_create_auth_lock_state_v2.py`
- Creates `auth_lock_state_v2` for login cooldown/lockouts.

4. `20260302_006_create_auth_audit_event_v2.py`
- Creates `auth_audit_event_v2` for auth/RBAC audit events and rate-limit counting.

### RBAC tables

1. `20260302_003_create_rbac_role.py`
- Creates `rbac_role`:
  - `id`, `code` (unique), `name`, `description`, `is_active`
  - audit metadata (`created_at`, `modified_at`, actor fields, `source`)

2. `20260302_004_create_rbac_employee_role.py`
- Creates `rbac_employee_role`:
  - `(employee_id, role_id)` unique
  - FK `role_id -> rbac_role.id`
  - `is_active` plus metadata

3. `20260306_007_create_rbac_resource_v2.py`
- Creates `rbac_resource_v2`:
  - `code` unique, `name`, `parent_id` self FK, `sort_order`, `meta`, `is_active`
  - actor metadata includes both `*_by_user_id` and `*_by_employee_id`

4. `20260306_008_create_rbac_role_permission_v2.py`
- Creates `rbac_role_permission_v2`:
  - unique `(role_id, resource_id)`
  - flags: `can_view`, `can_add`, `can_edit`, `can_delete`, `can_super`
  - FK `role_id -> rbac_role.id`, FK `resource_id -> rbac_resource_v2.id`

5. `20260306_009_create_rbac_position_department_role_v2.py`
- Creates `rbac_position_department_role_v2`:
  - unique `(position_id, department_id, role_id)`
  - FK `role_id -> rbac_role.id`
  - stores `is_active` plus metadata

6. `20260306_010_seed_rbac_resource_v2_defaults.py`
- Seeds default resources and parent-child relations (`global`, `boards.*`, `reports.*`).

## 4) RBAC Data Model and Connections

### Main role-grant edges

1. Direct grant:
- `employee.id` -> `rbac_employee_role.employee_id` -> `rbac_role.id`

2. Org-pair grant:
- `employee.position_id + employee.department_id` ->
  `rbac_position_department_role_v2(position_id, department_id, role_id)` -> `rbac_role.id`

3. Permission grant:
- `rbac_role.id` -> `rbac_role_permission_v2(role_id, resource_id, action_flags)` -> `rbac_resource_v2.code`

### Permission code flattening

`AuthorizationResolver` converts row-level flags into codes:

- `<resource_code>:view`
- `<resource_code>:add`
- `<resource_code>:edit`
- `<resource_code>:delete`
- `<resource_code>:super` (allowed only for `resource_code == "global"`)

`is_super = true` only when `global:super` exists.

## 5) Runtime Authorization Resolver (Single Source of Truth)

File: `controllers/auth_v2/services/authorization.py`

`resolve_employee_authorization(employee_id)` does:

1. Load active employee and org context (position/department).
2. Resolve direct roles (`rbac_employee_role` + `rbac_role`).
3. Resolve pair roles (`rbac_position_department_role_v2` + `rbac_role`).
4. Union/dedupe roles by `role_id`.
5. Load active role-permission rows (`rbac_role_permission_v2` + `rbac_resource_v2`).
6. Flatten/dedupe permission codes.
7. Compute `permissions_version` from max `modified_at` epoch across:
   - employee-role rows for employee
   - pair-role rows for employee’s position+department
   - role-permission rows for effective roles
   - resource rows for touched resources
8. Return:
   - org context
   - `roles`
   - `permissions`
   - `grants_trace` (role and permission provenance)
   - `is_super`
   - `permissions_version`
   - `permissions_schema_version` (currently `1`)

## 6) Token/Claims Integration

File: `controllers/auth_v2/services/token_service.py`

On login and refresh, token claims include:

- identity: `sub`, `user_id`, `contact_id`, `employee_id`, `mobile`, `jti`
- token metadata: `typ`, `iat`, `exp`, `iss`, `aud`, `auth_ver`
- org context: `position_id`, `position`, `department_id`, `department`
- RBAC claims:
  - `roles` (list of `{role_code, role_name}` objects)
  - `permissions` (flattened list)
  - `is_super`
  - `permissions_version`
  - `permissions_schema_version`

Key details:

- PASETO `v4.local` with `kid` in footer.
- Keyring loaded from `AUTH_V2_SIGNING_KEYS_JSON`.
- Access and refresh both carry RBAC claims.
- Refresh path recomputes authorization from DB; it does not trust old token permissions.

## 7) Endpoint-Level RBAC Connection

### Router registration

- File: `api/v1/router.py`
- `auth_v2` router is included only when `AUTH_V2_ENABLED=True`.

### Public auth v2 endpoints

1. `POST /auth/v2/check-contact`
- Uses main DB for contact+employee lookup.
- Uses resolver org lookup for position/department names.
- No role/permission assignment yet; this is discovery + anti-enumeration/rate-limit flow.

2. `POST /auth/v2/login-employee`
- Validates main identity + central mapping (`auth_employee_user_map` + `user`).
- Resolves effective RBAC via `AuthorizationResolver`.
- Issues tokens with RBAC claims.
- Persists refresh session in `auth_refresh_token_v2`.

3. `POST /auth/v2/refresh`
- Verifies refresh token + one-time-use semantics.
- Detects replay and revokes session family.
- Recomputes effective RBAC from DB.
- Returns fresh tokens and fresh RBAC claims.

4. `GET /auth/v2/me`
- Token-only response (no DB read).
- Returns claims already present in access token.

5. `POST /auth/v2/logout`
- Revokes current token/session family.
- No role computation here.

### Internal RBAC admin endpoints

All endpoints are under:

- `/internal/auth/v2/permissions/*`
- Guard: `require_v2_super_auth` (requires `is_super == true` in access token).

Implemented operations:

- Resources:
  - `GET /resources`
  - `POST /resources`
  - `PATCH /resources/{resource_id}`
- Roles:
  - `GET /roles`
- Role permissions:
  - `GET /role-permissions`
  - `PUT /role-permissions`
  - `DELETE /role-permissions`
- Position-department mappings:
  - `GET /position-department-roles`
  - `PUT /position-department-roles`
  - `DELETE /position-department-roles/{mapping_id}`
- Effective authz inspect:
  - `GET /effective/{employee_id}`

Concurrency/consistency pattern:

- Mutating endpoints require `expected_modified_at`.
- Version mismatch returns `409` (`AUTH_BAD_REQUEST`).
- Deletions are soft (`is_active=0`), not hard deletes.
- Admin actions are logged via `AUTHZ_ADMIN` log entries with before/after snapshots.

## 8) Resource Catalog + Manifest + CI

Manifest file:

- `authz/resources_manifest.yaml`

Validator:

- `scripts/auth_v2/validate_authz_manifest.py`
- Validates:
  - code format
  - duplicate codes
  - parent existence
  - optional DB drift (`--check-db`)

CI:

- `.github/workflows/authz-manifest.yml`
- Runs validator on manifest/script changes.

## 9) Operational/Bootstrap Scripts

### Super-admin bootstrap

File:

- `scripts/auth_v2/bootstrap_auth_v2_super_admin.py`

What it does:

1. Validates active employee in main DB.
2. Resolves or creates role.
3. Ensures `global` resource exists.
4. Upserts `can_super=1` permission for `(role, global_resource)`.
5. Upserts direct employee-role mapping.
6. Resolves final effective authz and prints summary.

### Employee-user mapping backfill

File:

- `scripts/backfill_auth_employee_user_map.py`

Purpose:

- Populate/repair `auth_employee_user_map` from legacy `employee/contact/user` data.
- Supports dry run, batching, conflict reporting, idempotent upsert.

## 10) Where RBAC Claims Are Consumed Outside Auth V2

1. Employee Events API auth dependency:
- File: `controllers/employee_events_v1/dependencies.py`
- Accepts both legacy and auth-v2 tokens.
- For auth-v2 tokens, uses `verify_v2_access_token`.
- Current behavior: token validation only; no per-resource permission enforcement.

2. SQLGW internal admin:
- File: `controllers/internal/sqlgw_admin.py`
- Uses legacy `validate_token` and role/permission string checks (`sqlgw_admin`, `sqlgw_approver`).
- This is separate from `require_v2_super_auth`.

## 11) Tests Implemented for RBAC/AuthZ

### Core authz logic and tooling

- `tests/auth_v2/test_authorization_service.py`
- `tests/auth_v2/test_permissions_admin.py`
- `tests/auth_v2/test_auth_flows_org_permissions.py`
- `tests/auth_v2/test_bootstrap_super_admin.py`
- `tests/auth_v2/test_manifest_validator.py`
- `tests/auth_v2/test_migration_audit.py`
- `tests/auth_v2/test_auth_v2_central_down_behavior.py`

### Auth-v2 flow tests (top-level)

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

What is covered:

- Role/permission flattening and dedupe.
- `global:super` semantics.
- Login/refresh claim expansion.
- Recompute-on-refresh behavior.
- Super-admin guard behavior.
- Migration safety constraints.
- Manifest structural validation.
- Central DB outage behavior matrix.
- Key rotation and token-size guard.

## 12) Current Known Behaviors / Gaps

1. Feature-gated exposure:
- Auth v2 routes are dark unless `AUTH_V2_ENABLED=True`.

2. `/auth/v2/me` is token-state view:
- It does not re-read DB; changed permissions appear after refresh/login.

3. Business route permission enforcement is not yet generalized:
- Current non-auth routes mostly validate token presence/family, not fine-grained RBAC permission codes.

4. SQLGW admin is still on legacy token validation path:
- It is not wired to `require_v2_auth` / `require_v2_super_auth` yet.

5. `DELETE /internal/auth/v2/permissions/role-permissions` has a schema mismatch bug:
- In `controllers/auth_v2/handlers/permissions_admin.py`, it checks `payload.position_id` and `payload.department_id`, but `RolePermissionDeleteRequest` does not define these fields.
- Current net effect: request can fall into generic exception path and return `503 AUTH_SERVICE_UNAVAILABLE`.

## 13) End-to-End Connection Summary

### Login path

`check-contact -> login-employee -> AuthorizationResolver -> token issuance`

Data linkage:

- `contact/mobile` + `employee_id` validated in main DB.
- `auth_employee_user_map` links employee to central `user`.
- resolver reads role edges and permission edges.
- access/refresh tokens include computed RBAC claims.

### Refresh path

`refresh token -> anti-replay/session checks -> AuthorizationResolver -> new claims`

Important:

- effective permissions are recomputed each refresh cycle.
- replay triggers session-family revocation.

### Admin path

`super access token (is_super=true) -> permissions_admin CRUD -> DB rows updated -> next refresh/login reflects changes`

## 14) Minimal Runbook (Current)

1. Apply migrations:
- `alembic -c alembic.ini upgrade head`

2. Backfill employee-user mappings (recommended first in non-prod):
- `python scripts/backfill_auth_employee_user_map.py --dry-run --batch-size=500`

3. Bootstrap first super admin:
- `python scripts/auth_v2/bootstrap_auth_v2_super_admin.py --employee-id <id> --role-code <code> --create-role-if-missing --role-name "<name>"`

4. Validate resource manifest:
- `python scripts/auth_v2/validate_authz_manifest.py --manifest authz/resources_manifest.yaml`

5. Enable routes in environment:
- `AUTH_V2_ENABLED=True`

