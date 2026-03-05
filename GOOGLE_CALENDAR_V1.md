# Google Calendar V1 API

This module provides isolated Google Calendar event operations in FastAPI.

## Base Path

- `/api/google-calendar/v1`

## Authentication

All endpoints require this app header:

1. `Authorization: Bearer <app_access_token>`

Error codes:

- `GCAL_UNAUTHORIZED` (401)
- `GCAL_TOKEN_UNAVAILABLE` (503/404)
- `GCAL_TOKEN_REFRESH_FAILED` (500/502)

Google token source:

- Access token is fetched from central DB table `google_drive_token`.
- Row is selected by `GOOGLE_DRIVE_TOKEN_ID` (default `2`).
- If token is expired/near-expiry, backend refreshes it using stored `refresh_token`, `client_id`, and `client_secret`, then updates the same row.

## Endpoints

### Create Event

- `POST /api/google-calendar/v1/events`

Request body:

```json
{
  "actor_name": "Admin User",
  "actor_email": "admin@example.com",
  "event": {
    "summary": "Demo",
    "description": "Created from FastAPI",
    "start": {
      "dateTime": "2026-03-10T10:00:00+05:30",
      "timeZone": "Asia/Kolkata"
    },
    "end": {
      "dateTime": "2026-03-10T11:00:00+05:30",
      "timeZone": "Asia/Kolkata"
    },
    "attendees": [
      {"email": "person1@example.com"}
    ]
  }
}
```

Behavior:

- Calls Google Calendar create event API with `sendUpdates=none`.
- On success (`200` or `201`), inserts success log row into `calendar_event_logs`.
- On Google error, inserts error log row and returns `GCAL_UPSTREAM_ERROR`.
- Uses env calendar ID `GOOGLE_CALENDAR_ID`.

### Update Event

- `PUT /api/google-calendar/v1/events/{event_id}`

Request body:

```json
{
  "actor_name": "Admin User",
  "actor_email": "admin@example.com",
  "event": {
    "summary": "Updated title"
  },
  "log_row_id": 123
}
```

Behavior:

- Calls Google Calendar update event API with `sendUpdates=none`.
- Requires Google status `200`.
- Updates `calendar_event_logs` by `log_row_id` when provided, else latest row by `event_id`.
- Uses env calendar ID `GOOGLE_CALENDAR_ID`.

### Delete Event

- `DELETE /api/google-calendar/v1/events/{event_id}?delete_mode=full|next_instance`
- `delete_mode` default: `full`

Behavior:

- `full`: deletes the event directly.
- `next_instance`: fetches instances and deletes the next upcoming instance based on `GOOGLE_CALENDAR_COMPARE_TIMEZONE`.
- Treats `204` and `410` as successful delete semantics.
- Updates `calendar_event_logs.park = '1'` for the original `event_id`.
- Uses env calendar ID `GOOGLE_CALENDAR_ID`.

Error codes:

- `GCAL_UPSTREAM_ERROR`
- `GCAL_INSTANCE_NOT_FOUND`
- `GCAL_LOG_PERSISTENCE_FAILED`
- `GCAL_CONFIG_ERROR`

## Data Handling

- Stored `event_start` and `event_end` are normalized to UTC ISO format when parsable.
- Event timezone comes from Google payload start/end timezone or `GOOGLE_CALENDAR_COMPARE_TIMEZONE` fallback.
- Attendees are stored as JSON string in log table.
- If datetime parsing fails, the raw value is stored to avoid data loss.
