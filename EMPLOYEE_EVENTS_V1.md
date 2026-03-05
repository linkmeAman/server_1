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

### 2) List Events

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

### 3) Check Conflict

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

### 4) Create Event

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

### 5) Update Event

- `PUT /api/employee-events/v1/events/{event_id}`

Request body shape is same as create.

Behavior:

- Updates local event and allowance rows atomically.
- If event is approved and not parked, syncs update/create to Google.
- On sync failure, local update remains and mapping row stores sync failure.

### 6) Park / Unpark Event

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

### 7) Approve Event

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
