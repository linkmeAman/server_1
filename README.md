# Dynamic Multi-Project API

FastAPI service with dual routing support:

- Explicit standard routes via `APIRouter` (primary for all new development)
- Legacy dynamic routes via `/{controller}/{function}/{item_id?}` (backward compatibility)

This README is the current development skeleton for this repository.

## 1) Project Goals

- Keep existing legacy clients fully compatible.
- Build all new endpoints using explicit FastAPI routers.
- Allow gradual migration from legacy function dispatch to standard router modules.
- Keep response format consistent across both systems.

## 2) Current Architecture

Request flow:

1. `main.py` creates the FastAPI app, exception handlers, and middleware.
2. `include_routers(app, "controllers")` auto-discovers explicit routers first.
3. `app.include_router(dynamic_router)` adds legacy dynamic fallback second.
4. Middleware applies API key auth, logging, CORS, and optional rate limiting.

Routing precedence:

- Explicit APIRouter routes are evaluated first.
- Legacy dynamic routes are fallback.
- If paths overlap, explicit routes win.

## 3) Repository Structure

```text
.
├── main.py                       # App entrypoint and router registration order
├── core/
│   ├── settings.py               # Pydantic settings + .env loading
│   ├── middleware.py             # API key, CORS, logging, rate limit
│   ├── router.py                 # Legacy dynamic dispatcher
│   ├── loader.py                 # Legacy controller/function resolver
│   ├── response.py               # Standard response models/helpers
│   ├── exceptions.py             # Custom exception types
│   └── database.py               # DB init + SQLAlchemy models (Venue, City)
├── loader/
│   └── autodiscover.py           # Explicit APIRouter auto-discovery
├── controllers/
│   ├── example.py                # Legacy function-based controller
│   ├── geosearch.py              # Legacy function-based controller
│   ├── llm.py                    # Legacy function-based controller
│   ├── orders.py                 # Explicit APIRouter example
│   └── api/                      # Explicit routes mapped from legacy controllers
│       ├── _responses.py         # Explicit route response normalization helper
│       ├── example.py
│       ├── geosearch.py
│       └── llm.py
├── ROUTING_DUAL_MODE.md          # Detailed dual-routing notes
├── .env.example                  # Environment template
└── requirements.txt
```

## 4) Runtime Requirements

- Python 3.10+
- MySQL (optional, only needed for DB-backed controller behavior)
- Dependencies in `requirements.txt`

Install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Or using the existing repo venv:

```bash
./pyenv/bin/pip install -r requirements.txt
```

## 5) Configuration

Copy template and adjust:

```bash
cp .env.example .env
```

Key environment variables:

- `HOST`, `PORT`, `RELOAD`, `DEBUG`
- `API_KEY_ENABLED`, `API_KEYS`
- `DATABASE_URL` or `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_PORT`
- `LOG_LEVEL`, `LOG_FILE`
- `ALLOW_PRIVATE_METHODS`, `MAX_CONTROLLER_NAME_LENGTH`, `MAX_FUNCTION_NAME_LENGTH`

Notes:

- `.env.example` ships with `API_KEY_ENABLED=False` for easier local startup.
- `core/settings.py` has secure/runtime defaults, but `.env` should be treated as source of truth per environment.

## 6) Run the Service

Development:

```bash
./pyenv/bin/python main.py
```

or:

```bash
./pyenv/bin/uvicorn main:app --host 127.0.0.1 --port 8010 --reload
```

OpenAPI docs:

- `GET /docs` (when `DEBUG=True`)
- `GET /openapi.json`

Health check:

- Direct app route: `GET /health`
- If deployed behind Nginx `/py` prefix: `GET /py/health`

## 7) Dual Routing Model

### Explicit standard routes (preferred)

- Implemented as modules exporting `router = APIRouter(...)`.
- Auto-discovered recursively under `controllers` package.
- Best location for new work: `controllers/api/<domain>.py`.

Current explicit route examples:

- `/orders/list`
- `/orders/get/{id}`
- `/orders/create`
- `/api/example/*`
- `/api/geosearch/*`
- `/api/llm/*`

### Legacy dynamic routes (kept for compatibility)

- `/{controller}/{function}`
- `/{controller}/{function}/{item_id}`

Behavior preserved:

- Dynamic module loading (`controllers.<controller>`)
- Function lookup and invocation
- Query/body/path argument mapping
- Type conversion for primitives
- Standardized response wrapping

## 8) Nginx `/py` Prefix Usage

