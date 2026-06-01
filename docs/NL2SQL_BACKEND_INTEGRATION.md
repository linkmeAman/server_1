# NL2SQL Backend Integration

Single backend-facing reference for the authenticated `server_1` NL2SQL wrapper and the standalone NL2SQL service it proxies.

## Summary

`server_1` exposes an authenticated NL2SQL wrapper under `/api/nl2sql/v1`.
The wrapper:

- enforces `ai-chat:read`
- resolves and forwards `request_id`
- validates request payloads before proxying
- validates upstream JSON responses before returning backend envelopes
- streams `/ask/stream` without buffering the full response
- publishes request-correlated NL2SQL lifecycle events through the shared notification broker

The standalone NL2SQL service lives separately on its own base URL and exposes a broader direct route surface for health, ingest, telemetry, governance, and operational debugging.

## Architecture

```text
Frontend / py-proxy
  -> server_1 /api/nl2sql/v1/*
  -> PRISM gate (ai-chat:read)
  -> wrapper validation + request-id resolution
  -> standalone NL2SQL service
  -> validated upstream response
  -> backend success/error envelope
```

## Wrapper Routes

Current app-facing wrapper routes in `server_1`:

### Health, Metrics, and Debugging

| Method | Wrapper Path | Upstream Path |
|---|---|---|
| `GET` | `/api/nl2sql/v1/health` | `/health` |
| `GET` | `/api/nl2sql/v1/health/config` | `/health/config` |
| `GET` | `/api/nl2sql/v1/health/runtime` | `/health/runtime` |
| `GET` | `/api/nl2sql/v1/health/llm` | `/health/llm` |
| `GET` | `/api/nl2sql/v1/health/vector` | `/health/vector` |
| `GET` | `/api/nl2sql/v1/metrics/llm` | `/metrics/llm` |
| `GET` | `/api/nl2sql/v1/metrics/teach` | `/metrics/teach` |
| `GET` | `/api/nl2sql/v1/telemetry/recent` | `/telemetry/recent` |
| `GET` | `/api/nl2sql/v1/telemetry/summary` | `/telemetry/summary` |
| `GET` | `/api/nl2sql/v1/telemetry/trace/{request_id}` | `/telemetry/trace/{request_id}` |
| `GET` | `/api/nl2sql/v1/failures` | `/failures` |
| `GET` | `/api/nl2sql/v1/cache/stats` | `/cache/stats` |
| `POST` | `/api/nl2sql/v1/cache/clear` | `/cache/clear` |
| `GET` | `/api/nl2sql/v1/governance/rules` | `/governance/rules` |
| `POST` | `/api/nl2sql/v1/governance/validate` | `/governance/validate` |
| `POST` | `/api/nl2sql/v1/benchmark/cases` | `/benchmark/cases` |
| `GET` | `/api/nl2sql/v1/benchmark/cases` | `/benchmark/cases` |

### Retrieval, Ingest, and Generation

| Method | Wrapper Path | Upstream Path |
|---|---|---|
| `GET` | `/api/nl2sql/v1/ingest/groups/status` | `/ingest/groups/status` |
| `POST` | `/api/nl2sql/v1/query` | `/query` |
| `POST` | `/api/nl2sql/v1/query/groups` | `/query/groups` |
| `POST` | `/api/nl2sql/v1/ask` | `/ask` |
| `POST` | `/api/nl2sql/v1/ask/stream` | `/ask/stream` |
| `POST` | `/api/nl2sql/v1/generate-sql` | `/generate-sql` |
| `POST` | `/api/nl2sql/v1/ingest/groups` | `/ingest/groups` |
| `POST` | `/api/nl2sql/v1/ingest/knowledge` | `/ingest/knowledge` |
| `POST` | `/api/nl2sql/v1/ingest/patterns` | `/ingest/patterns` |
| `POST` | `/api/nl2sql/v1/ingest/instructions` | `/ingest/instructions` |

### Learning and Administration

| Method | Wrapper Path | Upstream Path |
|---|---|---|
| `POST` | `/api/nl2sql/v1/teach` | `/teach` |
| `POST` | `/api/nl2sql/v1/teach/confirm` | `/teach/confirm` |
| `GET` | `/api/nl2sql/v1/teach/pending` | `/teach/pending` |
| `POST` | `/api/nl2sql/v1/teach/pending/cleanup` | `/teach/pending/cleanup` |
| `GET` | `/api/nl2sql/v1/instructions` | `/instructions` |
| `DELETE` | `/api/nl2sql/v1/instructions/{instruction_id}` | `/instructions/{instruction_id}` |
| `POST` | `/api/nl2sql/v1/patterns/feedback` | `/patterns/feedback` |

