# NL2SQL Backend Integration

## Summary

`server_1` exposes an authenticated NL2SQL wrapper under `/api/nl2sql/v1`.
It validates app-facing payloads, enforces `ai-chat:read`, forwards requests to
the standalone NL2SQL service, validates upstream responses, and returns the
standard backend success/error envelope.

## Wrapper Routes

| Method | Path | Upstream |
|---|---|---|
| `POST` | `/api/nl2sql/v1/ask` | `/ask` |
| `POST` | `/api/nl2sql/v1/generate-sql` | `/generate-sql` |
| `POST` | `/api/nl2sql/v1/teach` | `/teach` |
| `POST` | `/api/nl2sql/v1/teach/confirm` | `/teach/confirm` |
| `GET` | `/api/nl2sql/v1/instructions` | `/instructions` |
| `GET` | `/api/nl2sql/v1/failures` | `/failures` |
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

The resolved request id is:

- forwarded to the upstream service in the JSON body when supported
- forwarded as `X-Request-ID`
- returned in the wrapper response header
- included in error envelopes under `data.request_id`

## Request Models

### Ask and Generate SQL

```json
{
  "query": "show unpaid invoices by counselor",
  "top_k": 5,
  "request_id": "optional-request-id"
}
```

### Teach

```json
{
  "instruction_type": "term_mapping",
  "content": "counselor means employee",
  "tables_affected": ["employee"],
  "source_query": "show counselor invoices"
}
```

### Teach Confirm

```json
{
  "confirmation_token": "TOKEN",
  "action": "replace"
}
```

### Instructions Query

Query params:

- `instruction_type`
- `active_only`

### Ingest Groups

```json
{
  "group_names": ["inquiry_lifecycle"]
}
```

### Ingest Knowledge

Supports the standalone service include flags and limits:

- `include_column_catalog`
- `include_sql_examples`
- `include_relations`
- `include_graph`
- `include_view_registry`
- `include_onboarding_rules`
- `column_limit`
- `sql_example_limit`
- `relation_limit`
- `graph_limit`
- `view_registry_limit`

`/ingest/patterns` and `/ingest/instructions` use empty JSON bodies.

## Response Contracts

All successful wrapper responses use the normal backend envelope:

```json
{
  "success": true,
  "data": {},
  "message": "..."
}
```

### Cache Metadata

`/ask` and `/generate-sql` expose first-class cache fields:

- `cache_hit`
- `cache_source`

`cache_source` values:

- `none`
- `memory_exact`
- `memory_semantic`
- `db_exact`
- `db_semantic`

### Teach Responses

Wrapper passes through:

- `learning_status`
- `message`
- `instruction_id`
- `similar_instructions`
- `requires_confirmation`
- `confirmation_token`

### Instructions Responses

Wrapper returns the validated array of instruction objects from the upstream
service.

### Ingest Responses

Wrapper passes through version-aware ingest counters:

- `inserted`
- `updated`
- `skipped`
- `source`

Extra fields by route:

- groups: `failure_count`, `failed_groups`, `enrichment_summary`
- patterns/instructions: `embedded`

## Error Handling

Known wrapper errors:

| Condition | HTTP | Error code |
|---|---:|---|
| invalid JSON body | `400` | `BadRequest` |
| non-object JSON body | `400` | `BadRequest` |
| request validation failure | `422` | `ValidationError` |
| missing `NL2SQL_SERVICE_BASE_URL` | `503` | `NL2SQL_NOT_CONFIGURED` |
| upstream timeout | `502` | `NL2SQL_UPSTREAM_TIMEOUT` |
| upstream connectivity failure | `502` | `NL2SQL_UPSTREAM_UNAVAILABLE` |
| upstream non-200 response | upstream status | `NL2SQL_UPSTREAM_ERROR` |
| upstream invalid JSON | `502` | `NL2SQL_INVALID_RESPONSE` |
| upstream schema mismatch | `502` | `NL2SQL_INVALID_RESPONSE` |

## Timeouts

Current backend default from `app/core/settings.py`:

```env
NL2SQL_TIMEOUT_SECONDS=120
NL2SQL_DEFAULT_TOP_K=5
```

Timeouts should remain ordered from inner to outer:

```text
Standalone /generate-sql timeout: 90s
Standalone /ask timeout:         105s
server_1 wrapper timeout:        120s
Frontend py-proxy timeout:       > 120s
External reverse proxy:          > frontend timeout
```

## Related Docs

- `docs/NL2SQL_API_INTEGRATION_FLOW.md`
- `README.md`
