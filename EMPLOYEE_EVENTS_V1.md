# Employee Events V1 API

Employee Events V1 stores local event records first and synchronizes with Google Calendar only on approval.

## Base Path

- `/api/employee-events/v1`

## Auth

All endpoints require:

- `Authorization: Bearer <app_access_token>`

## Core Rules

- Local event write happens before Google sync.
- `status=1` means approved.
- `park=1` means parked.
- Google create happens on approve.
- Google delete happens when park is set to `1`.
- If Google delete fails during park, local park remains updated and sync failure is stored.

## Endpoints

### 1) Realtime Employee/Branch Data

- `GET /api/employee-events/v1/employees/realtime-data`

Returns:

- `employees` from `emp_cont_view` with fixed filters:
  - `park = 0`
  - `status = 1`
  - `fullname != ''`
  - grouped by `contact_id`
- `branches` from `branch` with fixed filters:
  - `id NOT IN (86)`
  - `park = 0`

Employee columns:

- `id`, `contact_id`, `fullname`, `position_id`, `position`, `bid`

Branch columns:

- `id`, `branch`, `type`

### 2) Active Venue Selectors

- `GET /api/employee-events/v1/venues`

Returns active venues from `venue` using fixed filters:

- `park = 0`
- `status = 0`

Venue columns:

- `id`, `venue`, `display_name`

Response data:

- `venues`
- `total_count`

Sample response:

```json
{
  "success": true,
  "message": "Active venues fetched successfully",
  "data": {
    "venues": [
      {
        "id": 10,
        "venue": "Andheri Center",
        "display_name": "Andheri Center"
      }
    ],
    "total_count": 1
  }
}
```

### 3) Active Batches by Venue

- `POST /api/employee-events/v1/batches/query`

Request:

```json
{
  "venue_ids": [10, 20]
}
```

Rules:

- `venue_ids` is required
- accepts `1..25` unique venue ids after first-seen dedupe
- values must be positive integers
- reads from `batch_employee_time_view`
- active batch filters are fixed:
  - `park = 0`
  - `inactive = 0`
  - `hide = 0`
  - `cont_park = 0`
  - when present in the view schema:
    - `demo_class = 0`
    - `training_assign = 0`

Response data:

- `venue_ids`
- `total_count`
- `batches`

Per batch:

- `id`
- `batch`
- `display_name`
- `venue_id`
- `venue`
- `parent_id`
- `date`
- `start_date`
- `end_date`
- `start_time`
- `end_time`
- `day_code`
- `title`
- `timezone_id`
- `contact_id`
- `code`
- `category`
- `branch`
- `bid`
- `employee_id`
- `associate_fullname`
- `modified_at`
- `parent_batch_name`

Additional mapped fields for UI compatibility with `/calendar/events` batch rows:

- `event_id`
- `batch_id`
- `demo_id`
- `batch_name`
- `summary`
- `location`
- `event_timezone`
- `event_start`
- `event_end`
- `attendees`
- `parent_batch_id`
- `batch_type`
- `batch_status`
- `is_original`
- `is_scheduled`
- `is_recurring`

Sample response:

```json
{
  "success": true,
  "message": "Active batches fetched successfully",
  "data": {
    "venue_ids": [10, 20],
    "total_count": 2,
    "batches": [
      {
        "id": 123,
        "batch": "Offline B87",
        "display_name": "Offline B87",
        "venue_id": 10,
        "venue": "Andheri Center",
        "parent_id": 0,
        "date": "2026-03-10",
        "start_date": "2026-03-10",
        "end_date": "2026-03-10",
        "start_time": "12:00:00",
        "end_time": "13:30:00",
        "day_code": "2,4",
        "title": "Offline B87",
        "timezone_id": "Asia/Kolkata",
        "contact_id": 72313,
        "code": "B87",
        "category": "Offline",
        "branch": "Mumbai",
        "bid": 7,
        "employee_id": 501,
        "associate_fullname": "Trainer One",
        "modified_at": "2026-03-10 09:00:00",
        "parent_batch_name": null,
        "event_id": null,
        "batch_id": 123,
        "demo_id": null,
        "batch_name": "Offline B87",
        "summary": "Offline B87",
        "location": "Andheri Center",
        "event_timezone": "Asia/Kolkata",
        "event_start": "2026-03-10 12:00:00",
        "event_end": "2026-03-10 13:30:00",
        "attendees": "[]",
        "parent_batch_id": null,
        "batch_type": "original",
        "batch_status": "original",
        "is_original": 1,
        "is_scheduled": 0,
        "is_recurring": 1
      }
    ]
  }
}
```

