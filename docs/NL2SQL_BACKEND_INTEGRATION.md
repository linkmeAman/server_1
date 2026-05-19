# NL2SQL Backend Integration

## Summary

`server_1` exposes an authenticated NL2SQL wrapper under `/api/nl2sql/v1`.
The wrapper validates app-facing requests, enforces PRISM access control,
forwards requests to the standalone NL2SQL service, validates upstream responses,
and returns the standard backend response envelope.

The backend does not generate SQL directly. It delegates generation and answer
work to the configured `NL2SQL_SERVICE_BASE_URL`.

## Routes

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/nl2sql/v1/ask` | Generate SQL, execute the query through NL2SQL, and return a natural-language answer with metadata. |
| `POST` | `/api/nl2sql/v1/generate-sql` | Generate SQL preview and metadata without returning a final natural-language answer. |

Routes are registered from `app/api/v1/router.py` via:

```python
api_router.include_router(nl2sql_router)
```

## Module Layout

```text
app/modules/nl2sql/
  __init__.py
  dependencies.py
  router.py
  schemas/
    __init__.py
    models.py
  services/
    __init__.py
    client.py
```

## Request Flow

```text
Frontend / Next py-proxy
  -> server_1 /api/nl2sql/v1/{ask|generate-sql}
  -> require_nl2sql_access
  -> Nl2SqlRequest validation
  -> Nl2SqlClient
  -> NL2SQL_SERVICE_BASE_URL/{ask|generate-sql}
  -> upstream response validation
  -> backend success/error envelope
