# NL2SQL API Integration Flow

Reference for the authenticated `server_1` wrapper and the standalone NL2SQL
service it proxies.

## Architecture

```text
Frontend / py-proxy
  -> server_1 /api/nl2sql/v1/*
  -> PRISM gate (ai-chat:read)
  -> wrapper validation + request-id resolution
  -> standalone NL2SQL service
  -> validated upstream response
  -> backend response envelope
```

## Wrapper Surface

`server_1` exposes:

- `POST /api/nl2sql/v1/ask`
- `POST /api/nl2sql/v1/generate-sql`
- `POST /api/nl2sql/v1/teach`
- `POST /api/nl2sql/v1/teach/confirm`
- `GET /api/nl2sql/v1/instructions`
- `POST /api/nl2sql/v1/ingest/groups`
- `POST /api/nl2sql/v1/ingest/knowledge`
- `POST /api/nl2sql/v1/ingest/patterns`
- `POST /api/nl2sql/v1/ingest/instructions`

## Request Flow

1. frontend sends request to `server_1`
2. wrapper enforces `ai-chat:read`
3. wrapper resolves request id
4. wrapper maps to the standalone route
5. wrapper validates upstream JSON into typed models
6. wrapper returns the normal backend envelope plus `X-Request-ID`

## Request ID Rules

Resolution order:

1. body `request_id`
2. `X-Request-ID` header
3. generated UUID

The same request id is forwarded to upstream and returned to the caller.

## Upstream Mapping

| Wrapper route | Upstream route |
|---|---|
| `/api/nl2sql/v1/ask` | `/ask` |
| `/api/nl2sql/v1/generate-sql` | `/generate-sql` |
| `/api/nl2sql/v1/teach` | `/teach` |
| `/api/nl2sql/v1/teach/confirm` | `/teach/confirm` |
| `/api/nl2sql/v1/instructions` | `/instructions` |
| `/api/nl2sql/v1/ingest/groups` | `/ingest/groups` |
| `/api/nl2sql/v1/ingest/knowledge` | `/ingest/knowledge` |
| `/api/nl2sql/v1/ingest/patterns` | `/ingest/patterns` |
| `/api/nl2sql/v1/ingest/instructions` | `/ingest/instructions` |

## Important Response Shapes

### Ask

Success envelope `data` can include:

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

### Generate SQL

Success envelope `data` can include:

- `status`
- `sql`
- `tables_used`
- `matched_groups`
- `attempt_count`
- `warnings`
- `cache_hit`
- `cache_source`
- `react_trace`

### Teach

- `learning_status`
- `message`
- `instruction_id`
- `similar_instructions`
- `requires_confirmation`
- `confirmation_token`

### Instructions

- array of instruction objects

### Ingest

Shared fields:

- `inserted`
- `updated`
- `skipped`
- `source`

Route-specific additions:

- groups: `failure_count`, `failed_groups`, `enrichment_summary`
- patterns/instructions: `embedded`

## Standalone Cache Behavior

`/ask` and `/generate-sql` now use:

1. memory exact cache
2. memory semantic cache
3. PostgreSQL exact cache for current epoch
4. PostgreSQL semantic cache for current epoch
5. full pipeline

Returned metadata:

- `cache_hit`
- `cache_source`

`cache_source` values:

- `none`
- `memory_exact`
- `memory_semantic`
- `db_exact`
- `db_semantic`

Teach and ingest mutations bump the persistent cache epoch and clear in-memory
caches.

## Standalone Teach Semantics

The standalone service returns:

- HTTP `200` for controlled learning outcomes
- HTTP `503` only when the DB pool is unavailable

The wrapper passes those results through its normal success/error envelope.

## Error Handling

Wrapper errors:

| Condition | HTTP | Error code |
|---|---:|---|
| invalid JSON body | `400` | `BadRequest` |
| request validation failure | `422` | `ValidationError` |
| NL2SQL base URL missing | `503` | `NL2SQL_NOT_CONFIGURED` |
| upstream timeout | `502` | `NL2SQL_UPSTREAM_TIMEOUT` |
| upstream unavailable | `502` | `NL2SQL_UPSTREAM_UNAVAILABLE` |
| upstream invalid JSON/schema | `502` | `NL2SQL_INVALID_RESPONSE` |
| upstream non-200 | upstream status | `NL2SQL_UPSTREAM_ERROR` |

## Timeout Budget

Current defaults:

```text
Standalone SQL_GENERATION_TIMEOUT: 90s
Standalone ASK_TIMEOUT:            105s
server_1 NL2SQL_TIMEOUT_SECONDS:   120s
```

Keep outer layers above `120s`, including the frontend NL2SQL proxy timeout.

## Direct Upstream Routes

Useful for local debugging:

- `/generate-sql`
- `/ask`
- `/ask/stream`
- `/teach`
- `/teach/confirm`
- `/instructions`
- `/ingest/groups`
- `/ingest/knowledge`
- `/ingest/patterns`
- `/ingest/instructions`
- `/cache/stats`
- `/cache/clear`
- `/telemetry/recent`
- `/telemetry/summary`

## Smoke Examples

Wrapper ask:

```bash
curl -X POST http://127.0.0.1:8010/api/nl2sql/v1/ask \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -d '{"query":"newest payment","top_k":5}' \
  | python3 -m json.tool
```

Wrapper teach:

```bash
curl -X POST http://127.0.0.1:8010/api/nl2sql/v1/teach \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -d '{"instruction_type":"term_mapping","content":"counselor means employee","tables_affected":["employee"]}' \
  | python3 -m json.tool
```