### 4) Batch Kids Present Query

- `POST /api/employee-events/v1/batches/kids-present/query`

Request:

```json
{
  "batch_id": 123
}
```

Rules:

- `batch_id` is required and must be a positive integer
- reads from `invoice_invoiceitem_view`
- effective date window is computed server-side using `EMP_EVENT_TIMEZONE`:
  - `from_date = today - 8 days`
  - `to_date = today + 90 days`
- fixed filters:
  - `start_date <= to_date`
  - `end_date >= from_date`
  - `batch_id = request.batch_id`
  - `park = 0`
  - `renew = 0`
  - `dropout = 0`
  - `freeze = 0`

Response data:

- `batch_id`
- `from_date`
- `to_date`
- `total_count`
- `kids`

Per row:

- `invoice_id`
- `item_id`
- `invoice`
- `code_name`
- `sessions`
- `sessions_used`
- `dob`
- `counsellor_name`
- `balance`
- `dropout`
- `freeze`
- `date`

### 5) Workshift Calendar Batch Query

- `POST /api/employee-events/v1/employees/workshift-calendar/query`

Phase note:

- This is a backend-only Phase 1 API for `/event-calendar`.
- Frontend rendering and Google Calendar integration are out of scope here.

Request:

```json
{
  "employee_ids": [123, 456],
  "from_date": "2026-03-01",
  "to_date": "2026-03-31"
}
```

Rules:

- accepts `1..25` unique employee ids after first-seen dedupe
- range is inclusive and must be `<= 62` days
- uses `emp_cont_view.id` as the public employee id
- uses active row filters `park = 0` and `status = 1`

Response data:

- `timezone`
- `from_date`
- `to_date`
- `range_day_count`
- `employee_count`
- `matched_count`
- `employees`

Per employee:

- `employee_id`
- `employee_name`
- `result_status` = `configured | unconfigured | not_found`
- `warnings`
- `workshift`
- `calendar_days`
- `day_count`

`workshift` fields:

- `workshift_id`
- `workshift_in_time`
- `workshift_out_time`
- `week_off_code`
- `week_off_days`
- `is_configured`
- `configuration_issues`

`calendar_days` fields:

- `date`
- `weekday` (`0=Sunday..6=Saturday`)
- `is_week_off`
- `is_overnight`
- `shift_start`
- `shift_end`
- `workshift_id`

Behavior:

- configured employees return one day row for every date in the requested range
- week-off rows are still returned, with `shift_start=null` and `shift_end=null`
- overnight shifts roll `shift_end` to the next date when `out_time <= in_time`
- missing employees are returned as `result_status="not_found"` inside the batch, not as `404`
- invalid `week_off_code` tokens are ignored for expansion and surfaced in `warnings`

### 6) Leave Calendar Batch Query

- `POST /api/employee-events/v1/employees/leave-calendar/query`

Request:

```json
{
  "employee_ids": [123, 456],
  "from_date": "2026-03-01",
  "to_date": "2026-03-31",
  "statuses": [0, 1],
  "request_types": [1, 3],
  "department_ids": [9]
}
```

Rules:

- `employee_ids` uses `emp_cont_view.id` (same id returned by realtime-data), not `contact_id`
- active employee filter is fixed: `emp_cont_view.status='1'` and `emp_cont_view.park='0'`
- overlap rule is fixed: `leave.start_date <= to_date` and `leave.end_date >= from_date`
- `employee_ids` is deduped first-seen and must have `1..25` unique ids
- date range is inclusive and must be `<= 62` days
- optional arrays are deduped; `[]` means no filter (same as omitted)
- unknown status/request_type rows are kept visible with fallback labels/colors

Response data:

- `timezone`
- `from_date`
- `to_date`
- `range_day_count`
- `employee_count`
- `matched_count` (active employee matches only, independent of leave filters)
- `filters_applied` (`statuses`, `request_types`, `department_ids`)
- `employees`

Per employee:

- `employee_id`
- `employee_name`
- `result_status` = `has_events | no_events | not_found`
- `warnings` (deduped)
- `leave_events`
- `leave_event_count`