These routes are registered from `app/api/v1/router.py` via `nl2sql_router`.

## Standalone Direct Routes

The standalone NL2SQL service still exposes a few direct-only routes that are not wrapped by `server_1`.
These are primarily for local maintenance or operator-only HTML help.

### Direct-only Upstream Routes

- `GET /help`
- `GET /help/{module}`
- `GET /help/{module}/{route_slug}`
- `POST /ingest`

Canonical upstream route details live in the standalone docs:

- `/var/www/py-workspace/nl2sql/README.md`
- `/var/www/py-workspace/nl2sql/ROUTES.md`

## Access Control

The wrapper remains gated by `require_nl2sql_access`.

- action: `ai-chat:read`
- resource type: `ai-chat`
- resource id: `*`

Super callers are allowed directly. Other callers are checked through PRISM PDP.

## Request ID Rules

Resolution order:

1. JSON body `request_id`
2. `X-Request-ID` header
3. generated UUID

The resolved request id is:

- forwarded to the standalone service in JSON where supported
- forwarded in `X-Request-ID`
- returned by the wrapper in `X-Request-ID`
- attached to wrapper error envelopes under `data.request_id`
- reused for notification publishing and trace lookups

## Wrapper Response Shapes

The wrapper returns normal backend success/error envelopes. The upstream NL2SQL payload is placed under `data`.

### Ask

Successful `data` can include:

- `status`
- `answer`
- `sql`
- `row_count`
- `columns`
- `tables_used`
- `matched_groups`
- `attempt_count`
- `warnings`
- `cache_hit`
- `cache_source`
- `react_trace`
- `stage_latencies_ms`
- `review_prompt`

### Generate SQL

Successful `data` can include:

- `status`
- `sql`
- `tables_used`
- `matched_groups`
- `attempt_count`
- `warnings`
- `cache_hit`
- `cache_source`
- `react_trace`
- `stage_latencies_ms`
- `review_prompt`

### Teach

`data` can include:

- `learning_status`
- `message`
- `instruction_id`
- `similar_instructions`
- `requires_confirmation`
- `confirmation_token`

### Failures

Wrapper response shape:

```json
{
  "success": true,
  "data": {
    "results": [],
    "total": 0
  }
}
```

### Trace Lookup

Wrapper response shape:

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

## Streaming Ask

`POST /api/nl2sql/v1/ask/stream` returns `application/x-ndjson`.
The wrapper uses `httpx.AsyncClient.stream()` and yields upstream bytes directly.
That keeps stage events visible to the browser while the standalone service is still running.

Typical stream events from the standalone service include:

- `started`
- `trace`
- `sql_generation_started`
- `sql_generation_running`
- `sql_generation_finished`
- `sql_generation_rejected`
- `row_cap_applied`
- `execution_started`
- `execution_finished`
- `execution_failed`
- `answer_generation_started`
- `answer_generation_running`
- `answer_generation_finished`
- `answer_generation_failed`
- `timeout`
- `final`

Example stream lines:

```json
{"event":"trace","request_id":"req-1","seq":2,"stage":"schema_retrieval","status":"completed","message":"Retrieved 3 table(s)."}
{"event":"final","response":{"status":"ok","answer":"Done.","sql":"SELECT ..."}}
```

The wrapper preserves `X-Request-ID` on the stream response and publishes notification events derived from `trace` and `final` lines.

## Notification Publishing

The notification module is universal across MarkX. NL2SQL is one producer, but the same broker and SSE stream are intended for reports, communications, workforce, finance, auth, PRISM, system jobs, and future agent workflows.

