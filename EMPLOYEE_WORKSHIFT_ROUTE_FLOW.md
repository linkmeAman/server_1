# Employee Workshift Route Endpoint Handling (With Import Details)

## Endpoint
- Method: `POST`
- Full path: `/api/employee-events/v1/employees/workshift-calendar/query`
- Route declaration: `controllers/employee_events_v1/router.py:111`

## Where This Route Is Mounted
1. `main.py:224` includes `api_router` into FastAPI.
2. `api/v1/router.py:30` includes `employee_events_v1_router`.
3. `controllers/employee_events_v1/__init__.py:3` re-exports `router` from `controllers/employee_events_v1/router.py`.
4. `controllers/employee_events_v1/router.py:26` sets prefix `/api/employee-events/v1`.

## Request Contract
- Pydantic model: `EmployeeWorkshiftCalendarBatchQueryRequest` in `controllers/employee_events_v1/schemas/models.py:63`
- Fields:
- `employee_ids: List[Any]` (`controllers/employee_events_v1/schemas/models.py:64`)
- `from_date: str` (`controllers/employee_events_v1/schemas/models.py:65`)
- `to_date: str` (`controllers/employee_events_v1/schemas/models.py:66`)

## Runtime Handling Flow
1. Router entrypoint receives request in `post_employee_workshift_calendar_query` (`controllers/employee_events_v1/router.py:112`).
2. Auth check runs first via `require_app_access_claims` (`controllers/employee_events_v1/router.py:114`).
3. Body is parsed with `await request.json()` (`controllers/employee_events_v1/router.py:116`).
4. Invalid JSON is converted to `EmployeeEventsError` with code `EMP_EVENT_INVALID_WORKSHIFT_QUERY` (`controllers/employee_events_v1/router.py:118`).
5. Non-object JSON (like array/string) is rejected with `invalid_body_type` (`controllers/employee_events_v1/router.py:125`).
6. Payload is validated by Pydantic model (`controllers/employee_events_v1/router.py:134`).
7. Validation errors are mapped to `EMP_EVENT_INVALID_WORKSHIFT_QUERY` and returned as 400 (`controllers/employee_events_v1/router.py:136`).
8. Service call: `get_employee_workshift_calendar_batch(employee_ids, from_date, to_date)` (`controllers/employee_events_v1/router.py:143`).
9. Success is wrapped with `success_response(...)` (`controllers/employee_events_v1/router.py:148`).
10. Any `EmployeeEventsError` is normalized by `_error_response` (`controllers/employee_events_v1/router.py:152`).
11. `_error_response` sets `X-Request-ID` header and standardized error envelope (`controllers/employee_events_v1/router.py:31`).

## Service Logic for This Endpoint
- Main method: `controllers/employee_events_v1/services/event_service.py:1040`
- Steps:
1. Normalize + dedupe + validate `employee_ids` (`event_service.py:165`).
2. Parse `from_date` and `to_date` as `YYYY-MM-DD` (`event_service.py:251`).
3. Reject if `from_date > to_date` (`event_service.py:1050`).
4. Reject if inclusive date range > 62 days (`event_service.py:1055`).
5. Resolve timezone from `EMP_EVENT_TIMEZONE` (`event_service.py:645`).
6. Build inclusive date list (`event_service.py:642`).
7. Fetch employee workshift rows from repository (`event_service.py:1064`).
8. Build one output item per requested employee (preserves input order after dedupe) (`event_service.py:1071`).
9. Return batch response (`timezone`, range metadata, counts, employees) (`event_service.py:1081`).

### Per-Employee Result Builder
- `event_service.py:801` `_build_workshift_batch_result(...)`
- Result statuses:
- `not_found`: no active employee row found (`event_service.py:809`)
- `unconfigured`: workshift exists but ID/time config invalid (`event_service.py:853`)
- `configured`: valid workshift + generated `calendar_days` (`event_service.py:872`)