`leave_events` fields:

- `leave_request_id`
- `employee_id`
- `employee_name`
- `department_id`
- `start`, `end` (RFC3339 with offset)
- `status`, `status_label`
- `request_type`, `request_type_name`
- `title`, `color`
- `allDay=false`
- `module_id=80`

Status label mapping:

- `0 -> Pending`
- `1 -> Approved`
- `2 -> Rejected`
- unknown -> `Unknown(<code>)` + warning `unknown_status:<code>`

Request type mapping:

- `1 -> Leave, #EF4865`
- `2 -> Work From Home, #96C1CC`
- `3 -> Half Day, #E29082`, then lighten `+60`
- `4 -> Late, #FF5722`
- `6 -> Optional Holiday, #4167B0`
- `7 -> Supplementary, #FFC25C`
- unknown -> `Unknown, #9CA3AF` + warning `unknown_request_type:<code>`

Color rule:

- after base color resolution, if `status == 0` (Pending), darken by `-40`

Datetime normalization:

- datetime values keep their time component and are converted/normalized to `EMP_EVENT_TIMEZONE`
- date-only values default to `09:00:00` (start) and `17:00:00` (end) in `EMP_EVENT_TIMEZONE`
- unusable/null normalized datetime skips that row and adds warning `invalid_leave_datetime:<leave_request_id>`

Result status examples:

- `has_events`: employee exists and has one or more leave rows after filters
- `no_events`: employee exists but no leave rows matched after filters
- `not_found`: employee id is not active/matched

### 7) Unified Calendar Events (Employee + Trainer Batch/Demo)

- `GET /api/employee-events/v1/calendar/events`
- Breaking change: this endpoint now returns a unified schema (`source`, `source_event_id`, `title`, `start`, `end`, `is_read_only`, `raw`) instead of trainer-only rows.

Query params:

- `contact_id=<int>` (required, employee/trainer contact id)
- `from_date=YYYY-MM-DD` (optional)
- `to_date=YYYY-MM-DD` (optional)

Rules:

- `contact_id` must be a positive integer
- when provided, `from_date` and `to_date` must be in `YYYY-MM-DD` format
- when both are provided, `from_date <= to_date`
- effective range is bounded to 90 days:
  - if both dates are missing: `today -> today+90d`
  - if only `from_date` is set: `to_date = from_date+90d`
  - if only `to_date` is set: `from_date = to_date-90d`
- merges two sources for the same `contact_id`:
  - employee events from `employee_schedule_events` (`park != 1`, all statuses)
  - trainer batch events from `batch_employee_time_view`
- trainer rows use active filters: `park=0`, `inactive=0`, `hide=0`, `cont_park=0`
- parent trainer rows (`parent_id==0`) expand by `day_code` weekdays (`0=Sun..6=Sat`)
- child trainer rows (`parent_id!=0`) emit a single one-off occurrence
- events with invalid/unparseable datetime values are skipped
- final merged list is sorted by `start`, then by `source_event_id`

Response data:

- `events`
- `total_count`

Each event includes:

- `source` (`employee_event | trainer_batch`)
- `source_event_id` (`employee_<id>` or `trainer_<batch_id>_<YYYYMMDD>`)
- `title`
- `start` (`YYYY-MM-DD HH:mm:ss`)
- `end` (`YYYY-MM-DD HH:mm:ss`)
- `is_read_only`
- `raw` (full source-specific payload)

`raw` for `trainer_batch` includes normalized fields such as:

- `batch_type` (`original | scheduled | prescheduled`)
- `is_original`
- `is_scheduled`
- `is_recurring`
- `batch_status`

Sample response:

```json
{
  "success": true,
  "message": "Calendar events fetched successfully",
  "data": {
    "events": [
      {
        "source": "employee_event",
        "source_event_id": "employee_55",
        "title": "Meeting",
        "start": "2026-03-10 10:00:00",
        "end": "2026-03-10 11:00:00",
        "is_read_only": false,
        "raw": {
          "id": 55,
          "contact_id": 72313,
          "date": "2026-03-10",
          "start_time": "10:00:00",
          "end_time": "11:00:00"
        }
      },
      {
        "source": "trainer_batch",
        "source_event_id": "trainer_123_20260310",
        "title": "Offline B87",
        "start": "2026-03-10 12:00:00",
        "end": "2026-03-10 13:30:00",
        "is_read_only": true,
        "raw": {
          "id": 123,
          "batch_name": "Offline B87",
          "parent_batch_id": null,
          "parent_batch_name": null,
          "event_timezone": "Asia/Kolkata",
          "event_start": "2026-03-10 12:00:00",
          "event_end": "2026-03-10 13:30:00",
          "attendees": "[]",
          "batch_type": "original",
          "is_original": 1,
          "is_scheduled": 0,
          "is_recurring": 1,
          "batch_status": "original"
        }
      }
    ],
    "total_count": 2
  }
}
```

