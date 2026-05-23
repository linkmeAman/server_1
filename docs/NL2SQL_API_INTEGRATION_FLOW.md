# NL2SQL API Integration Flow

Full reference for every API call that flows through the NL2SQL integration — from the
authenticated server_1 wrapper routes, through the upstream standalone NL2SQL service,
down to the final response shapes returned to callers.

> **Live-verified** — All responses in this document were captured from real API calls
> against the running services on `2026-05-23`. Both services were healthy:
> - NL2SQL upstream (`port 8080`): `{"status":"ok","db":"connected"}`
> - server_1 (`port 8010`): `{"success":true,"data":{"status":"healthy"}}`

---

## Architecture Overview

```
Caller (Frontend / Next py-proxy)
  │
  │  POST /api/nl2sql/v1/ask
  │  POST /api/nl2sql/v1/generate-sql
  │  Authorization: Bearer <token>
  ▼
server_1  (man-6100, port 8010)
  │  require_nl2sql_access  →  PRISM PDP  (action: ai-chat:read)
  │  Nl2SqlRequest validation
  │  Nl2SqlClient._post()
  │
  │  POST /ask           (mirrors server_1 /ask)
  │  POST /generate-sql  (mirrors server_1 /generate-sql)
  │  X-Request-ID forwarded
  ▼
Standalone NL2SQL service  (port 8080)
  │  ReAct loop  →  qwen3:4b  (planner)
  │                 deepseek-coder:6.7b  (SQL writer)
  │  pgvector retrieval  (PostgreSQL ragdb)
  │  governance rulebook injection
  │  MySQL EXPLAIN validation
  │  (for /ask only) MySQL bounded execution  +  answer model
  ▼
Validated upstream response
  │
  ▼
server_1  response adapter  (Pydantic discriminated union)
  │  success_response / error_response envelope
  ▼
Caller  JSON  +  X-Request-ID response header
```

---

## server_1 Exposed Routes

Registered in `app/api/v1/router.py` via:
```python
api_router.include_router(nl2sql_router)
```

| Method | Path | Source file | Upstream path |
|--------|------|-------------|---------------|
| `POST` | `/api/nl2sql/v1/ask` | `app/modules/nl2sql/router.py` | `/ask` |
| `POST` | `/api/nl2sql/v1/generate-sql` | `app/modules/nl2sql/router.py` | `/generate-sql` |

---

## Authentication and Authorization

**Dependency:** `require_nl2sql_access` (`app/modules/nl2sql/dependencies.py`)

Flow:
1. `require_any_caller` resolves the bearer token to a `CallerContext`.
2. If `caller.is_super` → access granted immediately.
3. Otherwise a PRISM PDP call is made:
   - `action`: `ai-chat:read`
   - `resource_type`: `ai-chat`
   - `resource_id`: `*`
   - `request_context`: `{ path, method, sourceIp }`
4. `decision != "Allow"` → `HTTP 403` with `"NL2SQL access denied by PRISM policy"`.

---

## Request ID Resolution

Order of precedence (first non-blank wins):
1. `request_id` field in the JSON body
2. `X-Request-ID` request header
3. Auto-generated UUID

The resolved ID is:
- forwarded to the upstream NL2SQL service in the JSON body **and** the `X-Request-ID` header.
- returned to the caller in the `X-Request-ID` response header.
- included in all error envelopes under `data.request_id`.

---

## Shared Request Body

Both routes accept identical JSON:

```json
{
  "query": "show unpaid invoices by counselor",
  "top_k": 5,
  "request_id": "9ad31f85-23e3-41d1-80bd-db5ad92ccce5"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `query` | string | yes | Trimmed; must not be blank |
| `top_k` | integer ≥ 0 | no | `null` or `0` fall back to `NL2SQL_DEFAULT_TOP_K` |
| `request_id` | string | no | Empty strings normalize to `null` |

Unknown fields are silently ignored (`extra="ignore"`).

---

## Upstream Payload Sent by server_1

```json
{
  "query": "show unpaid invoices by counselor",
  "top_k": 5,
  "request_id": "9ad31f85-23e3-41d1-80bd-db5ad92ccce5"
}
```

Headers forwarded:
```
Content-Type: application/json
Accept: application/json
X-Request-ID: <resolved-request-id>
```

`top_k` fallback in `Nl2SqlClient._post()`:
- `null` or `0` → replaced with `NL2SQL_DEFAULT_TOP_K` (default `5`)
- positive value → forwarded as-is

---

## POST /api/nl2sql/v1/ask

### What it does

1. Validates the request body.
2. Checks NL2SQL access via PRISM.
3. Calls the upstream NL2SQL service `/ask`.
4. Upstream runs `/generate-sql` internally, executes the generated SQL on MySQL (max 50 rows), and calls an answer model.
5. Returns a natural-language answer with SQL metadata.

### Success Response — `status: "ok"`

HTTP `200`

Live response from `POST /ask` query `"newest payment"` (`2026-05-23`):

```json
{
  "success": true,
  "data": {
    "status": "ok",
    "answer": "Found 1 row.\n\nid | invoice_id | date | amount | actual_amount | calculated_amount | receipt | pay_mode_text\n42993 | 42085 | 2026-05-23 | 660.0 | 646.8 | 0.0 | 11336 | Cash",
    "sql": "SELECT id, invoice_id, date, amount, actual_amount, calculated_amount, receipt, pay_mode_text FROM payment ORDER BY date DESC, created_at DESC, modified_at DESC, id DESC LIMIT 1",
    "warnings": [],
    "row_count": 1,
    "columns": ["id", "invoice_id", "date", "amount", "actual_amount", "calculated_amount", "receipt", "pay_mode_text"],
    "tables_used": ["payment"],
    "matched_groups": ["deterministic_payment"],
    "attempt_count": 0,
    "react_trace": null
  },
  "message": "NL2SQL ask completed"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | `"ok"` | Generation, execution, and answer all succeeded |
| `answer` | string | Natural-language answer from the answer model |
| `sql` | string | The SQL that was executed |
| `row_count` | integer | Rows returned (capped at 50) |
| `columns` | string[] | Column names from the result set |
| `tables_used` | string[] | MySQL tables referenced by the SQL |
| `matched_groups` | string[] | Schema groups matched during retrieval |
| `attempt_count` | integer | Completed ReAct iterations |
| `warnings` | object[] | Non-blocking warning objects (see warning codes below) |
| `react_trace` | object\|null | Step-by-step ReAct reasoning trace |

### Clarification Response — `status: "clarification_needed"`

HTTP `200`

```json
{
  "success": true,
  "data": {
    "status": "clarification_needed",
    "question": "Are you searching for an employee record or a contact record?",
    "suggestions": [
      "find employee with contact name aman",
      "search contact by name aman"
    ],
    "original_query": "fetch aman",
    "failure_reason": "Cannot determine correct table structure",
    "react_trace": { "steps": [...], "total_iterations": 1, "final_action": "GIVE_UP" }
  },
  "message": "NL2SQL ask completed"
}
```

| Field | Description |
|-------|-------------|
| `question` | The clarifying question for the user |
| `suggestions` | 2-3 refined query alternatives |
| `original_query` | The original query as received |
| `failure_reason` | Why generation could not proceed |

*Not present:* `answer`, `sql`, `row_count`, `columns`, `tables_used`, `matched_groups`, `attempt_count`, `warnings`

### Rejected Response — `status: "rejected"`

HTTP `200`

```json
{
  "success": true,
  "data": {
    "status": "rejected",
    "answer": null,
    "sql": null,
    "warnings": [
      { "code": "OLLAMA_TIMEOUT", "message": "Reasoning model timed out after 45s" }
    ],
    "attempt_count": 0,
    "react_trace": null
  },
  "message": "NL2SQL ask completed"
}
```

`sql` semantics:
- `null` → SQL generation itself failed (transport / upstream / malformed response)
- non-null string → SQL generation succeeded but MySQL execution failed

---

## POST /api/nl2sql/v1/generate-sql

### What it does

1. Validates the request body.
2. Checks NL2SQL access via PRISM.
3. Calls the upstream NL2SQL service `/generate-sql`.
4. Upstream runs a full ReAct loop and returns SQL or a clarification/rejection — **SQL is never executed**.
5. Returns the SQL preview plus metadata.

### Success Response — `status: "ok"`

HTTP `200`

Live response from `POST /generate-sql` query `"newest payment"` (`2026-05-23`):

```json
{
  "success": true,
  "data": {
    "status": "ok",
    "sql": "SELECT id, invoice_id, date, amount, actual_amount, calculated_amount, receipt, pay_mode_text FROM payment ORDER BY date DESC, created_at DESC, modified_at DESC, id DESC LIMIT 1",
    "warnings": [],
    "tables_used": ["payment"],
    "matched_groups": ["deterministic_payment"],
    "attempt_count": 0,
    "cache_hit": false,
    "react_trace": null
  },
  "message": "NL2SQL SQL preview completed"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | `"ok"` | SQL was generated and passed all guardrails |
| `sql` | string | The generated SQL (not yet executed) |
| `tables_used` | string[] | Tables referenced in the SQL |
| `matched_groups` | string[] | Schema groups matched during retrieval |
| `attempt_count` | integer | Completed ReAct iterations |
| `cache_hit` | boolean | `true` if this result came from the in-memory SQL cache |
| `warnings` | object[] | Non-blocking warnings (e.g. `REVIEW_FAILED`) |
| `react_trace` | object\|null | Full ReAct step trace |

### Clarification Response — `status: "clarification_needed"`

HTTP `200`

```json
{
  "success": true,
  "data": {
    "status": "clarification_needed",
    "question": "Do you mean the `employee` table or the `contact` table?",
    "suggestions": ["show invoices by employee", "show invoices by contact"],
    "original_query": "show invoices by counselor",
    "failure_reason": "TABLE_OUT_OF_SCOPE, MAX_RETRIES_EXCEEDED",
    "cache_hit": false,
    "react_trace": { "steps": [...] }
  },
  "message": "NL2SQL SQL preview completed"
}
```

*Not present:* `sql`, `warnings`, `tables_used`, `matched_groups`, `attempt_count`

### Rejected Response — `status: "rejected"`

HTTP `200`

```json
{
  "success": true,
  "data": {
    "status": "rejected",
    "sql": null,
    "warnings": [
      { "code": "REQUEST_TIMEOUT", "message": "SQL generation timed out after 90s" }
    ],
    "attempt_count": 0,
    "cache_hit": false,
    "react_trace": null
  },
  "message": "NL2SQL SQL preview completed"
}
```

*Not present:* `question`, `suggestions`, `tables_used`, `matched_groups`

---

## ReAct Loop (Inside Upstream `/generate-sql`)

The upstream service runs a multi-step ReAct agent before returning SQL.

| Action | Model | Description |
|--------|-------|-------------|
| `RETRIEVE_MORE_CONTEXT` | — | Re-queries pgvector schema groups with refined terms |
| `FETCH_SCHEMA` | — | Loads live MySQL columns for one or more tables |
| `GENERATE_SQL` | `deepseek-coder:6.7b` | Writes SQL from context; validated immediately in the same iteration |
| `VALIDATE_AND_RETURN` | — | Runs full SQL guardrails; returns `ok` if no blocking warnings remain |
| `ASK_CLARIFICATION` | `qwen3:4b` | Terminal — asks the user to rephrase |
| `GIVE_UP` | — | Terminal — cannot safely generate valid SQL |

Planner model: `qwen3:4b` (`think=true`, `num_predict=800`, `REASONING_TEMPERATURE`)
SQL writer: `deepseek-coder:6.7b` (`stream=false`, `temperature=0.0`)
Governance reviewer: `qwen3:4b` (`think=false`, `temperature=0.0`, `max_tokens=150`, timeout `15s`)

SQL guardrails (blocking):
- Must be non-empty
- Must be exactly one statement
- Must be `SELECT` or `WITH … SELECT`
- Must not contain destructive keywords outside comments/string literals
- Tables in `FROM`/`JOIN` must be within `tables_in_scope`
- Columns must be within known live-schema columns (when available)
- MySQL `EXPLAIN` must pass (when app DB is reachable)

---

## Warning Codes Reference

| Code | Surface | Description |
|------|---------|-------------|
| `REQUEST_TIMEOUT` | `/generate-sql`, `/ask` | Full workflow exceeded `SQL_GENERATION_TIMEOUT` or `ASK_TIMEOUT` |
| `OLLAMA_TIMEOUT` | `/generate-sql`, `/ask` | Planner/SQL model call timed out |
| `OLLAMA_UPSTREAM` | `/generate-sql`, `/ask` | Ollama unreachable |
| `OLLAMA_MALFORMED` | `/generate-sql`, `/ask` | Ollama returned unparseable output |
| `SQL_EMPTY` | `/generate-sql`, `/ask` | Generated SQL was blank |
| `SQL_MULTI_STATEMENT` | `/generate-sql`, `/ask` | More than one statement detected |
| `SQL_DESTRUCTIVE` | `/generate-sql`, `/ask` | Destructive keyword found outside safe context |
| `SQL_NOT_SELECT` | `/generate-sql`, `/ask` | Statement is not `SELECT` or `WITH` |
| `TABLE_OUT_OF_SCOPE` | `/generate-sql`, `/ask` | Table not in retrieved schema groups |
| `COLUMN_OUT_OF_SCOPE` | `/generate-sql`, `/ask` | Column not in known live schema |
| `MYSQL_EXPLAIN_ERROR` | `/generate-sql`, `/ask` | MySQL `EXPLAIN` failed |
| `MYSQL_EXPLAIN_UNAVAILABLE` | `/generate-sql`, `/ask` | App DB unreachable (informational, non-blocking) |
| `REVIEW_FAILED` | `/generate-sql`, `/ask` | Governance advisory reviewer flagged the SQL (non-blocking) |
| `MAX_RETRIES_EXCEEDED` | `/generate-sql`, `/ask` | ReAct loop exhausted all iterations |
| `MYSQL_QUERY_ERROR` | `/ask` | MySQL execution error |
| `ANSWER_TIMEOUT` | `/ask` | Answer model call timed out |
| `ANSWER_UPSTREAM` | `/ask` | Answer model unreachable |
| `ANSWER_MALFORMED` | `/ask` | Answer model returned unparseable output |
| `ANSWER_HALLUCINATION` | `/ask` | Answer contains numbers not present in returned rows |

---

## server_1 Error Responses

All errors use the standard backend envelope.

Live response — no `Authorization` header (`2026-05-23`):

```json
{
  "success": false,
  "data": null,
  "message": "Authorization: Bearer <token> header is required",
  "error": "HTTPException",
  "timestamp": "2026-05-23T06:29:56.295493"
}
```

HTTP `401`

Example upstream timeout envelope:

```json
{
  "success": false,
  "data": {
    "request_id": "9ad31f85-23e3-41d1-80bd-db5ad92ccce5"
  },
  "message": "NL2SQL upstream timed out while calling /ask",
  "error": "NL2SQL_UPSTREAM_TIMEOUT"
}
```

| Condition | HTTP | `error` code |
|-----------|-----:|--------------|
| Invalid JSON body | `400` | `BadRequest` |
| Non-object JSON body | `400` | `BadRequest` |
| Field validation failure | `422` | `ValidationError` |
| `NL2SQL_SERVICE_BASE_URL` not set | `503` | `NL2SQL_NOT_CONFIGURED` |
| Upstream HTTP timeout | `502` | `NL2SQL_UPSTREAM_TIMEOUT` |
| Upstream connectivity failure | `502` | `NL2SQL_UPSTREAM_UNAVAILABLE` |
| Upstream non-200 status | upstream status | `NL2SQL_UPSTREAM_ERROR` |
| Upstream returned invalid JSON | `502` | `NL2SQL_INVALID_RESPONSE` |
| Upstream schema mismatch | `502` | `NL2SQL_INVALID_RESPONSE` |
| PRISM access denied | `403` | — (FastAPI `HTTPException`) |
| PRISM PDP error | `500` | — |

`ValidationError` data includes the Pydantic error list under `data.details`:

```json
{
  "success": false,
  "data": {
    "request_id": "...",
    "details": [
      { "type": "string_type", "loc": ["body", "query"], "msg": "Input should be a valid string" }
    ]
  },
  "message": "Request validation failed",
  "error": "ValidationError"
}
```

---

## Upstream NL2SQL Service — All Routes

The standalone NL2SQL service (port `8080`) exposes more routes than server_1 proxies.
The table below shows **all** upstream routes — useful when calling the service directly
during local development or administration.

### Generation routes (proxied by server_1)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/generate-sql` | ReAct SQL generation; never executes SQL |
| `POST` | `/ask` | Generate + execute + natural-language answer (max 50 rows) |
| `POST` | `/ask/stream` | Same as `/ask` but streams `ndjson` progress events |

### Retrieval and ingestion routes

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/ingest` | Embed free text or schema table records |
| `POST` | `/ingest/groups` | Build and embed schema-group chunks from `rag_schema/` |
| `POST` | `/ingest/knowledge` | Embed column catalog, SQL examples, relations, graph, view registry, schema rules |
| `POST` | `/ingest/patterns` | Manually embed learned patterns from `nl2sql_learned_patterns` |
| `POST` | `/ingest/instructions` | Manually embed user instructions from `nl2sql_user_instructions` |
| `POST` | `/query` | Cosine-similarity retrieval across all chunk types |
| `POST` | `/query/groups` | Retrieval limited to `schema_group` chunks with user-instruction injection |

### Learning routes

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/teach` | Save a user-provided instruction (join, rule, term mapping, etc.) |
| `POST` | `/teach/confirm` | Resolve a pending instruction conflict (`confirm` / `replace` / `reject`) |
| `GET` | `/instructions` | List saved user instructions |
| `DELETE` | `/instructions/{instruction_id}` | Soft-delete an instruction |
| `POST` | `/patterns/feedback` | Mark a learned pattern helpful (boost) or unhelpful (deactivate) |

### Ops / observability routes — Live responses

**`GET /health`** (`2026-05-23`):
```json
{"status": "ok", "db": "connected"}
```

**`GET /cache/stats`** (`2026-05-23`):
```json
{"embed_cache_size": 0, "sql_cache_size": 0, "embed_cache_ttl_seconds": 1800, "sql_cache_ttl_seconds": 300}
```

**`GET /telemetry/summary?since_minutes=60`** (`2026-05-23`, no traffic in window):
```json
{"total_requests": 0, "ok_count": 0, "clarification_count": 0, "rejected_count": 0,
 "ok_rate": 0.0, "clarification_rate": 0.0, "rejected_rate": 0.0,
 "review_failed_count": 0, "review_failed_rate": 0.0,
 "avg_latency_ms": 0, "p50_latency_ms": 0, "p95_latency_ms": 0,
 "error_sources": [], "endpoint": null, "since_minutes": 60}
```

**`GET /governance/rules`** (`2026-05-23`, 10/10 rules enabled):
```json
{
  "total_rules": 10,
  "enabled_rules": 10,
  "governance_enabled": true,
  "rules": [
    {"name": "schema_fidelity",       "category": "boundary",     "severity": "hard", "enabled": true},
    {"name": "query_safety",          "category": "safety",       "severity": "hard", "enabled": true},
    {"name": "scope_boundary",        "category": "boundary",     "severity": "hard", "enabled": true},
    {"name": "single_statement",      "category": "safety",       "severity": "hard", "enabled": true},
    {"name": "answer_grounding",      "category": "quality",      "severity": "hard", "enabled": true},
    {"name": "uncertainty_declaration","category": "behavior",    "severity": "hard", "enabled": true},
    {"name": "self_verification",     "category": "verification", "severity": "soft", "enabled": true},
    {"name": "column_selection_quality","category": "quality",   "severity": "soft", "enabled": true},
    {"name": "no_assumptions",        "category": "behavior",     "severity": "soft", "enabled": true},
    {"name": "user_rules_priority",   "category": "behavior",     "severity": "hard", "enabled": true}
  ]
}
```

**`GET /instructions?active_only=true`** — 7 active instructions (`2026-05-23`):
```json
[
  {"id": 6, "instruction_type": "table_relationship", "content": "employee.employee_id = contact.id",
   "tables_affected": ["employee", "contact"], "confidence_score": 0.3, "is_verified": true,
   "use_count": 16, "success_count": 0, "failure_count": 16},
  {"id": 10, "instruction_type": "term_mapping",
   "content": "park and delete are same thing like in tables like park = 1 means the row is deleted",
   "tables_affected": ["batch"], "confidence_score": 0.4, "use_count": 13, "success_count": 2, "failure_count": 11},
  {"id": 9, "instruction_type": "term_mapping", "content": "batches and batch have same context",
   "tables_affected": ["batch"], "confidence_score": 0.4, "use_count": 13},
  {"id": 2, "instruction_type": "term_mapping", "content": "counselor means employee table",
   "tables_affected": ["employee"], "confidence_score": 0.4, "use_count": 14}
]
```

**`GET /telemetry/recent?limit=5`** — last 5 events from DB (`2026-05-23`):
```json
{"results": [
  {"request_id": "9d31ffc6-...", "endpoint": "/ask", "query_text": "show me the 5 most recent inquiries",
   "status": "rejected", "latency_ms": 90006, "warning_codes": ["REQUEST_TIMEOUT"],
   "error_source": "service_timeout", "created_at": "2026-05-18T12:04:48Z"},
  {"request_id": "codex-backend-ask-fast-1", "endpoint": "/ask", "query_text": "newest payment",
   "status": "ok", "latency_ms": 91,
   "stage_latencies_ms": {"execution": 62, "sql_generation": 24, "answer_generation": 0},
   "metadata": {"row_count": 1, "tables_used": ["payment"], "matched_groups": ["deterministic_payment"]}}
]}
```

**`POST /query/groups`** — query `"unpaid invoices by counselor"` top_k `2` (`2026-05-23`):
```json
{
  "matched_groups": ["legacy_invoice_billing", "sales_invoice_billing"],
  "tables_in_scope": [
    "invoice", "invoice_item", "payment", "invoice_change_log", "contact", "member",
    "category", "service", "batch", "branch",
    "sales_invoice_master", "sales_invoice_item", "sales_invoice_payment",
    "sales_invoice_payment_link", "sales_invoice_log", "sales_hsn_master",
    "sales_invoice_series_config", "branch_owner_master"
  ],
  "results": [
    {"similarity": 0.5684, "metadata": {"entity_id": "entity__legacy_invoice_billing",
     "root_table": "invoice", "schema_version": "9ada6b1d", "token_count": 81,
     "embedding_model": "bge-large-en-v1.5"}},
    {"similarity": 0.5570, "metadata": {"entity_id": "entity__sales_invoice_billing",
     "root_table": "sales_invoice_master", "schema_version": "f95bb574", "token_count": 79}}
  ]
}
```

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Service liveness + DB connectivity |
| `GET` | `/telemetry/recent` | Recent request telemetry events |
| `GET` | `/telemetry/summary` | Aggregate KPIs from `nl2sql_request_events` |
| `GET` | `/cache/stats` | In-memory embed/SQL cache sizes and TTLs |
| `POST` | `/cache/clear` | Clear both in-memory caches |
| `GET` | `/governance/rules` | Active governance rulebook |
| `POST` | `/governance/validate` | Advisory SQL review without full pipeline |
| `POST` | `/benchmark/cases` | Add a benchmark case for replay |
| `GET` | `/benchmark/cases` | List stored benchmark cases |

### Documentation routes

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/help` | Browser-rendered documentation hub |
| `GET` | `/help/{module}` | Module-scoped docs (`ops`, `ingestion`, `retrieval`, `learning`, `generation`) |
| `GET` | `/help/{module}/{route_slug}` | Single-route detail page |

---

## `/ask/stream` Progress Events

When calling the upstream `/ask/stream` directly the response is `application/x-ndjson`:

```jsonl
{"event":"started","message":"Received question.","query":"show me the 5 most recent inquiries","top_k":3}
{"event":"sql_generation_started","message":"Retrieving schema context and generating guarded SQL."}
{"event":"sql_generation_running","message":"Still generating and validating SQL."}
{"event":"sql_generation_finished","message":"SQL generated and validated.","sql":"SELECT ...","warnings":[]}
{"event":"execution_started","message":"Executing bounded SQL on the app MySQL database."}
{"event":"execution_finished","message":"SQL execution finished.","row_count":5,"columns":["id","created_at"]}
{"event":"answer_generation_started","message":"Generating final answer from bounded result rows."}
{"event":"answer_generation_running","message":"Still generating final answer."}
{"event":"answer_generation_finished","message":"Final answer is ready.","warnings":[]}
{"event":"final","response":{"status":"ok","answer":"...","sql":"..."}}
```

All possible event names: `started`, `sql_generation_started`, `sql_generation_running`,
`sql_generation_finished`, `sql_generation_rejected`, `row_cap_applied`,
`execution_started`, `execution_finished`, `execution_failed`,
`answer_generation_started`, `answer_generation_running`, `answer_generation_finished`,
`answer_generation_failed`, `final`.

---

## Environment Variables

### server_1 (`man-6100`)

| Variable | Default | Description |
|----------|---------|-------------|
| `NL2SQL_SERVICE_BASE_URL` | `""` | Root URL of the standalone NL2SQL service |
| `NL2SQL_TIMEOUT_SECONDS` | `30` | `httpx` timeout for upstream calls |
| `NL2SQL_DEFAULT_TOP_K` | `5` | Fallback `top_k` when `null` or `0` is sent |

### Standalone NL2SQL service (key generation/ask settings)

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `ollama` | Model provider |
| `LLM_BASE_URL` | — | Ollama root URL |
| `REASONING_MODEL` | `qwen3:4b` | ReAct planner model |
| `LLM_MODEL` | `deepseek-coder:6.7b` | SQL generation model |
| `REACT_MAX_ITERATIONS` | `4` | Max ReAct loop cycles |
| `SQL_GENERATION_TIMEOUT` | `90` | Caps full ReAct SQL workflow (seconds) |
| `ASK_TIMEOUT` | `105` | Caps full `/ask` workflow (seconds) |
| `ANSWER_MODEL` | `qwen3:4b` | Answer generation model |
| `ANSWER_TIMEOUT` | `45` | Answer model timeout (seconds) |
| `GOVERNANCE_ENABLED` | `true` | Enable rulebook injection + advisory SQL review |
| `SQL_CACHE_ENABLED` | `true` | In-memory SQL result cache |
| `SQL_CACHE_TTL_SECONDS` | `300` | SQL cache TTL |
| `EMBED_CACHE_ENABLED` | `true` | In-memory embedding cache |

---

## Timeout Budget

Timeouts must be ordered inner → outer to ensure server_1 returns a controlled JSON response
before any outer gateway issues a generic `504`.

```
Standalone NL2SQL ASK_TIMEOUT:    105s
server_1 NL2SQL_TIMEOUT_SECONDS:  110s+
Frontend py-proxy timeout:        115s+
External reverse proxy:           120s+
Client/UI patience:               120s+
```

---

## Logging

server_1 logs one line per completed upstream request (level `INFO`):

```
NL2SQL request_id=<id> user_id=<user> route=<server-route> upstream=<upstream-route> duration_ms=<ms> status=<status> warnings=<codes>
```

Failure log (level `WARNING`):

```
NL2SQL request_id=<id> user_id=<user> route=<server-route> upstream=<route-name> duration_ms=<ms> status=error upstream_status=<status> warnings=<codes>
```

**Trace workflow:**
1. Capture `X-Request-ID` from the browser or frontend proxy log.
2. Search server_1 logs for `request_id=<id>`.
3. Search standalone NL2SQL telemetry (`GET /telemetry/recent?limit=20`) for the same `request_id`.
4. Compare `duration_ms` across server_1, upstream service, and frontend proxy.

---

## Local Smoke Tests

### server_1 `/ask`

```bash
curl -X POST http://127.0.0.1:8010/api/nl2sql/v1/ask \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "X-Request-ID: 9ad31f85-23e3-41d1-80bd-db5ad92ccce5" \
  -d '{"query":"newest payment","top_k":5,"request_id":"9ad31f85-23e3-41d1-80bd-db5ad92ccce5"}' \
  | python3 -m json.tool
```

### server_1 `/generate-sql`

```bash
curl -X POST http://127.0.0.1:8010/api/nl2sql/v1/generate-sql \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -d '{"query":"show unpaid invoices by counselor","top_k":5}' \
  | python3 -m json.tool
```

### Upstream service directly

```bash
# health
curl -s http://localhost:8080/health | python3 -m json.tool

# generate SQL
curl -s -X POST http://localhost:8080/generate-sql \
  -H "Content-Type: application/json" \
  -d '{"query":"show unpaid invoices by counselor","top_k":3}' \
  | python3 -m json.tool

# ask (generate + execute + answer)
curl -s -X POST http://localhost:8080/ask \
  -H "Content-Type: application/json" \
  -d '{"query":"newest payment","top_k":5}' \
  | python3 -m json.tool

# streaming ask
curl -N -s -X POST http://localhost:8080/ask/stream \
  -H "Content-Type: application/json" \
  -d '{"query":"show me the 5 most recent inquiries","top_k":3}'

# telemetry
curl -s 'http://localhost:8080/telemetry/recent?limit=20&endpoint=/ask' \
  | python3 -m json.tool

# governance rules
curl -s http://localhost:8080/governance/rules | python3 -m json.tool

# cache stats
curl -s http://localhost:8080/cache/stats | python3 -m json.tool
```