### Calendar Day Generation
- `event_service.py:751` `_build_calendar_days(...)`
- Rules:
- `weekday` is Sunday=0 to Saturday=6 (`event_service.py:747`)
- Week-off days return `shift_start=null`, `shift_end=null` (`event_service.py:769`)
- Overnight shift if `out_time <= in_time`; end date rolls to next day (`event_service.py:763`, `event_service.py:784`)

### Week-Off Parsing
- `event_service.py:715` `_decode_week_off_code(...)`
- Accepts comma/space separated tokens; keeps valid 0..6; invalid tokens become warnings.

## Repository Logic for This Endpoint
- Method: `controllers/employee_events_v1/services/event_repository.py:568`
- Query behavior:
- Selects from `emp_cont_view`
- Uses `e.id` as `employee_id`
- Enforces active filters `e.park = '0'` and `e.status = '1'`
- Returns columns:
- `employee_id`, `employee_name`, `workshift_id`, `workshift_in_time`, `workshift_out_time`, `week_off_code`
- SQL is built with bound placeholders per employee id and executed once for the batch (`event_repository.py:580` to `event_repository.py:599`).

## Auth Path Used by This Endpoint
- Entry: `controllers/employee_events_v1/dependencies.py:28` `require_app_access_claims(...)`
- Behavior:
1. Requires `Authorization: Bearer <token>` header.
2. Tries legacy token validation via `core.security.validate_token` (`dependencies.py:52`).
3. If legacy fails, tries auth-v2 validation via `verify_v2_access_token` (`dependencies.py:59`).
4. On failure returns `EMP_EVENT_UNAUTHORIZED` (401) with both reasons.

## Import Details (Complete for Endpoint Path)

### `main.py`
- `import logging` (`main.py:4`) - logging setup.
- `import os` (`main.py:5`) - path/env helpers.
- `import sys` (`main.py:6`) - Python path setup.
- `from contextlib import asynccontextmanager` (`main.py:7`) - app lifespan context.
- `from uuid import uuid4` (`main.py:8`) - request id for auth-v2 error handler.
- `from fastapi import FastAPI, Request` (`main.py:9`) - app + request typing.
- `from fastapi.responses import JSONResponse` (`main.py:10`) - error responses.
- `from fastapi.exceptions import RequestValidationError` (`main.py:11`) - validation handler.
- `from starlette.exceptions import HTTPException as StarletteHTTPException` (`main.py:12`) - HTTP error handler.
- `from core.settings import get_settings` (`main.py:17`) - runtime settings.
- `from core.router import router as dynamic_router` (`main.py:18`) - legacy dynamic routes.
- `from core.middleware import setup_middleware` (`main.py:19`) - middleware registration.
- `from core.response import error_response` (`main.py:20`) - error envelope.
- `from core.exceptions import DynamicAPIException` (`main.py:21`) - custom error type.
- `from core.database import init_database` (`main.py:22`) - DB init at startup.
- `from api.v1.router import api_router` (`main.py:23`) - explicit v1 routes (includes this endpoint).
- `from controllers.auth_v2.services.common import AuthV2Error` (`main.py:24`) - auth-v2 exception handler.

### `api/v1/router.py`
- `from fastapi import APIRouter` (`api/v1/router.py:3`) - central v1 router.
- `from controllers.api.auth import router as auth_router` (`api/v1/router.py:5`) - auth routes.
- `from controllers.auth_v2.router import router as auth_v2_router` (`api/v1/router.py:6`) - auth-v2 routes.
- `from controllers.api.example import router as example_router` (`api/v1/router.py:7`) - example routes.
- `from controllers.api.geosearch import router as geosearch_router` (`api/v1/router.py:8`) - geosearch routes.
- `from controllers.api.llm import router as llm_router` (`api/v1/router.py:9`) - llm routes.
- `from controllers.api.query_gateway import router as query_gateway_router` (`api/v1/router.py:10`) - query gateway routes.
- `from controllers.employee_events_v1 import router as employee_events_v1_router` (`api/v1/router.py:11`) - employee events routes (this endpoint path).
- `from controllers.google_calendar_v1 import router as google_calendar_v1_router` (`api/v1/router.py:12`) - google calendar routes.
- `from controllers.internal.sqlgw_admin import router as sqlgw_admin_router` (`api/v1/router.py:13`) - sqlgw admin routes.
- `from controllers.orders import router as orders_router` (`api/v1/router.py:14`) - orders routes.
- `from core.settings import get_settings` (`api/v1/router.py:15`) - conditional route include flags.