If Nginx exposes the service under `/py/...`, your public URLs are prefixed while app routes remain unchanged.

Example:

- App route: `/api/example/hello`
- Public route via Nginx: `/py/api/example/hello`

Recommended Nginx location style:

```nginx
location /py/ {
    proxy_pass http://127.0.0.1:8010/;
}
```

## 9) Authentication and Middleware

Configured in `core/middleware.py`.

- API key auth:
  - Header: `X-API-Key: <key>`
  - Or `Authorization: Bearer <key>`
  - Public by default: `/docs`, `/redoc`, `/openapi.json`
- Request logging:
  - Adds `X-Process-Time` response header
- CORS:
  - Controlled by `CORS_ORIGINS`
- Rate limiting:
  - Optional in-memory limiter via `RATE_LIMIT_ENABLED`

## 10) Standard Response Contract

Primary schema from `core/response.py`:

```json
{
  "success": true,
  "data": {},
  "message": "Success",
  "error": null,
  "timestamp": "2026-02-12T10:00:00"
}
```

Rules:

- If a handler returns a dict with `success`, it is used as-is.
- Otherwise, result is wrapped using `success_response(...)`.
- Explicit wrappers in `controllers/api/_responses.py` mirror this behavior.

## 11) Development Workflow (From Now On)

### Rule 1: New endpoints must be explicit routers

- Create module under `controllers/api/` (recommended).
- Export `router = APIRouter(...)`.
- Add `@router.get/post/put/delete/...` handlers.
- Use typed request models (`pydantic.BaseModel`) for body payloads.

### Rule 2: Do not add new legacy dynamic endpoints

- Keep legacy controllers only for backward compatibility.
- If legacy functionality is needed in new API, wrap legacy logic in explicit routes.

### Rule 3: Keep route naming stable

- Prefer `/api/<domain>/<action>` for migrated domains.
- Keep existing legacy URLs untouched.

## 12) How To Add a New Domain Router

Create `controllers/api/inventory.py`:

```python
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

class CreateItemRequest(BaseModel):
    name: str
    quantity: int

@router.get("/list")
async def list_items():
    return {"success": True, "data": []}

@router.post("/create")
async def create_item(payload: CreateItemRequest):
    return {"success": True, "data": payload.model_dump()}
```

No manual registration needed. `loader/autodiscover.py` will include it at startup.

## 13) How To Wrap an Existing Legacy Controller

Pattern used in this repo:

1. Import legacy module in `controllers/api/<name>.py`.
2. Call legacy function from explicit endpoint.
3. Normalize output using `controllers/api/_responses.py`.

This keeps response shape and behavior aligned during migration.

## 14) Quick Validation Commands

Replace key with your configured key (or disable auth locally).

```bash
curl -H "X-API-Key: tr_test_key_2025_x9y8z7w6v5u4" http://127.0.0.1:8010/example/hello
curl -H "X-API-Key: tr_test_key_2025_x9y8z7w6v5u4" http://127.0.0.1:8010/api/example/hello
curl -H "X-API-Key: tr_test_key_2025_x9y8z7w6v5u4" http://127.0.0.1:8010/orders/list
curl -H "X-API-Key: tr_test_key_2025_x9y8z7w6v5u4" http://127.0.0.1:8010/docs
```

With Nginx prefix:

```bash
curl -H "X-API-Key: <key>" https://<host>/py/api/example/hello
```

## 15) Database Notes

- DB initialization runs at startup (`init_database()`).
- If DB is missing/unreachable, app still starts and DB-backed features may degrade.
- Existing SQLAlchemy models are read-only mappings in `core/database.py`:
  - `Venue`
  - `City`

## 16) Troubleshooting

- `401 API key required`: missing `X-API-Key` while auth enabled.
- `403 Invalid API key`: key not in configured list.
- `Controller '<name>' not found`: dynamic route controller file missing or name invalid.
- `Function '<name>' not found`: function not exported/callable in legacy controller.
- DB errors in geosearch health/search: verify `.env` DB settings and connectivity.

## 17) Migration Direction

- Keep all legacy endpoints alive until clients are migrated.
- Expose equivalent explicit endpoints under `/api/...`.
- Move business logic to service modules over time, then deprecate dynamic fallback.

## 18) Related Docs

- `ROUTING_DUAL_MODE.md` for routing migration details.
- `AUTHENTICATION.md` for auth endpoints, migration flow, and table usage.
- `PROJECT_ANALYSIS.md` for historical analysis notes.
