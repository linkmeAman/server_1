# NL2SQL 503 Fix

## Symptom

`server_1` returns HTTP `503` with `NL2SQL_NOT_CONFIGURED` and the frontend
cannot reach the NL2SQL wrapper.

## Cause

`NL2SQL_SERVICE_BASE_URL` is missing from the `server_1` environment. The check
lives in `app/modules/nl2sql/services/client.py`.

## Required Env Block

Add this to the live `server_1` `.env`:

```env
NL2SQL_SERVICE_BASE_URL=http://localhost:8080
NL2SQL_TIMEOUT_SECONDS=120
NL2SQL_DEFAULT_TOP_K=5
```

## Recovery Steps

1. Edit `/var/www/py-workspace/server_1/.env`
2. Add the env block above if it is missing
3. Restart `server_1`
4. Verify the standalone NL2SQL service on port `8080`

Example:

```bash
sudo systemctl restart py-server-1
sudo systemctl status py-server-1
curl http://localhost:8080/health
```

Expected health response:

```json
{"status":"ok","db":"connected"}
```

## Quick End-to-End Check

```bash
curl -s -X POST http://localhost:8010/api/nl2sql/v1/ask \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"query":"newest payment","top_k":5}' \
  | python3 -m json.tool
```

## Timeout Reminder

Current defaults across the stack should stay ordered:

| Layer | Setting | Recommended |
|---|---|---|
| standalone `/generate-sql` | `SQL_GENERATION_TIMEOUT` | `90` |
| standalone `/ask` | `ASK_TIMEOUT` | `105` |
| `server_1` wrapper | `NL2SQL_TIMEOUT_SECONDS` | `120` |
| frontend NL2SQL proxy | `PY_PROXY_NL2SQL_TIMEOUT_MS` | greater than `120000` |
| outer reverse proxy | nginx/Caddy/etc. | greater than frontend timeout |

If an outer layer times out first, callers will see a transport error instead
of the controlled NL2SQL response.
