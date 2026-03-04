# Dual Routing Mode (Legacy + Standard FastAPI)

## Current state reference

For the live, code-synced route structure (registration order, explicit route inventory,
dynamic fallback behavior, and shadowing notes), see:

- `ROUTING_CURRENT_MAP.md`

## Why this change
The app now supports two routing systems in parallel:

1. Legacy dynamic routing for existing clients (unchanged behavior)
2. Standard FastAPI `APIRouter` routing for all new development

This allows gradual migration with no breaking changes.

## Routing order and precedence
In `main.py`, router inclusion order is now:

1. `app.include_router(api_router)` (central explicit router registry)
2. `app.include_router(dynamic_router)` (legacy fallback)

Because explicit routers are included first, if the same path exists in both systems, the explicit FastAPI route takes precedence.

## Legacy system (kept as-is)
Legacy routes still work exactly the same:

- `/{controller}/{function}`
- `/{controller}/{function}/{item_id}`

No changes were made to:

- `core/router.py` dispatch behavior
- `core/loader.py` argument mapping and execution
- response normalization behavior
- API key middleware behavior

## New standard routing system
New route modules should live under `controllers/` and expose:

- `router = APIRouter(...)`
- path operations via `@router.get`, `@router.post`, etc.

These routers are now included through a central registry:

- `api/v1/router.py`

## Central registry behavior
`api/v1/router.py` includes explicit routers in a fixed order:

1. `controllers.api.auth` (root auth paths)
2. `controllers.api.example`, `controllers.api.geosearch`, `controllers.api.llm`, `controllers.api.query_gateway`
3. `controllers.internal.sqlgw_admin` (RBAC-protected internal policy/schema APIs)
4. `controllers.orders`

This makes route registration deterministic and avoids implicit startup scanning.

## Example new-style controller
`controllers/orders.py` provides:

- `GET /orders/list`
- `GET /orders/get/{id}`
- `POST /orders/create`

These routes appear in OpenAPI docs at `/docs`.

## Converted standard routes for legacy controllers
Legacy dynamic routes are still available unchanged. In addition, standardized
explicit routes now exist under `/api/...`:

- Example controller (`controllers/api/example.py`)
  - `GET /api/example/hello`
  - `GET /api/example/echo?message=...`
  - `POST /api/example/calculate`
  - `GET /api/example/users`
  - `GET /api/example/user/{id}`
  - `POST /api/example/create_user`
  - `GET /api/example/random_data`
  - `POST /api/example/async_task`
  - `GET /api/example/status`

- Geosearch controller (`controllers/api/geosearch.py`)
  - `GET /api/geosearch/search`
  - `GET /api/geosearch/health`

- LLM controller (`controllers/api/llm.py`)
  - `GET /api/llm/health`
  - `GET /api/llm/models`
  - `POST /api/llm/chat`
  - `POST /api/llm/complete`
  - `POST /api/llm/conversation`

- Query gateway (`controllers/api/query_gateway.py`)
  - `POST /api/query/gateway`
  - Requires `Authorization: Bearer <access_token>`
  - Uses structured JSON DSL (`select|insert|update|delete`) with allowlist enforcement

- Internal SQL Gateway policy manager (`controllers/internal/sqlgw_admin.py`)
  - `GET /internal/sqlgw/schema/databases`
  - `GET /internal/sqlgw/schema/tables`
  - `GET /internal/sqlgw/schema/columns`
  - `GET /internal/sqlgw/policies`
  - `GET /internal/sqlgw/policies/{policy_id}`
  - `POST /internal/sqlgw/policies`
  - `POST /internal/sqlgw/policies/{policy_id}/approve`
  - `POST /internal/sqlgw/policies/{policy_id}/activate`
  - `POST /internal/sqlgw/policies/{policy_id}/archive`

## Developer guidance
- Keep legacy endpoints in their current function-based modules for backward compatibility.
- Implement all new endpoints using `APIRouter` modules in `controllers/`.
- For migrated legacy features, prefer explicit routes under `/api/<controller>/...`.
- Over time, migrate legacy endpoints to explicit routers and deprecate the dynamic fallback.

## Quick verification checklist
Use a valid API key header if API key auth is enabled:

- `X-API-Key: <key>`

Verify:

1. Legacy endpoint without ID still works: `GET /example/hello`
2. Legacy endpoint with ID still works: `GET /example/user/1`
3. New explicit endpoints work:
   - `GET /orders/list`
   - `GET /orders/get/123`
   - `POST /orders/create` with `{"customer":"Alice","amount":99.5}`
4. Converted legacy-standard endpoints work:
   - `GET /api/example/hello`
   - `GET /api/geosearch/health`
5. `/docs` includes the explicit endpoints.