```

## Authentication And Authorization

The route depends on `require_nl2sql_access`.

- Authentication is resolved through `require_any_caller`.
- Supreme callers are allowed immediately.
- Non-supreme callers are checked through PRISM PDP.
- Required action: `ai-chat:read`
- Resource type: `ai-chat`
- Resource id: `*`

Denied callers receive `403` with `NL2SQL access denied by PRISM policy`.

## Request Contract

Both routes accept the same JSON body:

```json
{
  "query": "show unpaid invoices by counselor",
  "top_k": 5,
  "request_id": "optional-request-id"
}
```

Validation rules:

- `query` is required, strict string, trimmed, and must not be blank.
- `top_k` is optional, strict integer, and must be `>= 0`.
- `request_id` is optional, strict string, trimmed, and empty strings normalize to `null`.
- Unknown fields are ignored.

Request id resolution order:

1. `request_id` in the JSON body
2. `X-Request-ID` request header
3. generated UUID

`X-Request-ID` is returned on backend responses and forwarded to the upstream
NL2SQL service.

## Upstream Request Mapping

`Nl2SqlClient` maps app-facing routes to standalone NL2SQL service routes:

| server_1 route | Upstream route |
|---|---|
| `/api/nl2sql/v1/ask` | `/ask` |
| `/api/nl2sql/v1/generate-sql` | `/generate-sql` |

Upstream payload:

```json
{
  "query": "show unpaid invoices by counselor",
  "top_k": 5,
  "request_id": "resolved-request-id"
}
```

`top_k` fallback behavior:

- `top_k = null` uses `NL2SQL_DEFAULT_TOP_K`.
- `top_k = 0` also uses `NL2SQL_DEFAULT_TOP_K`.
- positive values are forwarded as provided.

## Environment Variables

```env
NL2SQL_SERVICE_BASE_URL=http://127.0.0.1:8080
NL2SQL_TIMEOUT_SECONDS=30
NL2SQL_DEFAULT_TOP_K=5
```

Timeouts must be ordered from inner to outer layers. If the standalone NL2SQL
service can run for up to `NL2SQL_TIMEOUT_SECONDS`, then any frontend proxy,
external gateway, or reverse proxy in front of server_1 must allow a longer
timeout. Otherwise callers will see an outer `504` before server_1 can return a
controlled NL2SQL error response.

Example budget:

```text
Standalone NL2SQL work:       <= 120s
server_1 NL2SQL_TIMEOUT:      120s
Frontend py-proxy timeout:    125s+
External reverse proxy:       130s+
Client/UI patience:           130s+
```

## Response Contract

Successful server_1 responses use the standard backend envelope:

```json
{
  "success": true,
  "data": {
    "status": "ok"
  },
  "message": "NL2SQL ask completed"
}
```

`/ask` success data may include:

- `status`
- `answer`
- `sql`
- `row_count`
- `columns`
- `tables_used`
- `matched_groups`
- `attempt_count`
- `warnings`
- `react_trace`

`/generate-sql` success data may include:

- `status`
- `sql`
- `tables_used`
- `matched_groups`
- `attempt_count`
- `warnings`
- `react_trace`

Controlled upstream statuses are returned inside `data.status`:

- `ok`
- `clarification_needed`
- `rejected`

## Error Handling

Known backend errors:

| Condition | HTTP status | Error code |
|---|---:|---|
| invalid JSON body | `400` | `BadRequest` |
| non-object JSON body | `400` | `BadRequest` |
| request validation failure | `422` | `ValidationError` |
| missing `NL2SQL_SERVICE_BASE_URL` | `503` | `NL2SQL_NOT_CONFIGURED` |
| upstream timeout | `502` | `NL2SQL_UPSTREAM_TIMEOUT` |
| upstream connectivity failure | `502` | `NL2SQL_UPSTREAM_UNAVAILABLE` |
| upstream non-200 response | upstream status | `NL2SQL_UPSTREAM_ERROR` |
| upstream invalid JSON | `502` | `NL2SQL_INVALID_RESPONSE` |
| upstream unexpected schema | `502` | `NL2SQL_INVALID_RESPONSE` |

Backend error envelope shape:

```json
{
  "success": false,
  "data": {
    "request_id": "resolved-request-id"
  },
  "message": "NL2SQL upstream timed out while calling /ask",
  "error": "NL2SQL_UPSTREAM_TIMEOUT"
}
```

## Logging And Observability

`Nl2SqlClient` logs one line per completed upstream request:

```text
NL2SQL request_id=<id> user_id=<user> route=<server-route> upstream=<upstream-route> duration_ms=<ms> status=<status> warnings=<codes>
```

Failure logs include:

```text
NL2SQL request_id=<id> user_id=<user> route=<server-route> upstream=<route-name> duration_ms=<ms> status=error upstream_status=<status> warnings=<codes>
```

Recommended trace workflow:

1. Capture `X-Request-ID` from the browser or frontend proxy log.
2. Search server_1 logs for `request_id=<id>`.
3. Search standalone NL2SQL telemetry for the same `request_id`.
4. Compare durations across frontend proxy, server_1, and standalone NL2SQL.

## Local Smoke Tests

Direct server_1 call:

```bash
curl -X POST http://127.0.0.1:8010/api/nl2sql/v1/ask \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "X-Request-ID: 9ad31f85-23e3-41d1-80bd-db5ad92ccce5" \
  -d '{
    "query": "newest payment",
    "top_k": 5,
    "request_id": "9ad31f85-23e3-41d1-80bd-db5ad92ccce5"
  }'
```

SQL preview:

```bash
curl -X POST http://127.0.0.1:8010/api/nl2sql/v1/generate-sql \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -d '{
    "query": "show unpaid invoices by counselor",
    "top_k": 5
  }'
```

## Operational Notes

- Keep `NL2SQL_SERVICE_BASE_URL` pointed at the standalone NL2SQL service root,
  not at server_1.
- Keep request ids consistent in both headers and JSON bodies when requests pass
  through external gateways that may strip custom headers.
- Prefer returning controlled `502 NL2SQL_UPSTREAM_TIMEOUT` responses from
  server_1 over allowing an outer proxy to emit a generic `504`.
- For long-running NL2SQL prompts, consider moving `/ask` to streaming or an
  async job model if response times routinely exceed gateway limits.
