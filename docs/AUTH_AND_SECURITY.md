# Auth And Security

## Auth Surfaces

### Legacy auth

Legacy login-style routes are implemented in:

- `app/modules/auth/legacy_router.py`

### Auth v2

Primary auth-v2 routes and handlers are implemented in:

- `app/modules/auth/router.py`
- `app/modules/auth/handlers/*`
- `app/modules/auth/services/*`

## Token Model

- PASETO is used for token generation and validation
- `app/core/security.py` handles legacy/single-secret token helpers
- `app/modules/auth/services/keyring.py` handles auth-v2 signing key rotation

## Required Production Security Settings

These must be set in `.env` when `DEBUG=False`:

- `SECRET_KEY`
- `PASETO_SECRET_KEY`
- `ALLOWED_HOSTS`
- `CORS_ORIGINS` unless `CORS_MANAGED_BY_PROXY=True`
- `AUTH_V2_SIGNING_KEYS_JSON`

If `API_KEY_ENABLED=True`, then `API_KEYS` must contain at least one key.

## PRISM

PRISM code lives under:

- `app/modules/prism/*`
- `app/core/prism_cache.py`
- `app/core/prism_guard.py`
- `app/core/prism_pdp.py`
