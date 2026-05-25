# NL2SQL Backend Integration

## Summary

`server_1` exposes an authenticated NL2SQL wrapper under `/api/nl2sql/v1`.
It validates app-facing payloads, enforces `ai-chat:read`, forwards requests to
the standalone NL2SQL service, validates JSON responses, and streams
`/ask/stream` without buffering the whole response.

## Wrapper Routes

| Method | Path | Upstream |
|---|---|---|
| `POST` | `/api/nl2sql/v1/ask/stream` | `/ask/stream` |
| `POST` | `/api/nl2sql/v1/ask` | `/ask` |
| `POST` | `/api/nl2sql/v1/generate-sql` | `/generate-sql` |
| `POST` | `/api/nl2sql/v1/teach` | `/teach` |
| `POST` | `/api/nl2sql/v1/teach/confirm` | `/teach/confirm` |
| `GET` | `/api/nl2sql/v1/instructions` | `/instructions` |
| `GET` | `/api/nl2sql/v1/failures` | `/failures` |
| `GET` | `/api/nl2sql/v1/telemetry/trace/{request_id}` | `/telemetry/trace/{request_id}` |
| `POST` | `/api/nl2sql/v1/ingest/groups` | `/ingest/groups` |
| `POST` | `/api/nl2sql/v1/ingest/knowledge` | `/ingest/knowledge` |
| `POST` | `/api/nl2sql/v1/ingest/patterns` | `/ingest/patterns` |
| `POST` | `/api/nl2sql/v1/ingest/instructions` | `/ingest/instructions` |

Routes are registered from `app/api/v1/router.py` via:

```python
api_router.include_router(nl2sql_router)
```

## Access Control

The wrapper remains gated by `require_nl2sql_access`.

- action: `ai-chat:read`
- resource type: `ai-chat`
- resource id: `*`

Super callers are allowed directly. Other callers are checked through PRISM PDP.

## Request ID Handling

Resolution order:

1. JSON `request_id`
2. `X-Request-ID` header
3. generated UUID

The resolved request id is forwarded as JSON where supported, forwarded as
`X-Request-ID`, returned in the wrapper response header, and included in error
envelopes under `data.request_id`.

## Streaming Ask

`POST /ask/stream` returns `application/x-ndjson`. The wrapper uses
`httpx.AsyncClient.stream()` and yields upstream bytes directly, so stage events
arrive in the browser while the standalone service is still working.

Example stream lines:

```json
{"event":"trace","request_id":"req-1","seq":2,"stage":"schema_retrieval","status":"completed","message":"Retrieved 3 table(s)."}
{"event":"final","response":{"status":"ok","answer":"Done.","sql":"SELECT ..."}}
```

The wrapper preserves `X-Request-ID` on the stream response and logs stream
request, HTTP status, success, timeout, and unavailable cases.

## Trace Lookup

`GET /api/nl2sql/v1/telemetry/trace/{request_id}?limit=500` returns the standard
success envelope:

```json
{
  "success": true,
  "data": {
    "request_id": "req-1",
    "results": [],
    "total": 0
  }
}
```

Trace event shape:

```json
{
  "request_id": "req-1",
  "seq": 1,
  "layer": "nl2sql-service",
  "stage": "request_received",
  "status": "started",
  "message": "Received ask request.",
  "duration_ms": null,
  "warning_codes": [],
  "error_source": null,
  "details": {},
  "created_at": "2026-05-25T00:00:00Z"
}
```

## Timeouts

Timeouts should remain ordered from inner to outer:

```text
Standalone /generate-sql timeout: 90s default
Standalone /ask timeout:         105s default
server_1 wrapper timeout:        configured above standalone
Frontend py-proxy timeout:       300000ms for NL2SQL
External reverse proxy:          above frontend timeout
```

If `server_1` is configured with `NL2SQL_TIMEOUT_SECONDS=280`, the frontend
`PY_PROXY_NL2SQL_TIMEOUT_MS` must stay above that value.

## Error Handling

Known wrapper errors:

| Condition | HTTP | Error code |
|---|---:|---|
| invalid JSON body | `400` | `BadRequest` |
| request validation failure | `422` | `ValidationError` |
| missing `NL2SQL_SERVICE_BASE_URL` | `503` | `NL2SQL_NOT_CONFIGURED` |
| upstream timeout | `502` | `NL2SQL_UPSTREAM_TIMEOUT` |
| upstream connectivity failure | `502` | `NL2SQL_UPSTREAM_UNAVAILABLE` |
| upstream non-200 response | upstream status | `NL2SQL_UPSTREAM_ERROR` |
| upstream invalid JSON | `502` | `NL2SQL_INVALID_RESPONSE` |
| upstream schema mismatch | `502` | `NL2SQL_INVALID_RESPONSE` |

## Finding A Stuck Request

Use the request id from frontend diagnostics or logs:

```bash
curl 'http://server-1/api/nl2sql/v1/telemetry/trace/REQ_ID?limit=500'
```

Sort by `seq` and inspect the last stage. A `started` event without a later
`completed`, `warning`, `failed`, or `complete` event identifies the likely
stall point.

## Verification

Focused wrapper check:

```bash
DEBUG=true python -m pytest tests/test_nl2sql_routes.py
```