### 8) List Events

- `GET /api/employee-events/v1/events`

Query params (all optional):

- `from_date=YYYY-MM-DD`
- `to_date=YYYY-MM-DD`
- `contact_id=<int>`
- `status=<int>`
- `park=<int>`
- `include_parked=true|false` (default `true`)

Response data:

- `events`: array of event rows from `employee_schedule_events`
- `count`: number of returned rows

Each event includes:

- local event fields
- `allowance_items` from `employee_event_allowance`
- `contact` object from `contact` table
- `sync` object from `employee_event_google_link` (if sync enabled)

### 9) Check Conflict

- `POST /api/employee-events/v1/events/check-conflict`

Request:

```json
{
  "date": "2026-03-15",
  "start_time": "10:00:00",
  "end_time": "11:00:00",
  "contact_id": 123,
  "exclude_event_id": 456
}
```

Response data:

- `conflict` boolean
- `conflict_event_ids` list

### 10) Create Event

- `POST /api/employee-events/v1/events`

Request:

```json
{
  "category": "Meeting",
  "contact_id": 123,
  "branch": "Mumbai",
  "description": "Initial meeting with candidate",
  "type": "Interview",
  "lease_type": "N/A",
  "amount": 1500,
  "deduction_amount": 0,
  "date": "2026-03-15",
  "start_time": "10:00:00",
  "end_time": "11:00:00",
  "allowance": 1,
  "allowance_items": [
    {"name": "Travel", "amount": 400}
  ]
}
```

Behavior:

- Inserts into `employee_schedule_events`.
- Inserts allowance rows when `allowance=1`.
- Creates/updates sync mapping row with `pending_approval`.
- Does not call Google here.

### 11) Update Event

- `PUT /api/employee-events/v1/events/{event_id}`

Request body shape is same as create.

Behavior:

- Updates local event and allowance rows atomically.
- If event is approved and not parked, syncs update/create to Google.
- On sync failure, local update remains and mapping row stores sync failure.

### 12) Park / Unpark Event

- `PATCH /api/employee-events/v1/events/{event_id}/park`

Request:

```json
{
  "park_value": 1
}
```

Behavior:

- Updates local park value.
- If `park_value=1` and linked Google event exists, attempts Google delete.
- On delete failure, keeps local park and records sync error.

### 13) Approve Event

- `POST /api/employee-events/v1/events/{event_id}/approve`

Request:

```json
{
  "status": 1
}
```

Behavior:

- If approving (`status=1`), creates Google event if not already linked.
- Idempotent if already linked active.
- If Google create fails, local status is not switched to approved.
- If changing from approved to a non-approved status (for example reject `status=2`),
  linked Google event is deleted automatically.

## Google Payload Mapping

Summary:

- `"{branch} | {type} | {category} | {contact_fname} {contact_lname}"`

Description includes:

- category, type, lease_type, amount, deduction_amount
- allowance flag and allowance item summary
- contact name/mobile/email

Location:

- `branch`

Attendees:

- From `contact.email` if present.

DateTime:

- Built from local `date + start_time/end_time` in `EMP_EVENT_TIMEZONE`.

## Config

Required/used keys:

- `EMP_EVENT_APPROVED_STATUS` (default `1`)
- `EMP_EVENT_PARKED_VALUE` (default `1`)
- `EMP_EVENT_TIMEZONE` (default `Asia/Kolkata`)
- `EMP_EVENT_ENABLE_GOOGLE_SYNC` (default `true`)
- `GOOGLE_CALENDAR_ID`
- `GOOGLE_DRIVE_TOKEN_ID`
- `GOOGLE_OAUTH_TOKEN_URL`

## Sync Link Table

Runtime-created table:

- `employee_event_google_link`

Stores:

- local event id
- google event id
- sync status
- last sync error code/message
