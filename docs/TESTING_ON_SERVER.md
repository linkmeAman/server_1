# Testing On Server

Run these commands on the server from the project root.

## 1. Activate Environment

```bash
cd /var/www/py-workspace/server_1
source pyenv/bin/activate
```

For a branch-specific developer instance, replace the path with your branch directory, for example:

```bash
cd /var/www/py-workspace/server_1_developer/vicky-6101
source pyenv/bin/activate
```

## 2. Install Dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If `pytest` is still missing:

```bash
python -m pip install pytest pytest-asyncio
```

## 3. Fast Syntax Check

```bash
python -m compileall app tests main.py routes scripts alembic
```

## 4. Apply Database Migrations

Run migrations before restarting code that depends on new tables:

```bash
alembic upgrade head
```

For notification changes, verify these tables exist after migration:

- `notification_event`
- `notification_user_state`
- `notification_user_preference`

If Alembic fails, do not restart the service.

## 5. Run Full Test Suite

```bash
python -m pytest tests -q
```

## 6. Run Targeted Suites

Examples:

```bash
python -m pytest tests/test_routing_phase1.py -q
python -m pytest tests/test_employee_events_v1_routes.py -q
python -m pytest tests/test_google_calendar_v1_routes.py -q
python -m pytest tests/test_query_gateway_cache_and_rate_limit.py -q
python -m pytest tests/auth -q
```

## 7. Restart Service After Passing Tests

Example production service:

```bash
sudo systemctl restart py-server-1
sudo systemctl status py-server-1
journalctl -u py-server-1 -f
```

## 8. Useful Smoke Tests

```bash
curl -I http://127.0.0.1:8010/health
curl -X POST http://127.0.0.1:8010/api/query/gateway \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <token>' \
  -d '{}'
```

If you intentionally run with `DEBUG=True`, you can also check:

```bash
curl -I http://127.0.0.1:8010/docs
```

Notification smoke checks:

```bash
curl -N http://127.0.0.1:8010/api/notifications/v1/stream \
  -H 'Authorization: Bearer <token>'

curl http://127.0.0.1:8010/api/notifications/v1/recent?limit=10 \
  -H 'Authorization: Bearer <token>'
```

## 9. What To Verify After Hardening

- startup does not fail due to missing production secrets in `.env`
- PRISM-protected dynamic routes only work when explicit allow patterns exist
- auth login and refresh still work
- SQL gateway and employee-events routes still mount correctly
- notification recent/read/clear/preference routes work after Alembic migrations
