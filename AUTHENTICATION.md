# Authentication Guide (PASETO + Hybrid Legacy Migration)

This document explains the current authentication design, data flow, and API contracts for future development.

## Auth V2 Note

Parallel RBAC/auth v2 work is documented separately in:

- `AUTH_V2_HANDOVER.md`

This file remains focused on the legacy `/login` family behavior.

## 1) Core Intent

- Keep legacy `user` table schema untouched.
- Store all new authentication state in sidecar table `auth_identity`.
- Support seamless migration from legacy plaintext password verification to bcrypt-based verification.
- Use PASETO v4.local for access/refresh tokens.

## 2) Database Architecture

The app uses a dual-database setup:

- Main DB: business data (default bind).
- Central DB: legacy `user` table + new `auth_identity` table.

Relevant code:

- `core/database.py`
- `core/models.py`

### Legacy table (read-only for schema changes)

Model: `User` (`core/models.py`)

Used fields:

- `id`
- `mobile`
- `password` (legacy plaintext source)
- `inactive`

### New sidecar table

Model: `AuthIdentity` (`core/models.py`)

Columns:

- `user_id` (PK, FK -> `user.id`)
- `password_hash` (bcrypt)
- `refresh_token` (latest valid refresh token)
- `reset_token`
- `reset_token_expires_at`
- `created_at`

### Table creation behavior

At startup (`init_database` in `core/database.py`), only the sidecar table is created if missing:

- `AuthIdentity.__table__.create(..., checkfirst=True)`

No migration or alteration is performed on legacy `user`.

## 3) Token & Password Security

Implemented in `core/security.py`:

- Password hashing: `bcrypt`
- Token format: PASETO `v4.local`
- Access token + refresh token generation
- Refresh token validation
- Reset token generation

Note:

- PASETO tokens do not modify database passwords.
- Password values in legacy `user.password` are not rewritten by auth routes.

## 4) Endpoint Base URL

Router file: `controllers/api/auth.py`

Defined paths:

- `POST /login`
- `POST /refresh`
- `POST /logout`
- `POST /forgot-password`
- `POST /reset-password`

If deployed behind Nginx `/py` proxy prefix:

- Public URLs become `/py/login`, `/py/refresh`, etc.

## 5) Request/Response Contracts

## `POST /login`

Request:

```json
{
  "mobile": "9876543210",
  "password": "your_password"
}
```

Response:

```json
{
  "access_token": "....",
  "refresh_token": "....",
  "token_type": "Bearer"
}
```

## `POST /refresh`

Request:

```json
{
  "refresh_token": "...."
}
```

Response:

```json
{
  "access_token": "....",
  "refresh_token": "....",
  "token_type": "Bearer"
}
```

## `POST /logout`

Request:

```json
{
  "refresh_token": "...."
}
```

Response:

```json
{
  "success": true,
  "message": "Logged out successfully"
}
```

## `POST /forgot-password`

Request:

```json
{
  "mobile": " "
}
```

Response:

```json
{
  "success": true,
  "message": "If this account exists, a reset message has been sent."
}
```

Behavior:

- Generates reset token.
- Stores token + expiry in `auth_identity`.
- Prints mock notification to server console:
  - `Mock Email Sent to <mobile>: reset token=<token>`

## `POST /reset-password`

Request:

```json
{
  "token": "token-from-forgot-password",
  "new_password": "NewStrongPass123"
}
```

Response:

```json
{
  "success": true,
  "message": "Password reset successful"
}
```

## 6) Hybrid Login Flow (Important)

Function: `authenticate_user` in `controllers/api/auth.py`.

Steps:

1. Find active user by `mobile` in legacy `user` table (`inactive=0`).
2. Lookup sidecar row in `auth_identity` by `user_id`.
3. If sidecar exists:
   - Verify incoming password against `auth_identity.password_hash` (bcrypt).
4. If sidecar does not exist:
   - Compare incoming password with legacy `user.password` (plaintext check).
   - If matched, create `auth_identity` and save bcrypt hash (auto-migration).
5. Issue PASETO access + refresh tokens.
6. Save latest refresh token in `auth_identity.refresh_token`.

This means first successful login can migrate a user silently.

## 7) Password Storage Rules

After deployment:

- Existing users may still have old values in `user.password`.
- New secure source of truth for authenticated users is `auth_identity.password_hash`.
- `reset-password` updates only `auth_identity.password_hash`.
- Legacy `user.password` is not updated by this auth module.

## 8) What Uses Which Table

`/login`:

- Reads `user` always.
- Reads/writes `auth_identity`.

`/refresh`:

- Reads `auth_identity` and validates token.
- Reads `user` for active check.
- Updates `auth_identity.refresh_token`.

`/logout`:

- Updates `auth_identity.refresh_token = NULL`.

`/forgot-password`:

- Reads `user`.
- Writes `auth_identity.reset_token` and expiry.

`/reset-password`:

- Reads `auth_identity` by reset token.
- Updates `auth_identity.password_hash`, clears reset + refresh token fields.

## 9) If `auth_identity` Does Not Exist

- Startup attempts to create it automatically (`checkfirst=True`).
- Legacy `user` table is not altered.
- If central DB is unreachable or creation fails, auth endpoints may fail at runtime.

## 10) Frontend Integration Notes

Headers:

- `Content-Type: application/json`
- `X-API-Key` if API key middleware is enabled

Recommended client behavior:

- Store `access_token` + `refresh_token`.
- On 401 from protected call, attempt one `/refresh` and retry once.
- On logout, call `/logout` then clear local tokens.

## 11) Error Expectations

Typical responses:

- `401 Invalid credentials` (login failure)
- `401 Invalid refresh token` (refresh/logout)
- `400 Invalid reset token` (reset-password)
- `400 Reset token expired` (reset-password)
- `500` when bcrypt/pyseto dependencies are missing

## 12) Current Limitations

- Legacy login checks `user.password` only (not `mpin`).
- Forgot-password delivery is mock console output only.
- Single stored refresh token per user (new login rotates old refresh token).

## 13) Extension Ideas (Future)

- Add real SMS/email provider for reset delivery.
- Add token revocation history table if multi-device refresh is needed.
- Add optional support for `mpin` during migration if required by business rules.

## 14) SQL Gateway Route Protection

The SQL gateway route is explicitly protected by access token validation:

- `POST /api/query/gateway`
- Required header: `Authorization: Bearer <access_token>`
- Validation checks: token signature, expiry, and `typ=access`

Notes:

- This protection is route-specific in v1 (not a global auth rollout).
- API key middleware behavior remains unchanged and still depends on `API_KEY_ENABLED`.

## 15) Internal SQLGW Admin Endpoints

Internal policy/schema routes under `/internal/sqlgw/*` also require access tokens.

RBAC checks:

- Admin endpoints: `is_admin=true` OR role `sqlgw_admin`
- Approver actions (`approve` / `activate` / `archive`): admin OR role `sqlgw_approver`
