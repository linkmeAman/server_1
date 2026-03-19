# Demo Events API — Frontend Integration Guide

## Endpoint

```
POST /api/employee-events/v1/demo/query
```

**Auth:** Bearer token required (`Authorization: Bearer <token>`)

---

## Request Body

```json
{
  "employee_ids": [1, 2, 3],
  "from_date": "2026-03-01",
  "to_date": "2026-03-31",
  "statuses": [1, 2],
  "types": [1],
  "venue_ids": [10, 20],
  "batch_ids": [5]
}
```

### Required Fields

| Field          | Type       | Description                                      |
|----------------|------------|--------------------------------------------------|
| `employee_ids` | `int[]`    | Contact IDs to fetch demos for. A demo is matched if **any** of `host_contact_id`, `sc_contact_id`, `so_contact_id`, or `owner_contact_id` in `demo_link_view` equals a requested ID. Min 1, max 25. Positive integers only. Duplicates are auto-removed. |
| `from_date`    | `string`   | Start date (inclusive), `YYYY-MM-DD` format. Filters on `start_date` column. |
| `to_date`      | `string`   | End date (inclusive), `YYYY-MM-DD` format. Filters on `start_date` column. Max 62-day range from `from_date`. |

### Optional Filters

| Field            | Type       | Default | Description                                    |
|------------------|------------|---------|------------------------------------------------|
| `statuses`       | `int[]`    | `null`  | Filter by `demo_status`. Values must be >= 0.   |
| `types`          | `int[]`    | `null`  | Filter by `demo_type`. Values must be >= 0.     |
| `venue_ids`      | `int[]`    | `null`  | Filter by `venue_id`. Values must be >= 1.       |
| `batch_ids`      | `int[]`    | `null`  | Filter by `batch_id`. Values must be >= 1.       |

When an optional filter is `null` or omitted, no filtering is applied for that field.

### Static Filters (always applied)

| Condition          | Description                                      |
|--------------------|--------------------------------------------------|
| `park = 0`         | Only non-parked demos are returned.              |
| `demoApproval = 1` | Only approved demos are returned.                |
| `start_date >= from_date AND start_date <= to_date` | Date range filters on the `start_date` column only. |

---

## Success Response (200)

```json
{
  "status": "success",
  "message": "Demo events fetched successfully",
  "data": {
    "from_date": "2026-03-01",
    "to_date": "2026-03-31",
    "range_day_count": 31,
    "employee_count": 3,
    "matched_count": 2,
    "total_demos": 5,
    "employees": [
      {
        "employee_id": 1,
        "demo_count": 3,
        "demos": [
          {
            "id": 10,
            "type": 1,
            "status": 1,
            "demoApproval": 1,
            "otp": "1234",
            "hashed_otp": "abc123...",
            "name": "Demo Session A",
            "date": "2026-03-10",
            "start_date": "2026-03-10",
            "end_date": "2026-03-10",
            "include_time": 1,
            "start_time": "10:00",
            "end_time": "11:00",
            "existing_batch": 0,
            "infant": 0,
            "ad_hoc": 0,
            "batch_id": 5,
            "venue_id": 10,
            "storytelling_location": "Room A",
            "workshop_location": "Hall B",
            "currency": "INR",
            "amount": 500,
            "upi_id": "user@upi",
            "host_employee_id": 1,
            "sc_employee_id": 2,
            "so_employee_id": 3,
            "comment": "First demo",
            "bid": 100,
            "owner_id": 50,
            "mobile_country_code": "+91",
            "optional_mobile_country_code": null,
            "mobile_number": "9876543210",
            "optional_mobile_number": null,
            "park": 0,
            "stop_response": 0,
            "response_limit": 50,
            "ads": 0,
            "ads_comment": null,
            "all_fields_filled": 1,
            "response_limit_flag": 0,
            "zone_time_str": "Asia/Kolkata",
            "zone_time_details": null,
            "doubletick_status": 0,
            "mail_dropdown_template": null,
            "send_mail_update_status": 0,
            "created_at": "2026-03-09 12:00:00",
            "created_by": "admin",
            "modified_at": "2026-03-09 14:00:00",
            "modified_by": "admin",
            "owner_name": "Owner A",
            "owner_mobile_number": "9999999999",
            "owner_contact_id": 100,
            "owner_park": 0
          }
        ]
      },
      {
        "employee_id": 2,
        "demo_count": 2,
        "demos": [...]
      },
      {
        "employee_id": 3,
        "demo_count": 0,
        "demos": []
      }
    ]
  }
}
```

### Response Field Reference

#### Top-level `data`

| Field             | Type     | Description                                               |
|-------------------|----------|-----------------------------------------------------------|
| `from_date`       | `string` | Echoed start date.                                        |
| `to_date`         | `string` | Echoed end date.                                          |
| `range_day_count` | `int`    | Number of days in the range (inclusive).                   |
| `employee_count`  | `int`    | Number of unique employee IDs requested.                  |
| `matched_count`   | `int`    | Number of employees that had at least one demo.           |
| `total_demos`     | `int`    | Total demo records returned across all employees.         |
| `employees`       | `array`  | One entry per requested employee (preserves input order). |

#### Each `employees[n]`

| Field         | Type     | Description                                      |
|---------------|----------|--------------------------------------------------|
| `employee_id` | `int`    | The requested contact ID.                        |
| `demo_count`  | `int`    | Number of demos for this employee.               |
| `demos`       | `array`  | Demo records for this employee (sorted by `start_date`, then `id`). |

#### Each `demos[n]` — from `demo_link_view`

All columns from the `demo_link_view` are returned directly. Key fields:

| Field                          | Description                             |
|--------------------------------|-----------------------------------------|
| `id`                           | Demo record ID.                         |
| `demo_type`                    | Demo type code.                         |
| `demo_status`                  | Demo status code.                       |
| `demoApproval`                 | Approval status.                        |
| `otp`                          | OTP value.                              |
| `hashed_otp`                   | Hashed OTP.                             |
| `demo_link`                    | Demo link.                              |
| `demo_venue_link`              | Demo venue link.                        |
| `name`                         | Demo name/title.                        |
| `date`                         | Demo date.                              |
| `start_date`                   | Start date.                             |
| `end_date`                     | End date.                               |
| `start_time`                   | Start time.                             |
| `end_time`                     | End time.                               |
| `include_time`                 | Whether time is included.               |
| `existing_batch`               | Existing batch flag.                    |
| `batch_id`                     | Associated batch ID.                    |
| `venue_id`                     | Venue ID.                               |
| `venue`                        | Venue name.                             |
| `venue_display_name`           | Venue display name.                     |
| `storytelling_location`        | Storytelling location.                  |
| `workshop_location`            | Workshop location.                      |
| `currency`                     | Currency code.                          |
| `amount`                       | Amount value.                           |
| `upi_id`                       | UPI ID.                                 |
| `host_employee_id`             | Host employee ID.                       |
| `host_contact_id`              | Host contact ID.                        |
| `host_name`                    | Host name.                              |
| `sc_employee_id`               | SC employee ID.                         |
| `sc_contact_id`                | SC contact ID.                          |
| `sc_host_name`                 | SC host name.                           |
| `sc_host_fullname`             | SC host full name.                      |
| `sc_host_email`                | SC host email.                          |
| `so_employee_id`               | SO employee ID.                         |
| `so_contact_id`                | SO contact ID.                          |
| `so_host_name`                 | SO host name.                           |
| `so_host_fullname`             | SO host full name.                      |
| `so_host_email`                | SO host email.                          |
| `owner_id`                     | Owner ID.                               |
| `owner_name`                   | Owner display name.                     |
| `owner_contact_id`             | Owner contact ID.                       |
| `owner_email`                  | Owner email.                            |
| `comment`                      | Comment text.                           |
| `bid`                          | BID value.                              |
| `branch`                       | Branch.                                 |
| `type`                         | Type code.                              |
| `mobile_country_code`          | Mobile country code.                    |
| `mobile_number`                | Mobile number.                          |
| `optional_mobile_country_code` | Optional mobile country code.           |
| `optional_mobile_number`       | Optional mobile number.                 |
| `park`                         | Park flag.                              |
| `stop_response`                | Stop response flag.                     |
| `response_count`               | Response count.                         |
| `response_limit_flag`          | Response limit flag.                    |
| `response_limit`               | Response limit.                         |
| `seats_left`                   | Seats left.                             |
| `ads`                          | Ads flag.                               |
| `ads_comment`                  | Ads comment.                            |
| `all_fields_filled`            | All fields filled flag.                 |
| `demo_ad_status`               | Demo ad status.                         |
| `demo_date_string`             | Demo date string.                       |
| `day_code`                     | Day code.                               |
| `demo_day`                     | Demo day.                               |
| `day`                          | Day.                                    |
| `zone_time_str`                | Timezone string.                        |
| `zone_time_details`            | Timezone details.                       |
| `created_at`                   | Creation timestamp.                     |
| `created_by`                   | Created by.                             |
| `modified_at`                  | Last modified timestamp.                |
| `modified_by`                  | Modified by.                            |
| `modified_by_fname`            | Modified by first name.                 |
| `hybrid`                       | Hybrid flag.                            |

---

## Error Responses

### 401 Unauthorized — Missing/invalid token

```json
{
  "status": "error",
  "error": "EMP_EVENT_UNAUTHORIZED",
  "message": "Authorization header is missing or invalid"
}
```

### 400 Bad Request — Validation errors

```json
{
  "status": "error",
  "error": "EMP_EVENT_INVALID_DEMO_QUERY",
  "message": "Invalid demo events query",
  "data": {
    "request_id": "uuid",
    "details": { "errors": [...] }
  }
}
```

**Common 400 reasons:**
- `employee_ids` is empty, has non-integers, values <= 0, or has > 25 entries
- `from_date` / `to_date` not in `YYYY-MM-DD` format
- `from_date` is after `to_date`
- Date range exceeds 62 days
- Filter array values are non-integer or below minimum allowed value
- Request body is not valid JSON or not a JSON object

### 500 Internal Server Error

```json
{
  "status": "error",
  "error": "EMP_EVENT_DEMO_QUERY_FAILED",
  "message": "Unexpected error fetching demo events: ..."
}
```

---

## cURL Example

```bash
curl -X POST "https://your-domain/api/employee-events/v1/demo/query" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "employee_ids": [1, 2],
    "from_date": "2026-03-01",
    "to_date": "2026-03-31"
  }'
```

### With all optional filters

```bash
curl -X POST "https://your-domain/api/employee-events/v1/demo/query" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "employee_ids": [1, 2],
    "from_date": "2026-03-01",
    "to_date": "2026-03-31",
    "statuses": [1],
    "types": [1, 2],
    "venue_ids": [10],
    "batch_ids": [5]
  }'
```

---

## Data Source

- **View:** `demo_link_view` — filtered by contact IDs (matching any of `host_contact_id`, `sc_contact_id`, `so_contact_id`, `owner_contact_id`) and `start_date` within date range
- **Static conditions:** `park = 0 AND demoApproval = 1`
- **Sort order:** `start_date ASC, id ASC`