### `controllers/employee_events_v1/__init__.py`
- `from .router import router` (`controllers/employee_events_v1/__init__.py:3`) - exposes module router for `api/v1/router.py`.

### `controllers/employee_events_v1/router.py`
- `from __future__ import annotations` (`router.py:3`) - postponed type evaluation.
- `from typing import Optional` (`router.py:5`) - optional query params for other routes in this file.
- `from uuid import uuid4` (`router.py:6`) - request id generation in `_error_response`.
- `from fastapi import APIRouter, Path, Query, Request` (`router.py:8`) - route decorators, param metadata, request object.
- `from fastapi.responses import JSONResponse` (`router.py:9`) - custom error response object.
- `from pydantic import ValidationError` (`router.py:10`) - request body validation errors.
- `from core.response import error_response, success_response` (`router.py:12`) - standard envelopes.
- `from .dependencies import EmployeeEventsError, require_app_access_claims` (`router.py:14`) - domain error + auth guard.
- `from .schemas.models import (...)` (`router.py:15`) - payload models; this endpoint uses `EmployeeWorkshiftCalendarBatchQueryRequest`.
- `from .services.event_service import EmployeeEventsService` (`router.py:24`) - business logic service.

### `controllers/employee_events_v1/dependencies.py`
- `from __future__ import annotations` (`dependencies.py:3`) - postponed type evaluation.
- `from typing import Any, Dict, Optional` (`dependencies.py:5`) - typed claims/error payloads.
- `from controllers.auth_v2.services.token_service import verify_v2_access_token` (`dependencies.py:7`) - auth-v2 token validation fallback.
- `from core.security import validate_token` (`dependencies.py:8`) - legacy token validation.

### `controllers/employee_events_v1/schemas/models.py`
- `from __future__ import annotations` (`schemas/models.py:3`) - postponed type evaluation.
- `from typing import Any, List, Optional` (`schemas/models.py:5`) - flexible request payload typing.
- `from pydantic import BaseModel, Field` (`schemas/models.py:7`) - request schema definitions.

### `controllers/employee_events_v1/services/event_service.py`
- `from __future__ import annotations` (`event_service.py:3`) - postponed type evaluation.
- `import logging` (`event_service.py:5`) - service logging.
- `import re` (`event_service.py:6`) - week-off token parsing.
- `from datetime import date as date_value, datetime, time as time_value, timedelta` (`event_service.py:7`) - date/time parsing and range generation.
- `from typing import Any, Dict, List, Optional, Tuple` (`event_service.py:8`) - typed service interfaces.
- `from zoneinfo import ZoneInfo, ZoneInfoNotFoundError` (`event_service.py:9`) - timezone resolution for shift timestamps.
- `from core.settings import get_settings` (`event_service.py:11`) - reads `EMP_EVENT_TIMEZONE`, status constants, sync flags.
- `from ..dependencies import EmployeeEventsError` (`event_service.py:13`) - domain error transport.
- `from .event_repository import EmployeeEventsRepository` (`event_service.py:14`) - DB data source for workshift rows.
- `from .google_payload_builder import build_google_event_payload` (`event_service.py:15`) - used by other service methods (not by this endpoint).
- `from .google_sync_repository import EmployeeEventGoogleSyncRepository` (`event_service.py:16`) - used by sync-related methods (not by this endpoint).
- `from ...google_calendar_v1.services.google_client import GoogleCalendarClient` (`event_service.py:17`) - used by sync-related methods.
- `from ...google_calendar_v1.services.token_manager import GoogleCalendarTokenManager` (`event_service.py:18`) - used by sync-related methods.