The notification module exposes:

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/notifications/v1/stream` | Authenticated SSE stream |
| `GET` | `/api/notifications/v1/recent` | Process-retained recent events |
| `POST` | `/api/notifications/v1/debug` | Authenticated debug publish endpoint |

Any backend module can publish with `app.modules.notifications.services.publisher.publish_notification`.
NL2SQL publishes lifecycle events with the same `request_id` used by the wrapper request and standalone trace telemetry.

Common event types:

- `SCHEMA_RETRIEVAL_STARTED`
- `SCHEMA_RETRIEVAL_SUCCESS`
- `SQL_GENERATION_STARTED`
- `SQL_GENERATION_FAILED`
- `QUERY_TIMEOUT`
- `QUERY_COMPLETED`

Event schema:

```json
{
  "event_id": "uuid",
  "request_id": "req-1",
  "event_type": "QUERY_COMPLETED",
  "severity": "success",
  "source": "nl2sql",
  "timestamp": "2026-05-26T00:00:00+00:00",
  "message": "Query completed",
  "metadata": {}
}
```

## Caching Semantics

The standalone service now uses a multi-layer cache for `/ask` and `/generate-sql`:

1. memory exact cache
2. memory semantic cache
3. PostgreSQL exact cache for current epoch
4. PostgreSQL semantic cache for current epoch
5. full pipeline

Returned cache metadata:

- `cache_hit`
- `cache_source`

`cache_source` values:

- `none`
- `memory_exact`
- `memory_semantic`
- `db_exact`
- `db_semantic`

Teach and ingest mutations bump the persistent cache epoch and clear in-memory caches.

## Teach Semantics

Important current behavior in the standalone service:

- pending teach confirmations are stored in PostgreSQL
- pending confirmation tokens survive service restarts
- confirmation tokens still expire after 30 minutes
- `GET /teach/pending` and `POST /teach/pending/cleanup` exist for operational review and cleanup
- `GET /metrics/teach` exposes backlog and expired-token alerts

The `server_1` wrapper currently exposes `/teach` and `/teach/confirm`, but not the direct pending-confirmation admin routes.

## Health and Readiness

The standalone service now splits readiness into multiple surfaces:

- `/health`
  - compact overall status
  - includes PostgreSQL status, provider-config status, MySQL target status, schema-asset status, and teach-confirmation alert status
- `/health/config`
  - resolved provider readiness report
- `/health/runtime`
  - MySQL execution readiness and schema/docs asset readiness
- `/health/llm`
  - role-specific LLM probe
- `/health/vector`
  - vector/PostgreSQL connectivity plus embedding config

### Startup Enforcement

The standalone service supports:

```env
STARTUP_ENFORCEMENT_MODE=warn|strict
```

- `warn`
  - logs provider/runtime readiness failures but still starts
- `strict`
  - fails startup when provider config, MySQL readiness, or schema/docs assets are not ready

Recommended production mode:

```env
STARTUP_ENFORCEMENT_MODE=strict
```

## 503 Configuration Fix

If `server_1` returns HTTP `503` with `NL2SQL_NOT_CONFIGURED`, the usual cause is a missing `NL2SQL_SERVICE_BASE_URL` in the `server_1` environment.

Required env block:

```env
NL2SQL_SERVICE_BASE_URL=http://localhost:8080
NL2SQL_TIMEOUT_SECONDS=120
NL2SQL_DEFAULT_TOP_K=5
```

Recovery steps:

1. Edit the live `server_1` `.env`
2. Add the env block above if it is missing
3. Restart `server_1`
4. Verify the standalone NL2SQL service directly

Recommended direct checks:

```bash
curl -s http://localhost:8080/health | python3 -m json.tool
curl -s http://localhost:8080/health/config | python3 -m json.tool
curl -s http://localhost:8080/health/runtime | python3 -m json.tool
```

In a deploy-ready environment, all three should return `status: ok`.

## Timeouts

Timeouts should remain ordered from inner to outer:

```text
Standalone SQL_GENERATION_TIMEOUT: 90s default
Standalone ASK_TIMEOUT:            105s default
server_1 NL2SQL_TIMEOUT_SECONDS:   above standalone ask timeout
Frontend py-proxy timeout:         above server_1 timeout
Outer reverse proxy timeout:       above frontend timeout
```

If `server_1` uses `NL2SQL_TIMEOUT_SECONDS=280`, the frontend NL2SQL proxy timeout must stay above that value.

## Error Handling

Wrapper errors:

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

## Verification

Wrapper checks:

```bash
DEBUG=true python -m pytest tests/test_nl2sql_routes.py
python -m compileall -q app/modules/notifications app/modules/nl2sql app/api/v1/router.py
```

Standalone deploy gate:

```bash
cd /var/www/py-workspace/nl2sql
make smoke-deploy
```

`make smoke-deploy` fails when `/health`, `/health/config`, or `/health/runtime` are not `ok`.
