# Notification System

This backend owns the durable notification event log, SSE delivery, per-user
notification preferences, and the v1 follow-up reminder scheduler.

## Backend Files

- Routes: `app/modules/notifications/router.py`
- Schemas: `app/modules/notifications/schemas/models.py`
- SSE publisher: `app/modules/notifications/services/publisher.py`
- Persistence and rules: `app/modules/notifications/services/repository.py`
- Follow-up scheduler: `app/modules/notifications/services/followup_reminders.py`
- Migrations:
  - `alembic/versions/20260526_011_notifications.py`
  - `alembic/versions/20260527_012_notification_rules_followup.py`

## API

All endpoints require an authenticated bearer token.

- `GET /api/notifications/v1/stream`
  - Server-sent events stream.
  - Emits events targeted to the caller plus broadcast events where `user_id`
    is null.
- `GET /api/notifications/v1/recent`
  - Returns recent visible notifications for the caller.
  - Uses the same visibility rule as `/stream`.
- `PATCH /api/notifications/v1/{event_id}/read`
- `PATCH /api/notifications/v1/read-all`
- `DELETE /api/notifications/v1/{event_id}`
- `DELETE /api/notifications/v1/clear-all`
- `GET /api/notifications/v1/preferences`
- `PATCH /api/notifications/v1/preferences`
- `GET /api/notifications/v1/rules`
- `PATCH /api/notifications/v1/rules`
- `POST /api/notifications/v1/debug`

## Follow-Up Reminder Rule

Default merged rule:

```json
{
  "source": "followups",
  "event_type": "FOLLOWUP_REMINDER_DUE",
  "enabled": true,
  "reminder_offsets_minutes": [5],
  "recipient_scope": "assigned_to_me"
}
```

`recipient_scope` accepts:

- `assigned_to_me`: only the counsellor assigned on `followup.employee_id`.
- `managed_team`: currently treated as assigned-only until team hierarchy is
  wired.
- `branch`: users in the same `user_bid.bid`, plus the assigned user.

## Scheduler Behavior

The scheduler starts during FastAPI lifespan after database initialization.
It runs every `FOLLOWUP_REMINDER_SCAN_INTERVAL_SECONDS` seconds, defaulting to
60 seconds.

For each scan it:

1. Uses `NOTIFICATION_WORKSPACE_TIMEZONE` to interpret `followup.reminder`
   timestamps. The default is `Asia/Kolkata`.
2. Finds active follow-ups that are not parked, not unfollowed, have an
   assigned employee, have a non-empty reminder, and have an open status.
3. Resolves `followup.employee_id` to an authenticated platform `user_id` via
   `auth_employee_user_map`, with a fallback through `employee.contact_id` and
   `user.contact_id` for the current `client_db`.
4. Loads the assigned user's delivery rule.
5. Publishes a targeted `FOLLOWUP_REMINDER_DUE` notification when the reminder
   enters a configured offset window.
6. Records a row in `notification_dispatch_ledger` keyed by `followup_id`,
   `reminder_at`, `offset_minutes`, and `recipient_user_id` so repeated scans
   and restarts do not duplicate delivery.

Published reminder events use:

- `source: "followups"`
- `event_type: "FOLLOWUP_REMINDER_DUE"`
- `severity: "warning"`
- `user_id: <recipient_user_id>`
- `group_key: "followup:{followup_id}"`
- stable `dedupe_key`

## Tables

`notification_event`

- Durable event log used by `/recent` and read/clear state.

`notification_user_state`

- Per-user read and cleared timestamps.

`notification_user_preference`

- Toast, desktop, silent mode, and severity preferences.

`notification_delivery_rule`

- Per-user overrides keyed by `user_id`, `source`, and `event_type`.

`notification_dispatch_ledger`

- Idempotency ledger for scheduled follow-up reminders.

## Settings

```env
NOTIFICATION_WORKSPACE_TIMEZONE=Asia/Kolkata
FOLLOWUP_REMINDERS_ENABLED=true
FOLLOWUP_REMINDER_SCAN_INTERVAL_SECONDS=60
```

## Desktop Notification Boundary

The backend publishes SSE events only. Native desktop popups are produced by
the browser tab using the Web Notification API. A desktop popup is expected
only when the user is signed in, MarkX is open in a browser tab, the SSE runtime
is connected, browser notification permission has been granted, and the user's
notification preferences allow desktop delivery. Closed-browser Web Push is out
of v1 scope.