### `controllers/employee_events_v1/services/event_repository.py`
- `from __future__ import annotations` (`event_repository.py:3`) - postponed type evaluation.
- `import re` (`event_repository.py:5`) - SQL identifier safety regex.
- `from typing import Any, Dict, List, Optional` (`event_repository.py:6`) - typed repository interfaces.
- `from sqlalchemy import inspect, text` (`event_repository.py:8`) - schema introspection + SQL text execution.
- `from core.database import engines` (`event_repository.py:10`) - runtime DB engine registry (`default` engine used).
- `from ..dependencies import EmployeeEventsError` (`event_repository.py:12`) - standardized repository errors.

### Indirectly Used Supporting Modules

#### `core/response.py`
- `from typing import Any, Optional, Dict, List` (`core/response.py:4`) - generic response payload types.
- `from pydantic import BaseModel` (`core/response.py:5`) - response model classes.
- `from datetime import datetime` (`core/response.py:6`) - response timestamp field.

#### `core/security.py`
- `import hashlib` (`core/security.py:3`) - deterministic PASETO key derivation.
- `import json` (`core/security.py:4`) - token payload serialization/parsing.
- `import secrets` (`core/security.py:5`) - token ids and reset tokens.
- `from datetime import datetime, timedelta, timezone` (`core/security.py:6`) - token iat/exp handling.
- `from typing import Any, Dict, Optional` (`core/security.py:7`) - typed token payloads.
- `from .settings import get_settings` (`core/security.py:9`) - security settings values.

#### `controllers/auth_v2/services/token_service.py`
- `from __future__ import annotations` (`token_service.py:3`) - postponed type evaluation.
- `import base64` (`token_service.py:5`) - decode token footer segment.
- `import json` (`token_service.py:6`) - payload/footer serialization.
- `from datetime import timedelta` (`token_service.py:7`) - token expiry windows.
- `from typing import Any, Dict, List, Optional` (`token_service.py:8`) - typed claims.
- `from controllers.auth_v2.constants import (...)` (`token_service.py:10`) - token constants and error codes.
- `from controllers.auth_v2.services.common import AuthV2Error, random_jti, utcnow` (`token_service.py:16`) - auth-v2 error + timing/jti utilities.
- `from controllers.auth_v2.services.keyring import get_current_key, get_key_for_kid` (`token_service.py:17`) - active key lookup.
- `from core.settings import get_settings` (`token_service.py:18`) - issuer/audience/version and TTL settings.

#### `core/database.py`
- `import logging` (`core/database.py:3`) - DB init logs.
- `from typing import Dict, Generator, Optional` (`core/database.py:4`) - engine/session typing.
- `from urllib.parse import quote_plus` (`core/database.py:5`) - safe password encoding in DB URL.
- `from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine, text` (`core/database.py:7`) - engine creation + models + SQL execution.
- `from sqlalchemy.engine import Engine` (`core/database.py:8`) - engine typing.
- `from sqlalchemy.orm import Session, declarative_base, sessionmaker` (`core/database.py:9`) - ORM session/base setup.
- `from .settings import get_settings` (`core/database.py:11`) - DB config values.

## Error Codes Seen in This Endpoint Path
- `EMP_EVENT_UNAUTHORIZED` - missing/invalid/expired bearer token (`dependencies.py:36`, `dependencies.py:63`).
- `EMP_EVENT_INVALID_WORKSHIFT_QUERY` - malformed JSON, bad body shape, validation failures, bad IDs/dates/range (`router.py:118`, `router.py:126`, `router.py:136`, `event_service.py:93`).
- `EMP_EVENT_SERVICE_MISCONFIGURED` - invalid/missing `EMP_EVENT_TIMEZONE` (`event_service.py:105`, `event_service.py:652`, `event_service.py:657`).
- `EMP_EVENT_DB_UNAVAILABLE` - default DB engine missing (`event_repository.py:78`).

## Test Coverage References
- Route auth/validation/success/failure: `tests/test_employee_events_v1_routes.py:99`, `:133`, `:152`, `:632`, `:701`
- Service workshift logic: `tests/test_employee_events_v1_service.py:389` to `:576`
- Repository workshift query behavior: `tests/test_employee_events_v1_repository.py:174` to `:201`
