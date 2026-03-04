# Current Routing Map

Last synced: February 26, 2026

This document reflects the routing behavior currently implemented in code.

## 1) Router Registration Order

Router inclusion is done in `main.py` in this order:

1. `app.include_router(api_router)` (central explicit router registry)
2. `app.include_router(dynamic_router)` (legacy fallback dispatcher)

Because explicit routers are included first, they are matched before the dynamic fallback when both could match.

## 2) Central Explicit Router Registry

Explicit routes are registered centrally in `api/v1/router.py` using direct imports.

Current included explicit routers:

- `controllers.api.auth`
- `controllers.api.example`
- `controllers.api.geosearch`
- `controllers.api.llm`
- `controllers.api.query_gateway`
- `controllers.internal.sqlgw_admin`
- `controllers.orders`

## 3) Current Registered Routes (Runtime Order)

Route matching follows registration order. Current table:

1. `GET /openapi.json`
2. `GET /docs`
3. `GET /docs/oauth2-redirect`
4. `GET /redoc`
5. `POST /login`
6. `POST /refresh`
7. `POST /logout`
8. `POST /forgot-password`
9. `POST /reset-password`
10. `GET /api/example/hello`
11. `GET /api/example/echo`
12. `POST /api/example/calculate`
13. `GET /api/example/users`
14. `GET /api/example/user/{id}`
15. `POST /api/example/create_user`
16. `GET /api/example/random_data`
17. `POST /api/example/async_task`
18. `GET /api/example/status`
19. `GET /api/geosearch/search`
20. `GET /api/geosearch/health`
21. `GET /api/llm/health`
22. `GET /api/llm/models`
23. `POST /api/llm/chat`
24. `POST /api/llm/complete`
25. `POST /api/llm/conversation`
26. `POST /api/query/gateway`
27. `GET /internal/sqlgw/schema/databases`
28. `GET /internal/sqlgw/schema/tables`
29. `GET /internal/sqlgw/schema/columns`
30. `GET /internal/sqlgw/policies`
31. `GET /internal/sqlgw/policies/{policy_id}`
32. `POST /internal/sqlgw/policies`
33. `POST /internal/sqlgw/policies/{policy_id}/approve`
34. `POST /internal/sqlgw/policies/{policy_id}/activate`
35. `POST /internal/sqlgw/policies/{policy_id}/archive`
36. `GET /orders/list`
37. `GET /orders/get/{id}`
38. `POST /orders/create`
39. `GET /`
40. `GET /health`
41. `GET /controllers`
42. `GET /controllers/{controller_name}/functions`
43. `GET|POST|PUT|PATCH|DELETE /{controller}/{function}`
44. `GET|POST|PUT|PATCH|DELETE /{controller}/{function}/{item_id}`

## 4) Explicit Routes by Source Module

### `controllers/api/auth.py`

- `POST /login`
- `POST /refresh`
- `POST /logout`
- `POST /forgot-password`
- `POST /reset-password`

### `controllers/api/example.py`

- `GET /api/example/hello`
- `GET /api/example/echo`
- `POST /api/example/calculate`
- `GET /api/example/users`
- `GET /api/example/user/{id}`
- `POST /api/example/create_user`
- `GET /api/example/random_data`
- `POST /api/example/async_task`
- `GET /api/example/status`

### `controllers/api/geosearch.py`

- `GET /api/geosearch/search`
- `GET /api/geosearch/health`

### `controllers/api/llm.py`

- `GET /api/llm/health`
- `GET /api/llm/models`
- `POST /api/llm/chat`
- `POST /api/llm/complete`
- `POST /api/llm/conversation`

### `controllers/api/query_gateway.py`

- `POST /api/query/gateway`

### `controllers/internal/sqlgw_admin.py`

- `GET /internal/sqlgw/schema/databases`
- `GET /internal/sqlgw/schema/tables`
- `GET /internal/sqlgw/schema/columns`
- `GET /internal/sqlgw/policies`
- `GET /internal/sqlgw/policies/{policy_id}`
- `POST /internal/sqlgw/policies`
- `POST /internal/sqlgw/policies/{policy_id}/approve`
- `POST /internal/sqlgw/policies/{policy_id}/activate`
- `POST /internal/sqlgw/policies/{policy_id}/archive`

### `controllers/orders.py`

- `GET /orders/list`
- `GET /orders/get/{id}`
- `POST /orders/create`

## 5) Legacy Dynamic Fallback

Dynamic route patterns:

- `/{controller}/{function}`
- `/{controller}/{function}/{item_id}`

Behavior:

1. Validate controller/function names against safe pattern and max length.
2. Import `controllers.<controller>`.
3. Resolve callable `<function>`.
4. Map arguments from path/query/body.
5. Execute sync or async function.
6. Normalize response format unless already normalized.

## 6) Dynamic Argument Mapping Rules

For dynamic dispatch:

1. If `{item_id}` exists, map to `id` parameter; if not present, map to `item_id`.
2. Merge query params for matching parameter names.
3. Merge body params for matching parameter names.
4. For path/query values, apply type conversion using type hints (`int`, `float`, `bool`).
5. If required parameters are still missing, return a 400 parameter validation error.

## 7) Current Collision/Shadowing Notes

Important runtime behavior:

1. `/controllers/example/functions` matches explicit `/controllers/{controller_name}/functions` before dynamic catch-all.
2. `/health` matches the explicit dynamic-router utility endpoint, not dynamic dispatch.
3. `/api/*`, `/orders/*`, and auth root endpoints are explicit and therefore matched before dynamic fallback.
4. Root `/` is registered before dynamic catch-alls.

## 8) Middleware Impact on Routing

Global middleware is applied before route handlers:

- Request logging middleware is always active.
- CORS middleware is skipped when `CORS_MANAGED_BY_PROXY=True`.
- API key middleware only applies when `API_KEY_ENABLED=True`.
- Rate limiting only applies when `RATE_LIMIT_ENABLED=True`.

Current `.env` value:

- `API_KEY_ENABLED=False`

## 9) Public URL Prefix Note (`/py`)

Routes in FastAPI are mounted at root (for example `/api/example/hello`).
If Nginx proxies under `/py`, public URLs become `/py/...`.
The `/py` prefix is deployment-level, not FastAPI router-level.

## 10) How To Re-Sync This Map

Run this command to print the current route order from the running codebase:

```bash
./pyenv/bin/python - <<'PY'
from main import app
for i, r in enumerate(app.router.routes):
    methods = getattr(r, "methods", None)
    if methods:
        m = ",".join(sorted(x for x in methods if x not in {"HEAD", "OPTIONS"}))
    else:
        m = ""
    print(f"{i:03d} | {m:20} | {r.path}")
PY
```

After running, update:

1. `Last synced` date at top.
2. Section `Current Registered Routes (Runtime Order)`.
3. `Collision/Shadowing Notes` if matching behavior changed.
