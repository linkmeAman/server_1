# NL2SQL 503 Fix — Live Server

## Symptom

Frontend shows **"NL2SQL service base URL is not configured"** and the backend
returns `HTTP 503` with error code `NL2SQL_NOT_CONFIGURED`.

## Root Cause

`server_1` reads `NL2SQL_SERVICE_BASE_URL` from its `.env` file at startup
(`/var/www/py-workspace/server_1/.env`). That env block is **missing on the live
server** — it was added to the local repo `.env` but `.env` is git-ignored and
never copied to the server during a code push.

The check that raises the 503 lives in
`app/modules/nl2sql/services/client.py`:

```python
base_url = str(getattr(settings, "NL2SQL_SERVICE_BASE_URL", "") or "").strip()
if not base_url:
    raise Nl2SqlClientError(
        503,
        error_code="NL2SQL_NOT_CONFIGURED",
        message="NL2SQL service base URL is not configured",
        ...
    )
```

## Fix — Steps on the Live Server

SSH into the server and run:

```bash
# 1. Open the server_1 env file
nano /var/www/py-workspace/server_1/.env

# 2. Append the following block at the end of the file (if not already there):
```

```env
# NL2SQL upstream service
NL2SQL_SERVICE_BASE_URL=http://localhost:8080
NL2SQL_TIMEOUT_SECONDS=60
NL2SQL_DEFAULT_TOP_K=5
```

```bash
# 3. Restart server_1
sudo systemctl restart py-server-1

# 4. Confirm service is up
sudo systemctl status py-server-1
```

## Verify NL2SQL Service Is Running

The NL2SQL service must also be running on port 8080 on the same machine.
Check its status before restarting server_1:

```bash
sudo systemctl status nl2sql

# If it is not running, start it:
sudo systemctl start nl2sql

# Check it is reachable:
curl http://localhost:8080/health
# Expected: {"status":"ok","db":"connected"}
```

## Quick End-to-End Smoke Test

After both services are up, test the full chain from the server directly:

```bash
# Replace YOUR_TOKEN with a valid bearer token
curl -s -X POST http://localhost:8010/api/nl2sql/v1/ask \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"query": "newest payment", "top_k": 5}' | python3 -m json.tool
```

Expected shape:

```json
{
  "success": true,
  "data": { "status": "ok", "answer": "..." },
  "message": "NL2SQL ask completed"
}
```

## Why a Code Push Cannot Fix This

`.env` is git-ignored intentionally — it contains secrets. The env block must
be added manually on each environment's `.env` file.

The deploy script (`scripts/deploy_server1.sh`) now includes a pre-deploy
check that warns when `NL2SQL_SERVICE_BASE_URL` is absent so this failure is
caught at deploy time rather than at runtime. See the change in that file.

## Timeout Budget Reminder

Keep timeouts layered from inner to outer:

| Layer | Setting | Recommended |
|---|---|---|
| NL2SQL service internal | model inference | ~120 s |
| server_1 | `NL2SQL_TIMEOUT_SECONDS` | `120` |
| reverse proxy / gateway | nginx / Caddy config | `130 s+` |
| frontend py-proxy | `PY_PROXY_NL2SQL_TIMEOUT_MS` | `125000` |
