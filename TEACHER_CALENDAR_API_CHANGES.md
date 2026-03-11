# Teacher Calendar API - Changes & New Features

**Date**: March 10, 2026  
**Status**: ✅ Ready for Integration

---

## Summary

Two changes to Employee Events V1 API:
1. **Fixed**: Existing calendar endpoint now respects batch timezone
2. **New**: Teacher availability endpoint for scheduling

---

## 1. Existing Endpoint - Timezone Fix

### `GET /api/employee-events/v1/calendar/events`

**What Changed**: Batch occurrences now display in their configured timezone instead of server timezone.

**Breaking Changes**: ❌ None - response format unchanged

**Request** (unchanged):
```
GET /api/employee-events/v1/calendar/events?contact_id=123&from_date=2026-03-01&to_date=2026-03-31
Authorization: Bearer <app_access_token>
```

**Response** (same structure, correct times):
```json
{
  "success": true,
  "data": {
    "events": [
      {
        "source": "trainer_batch",
        "source_event_id": "trainer_100_20260310",
        "title": "Offline B87",
        "start": "2026-03-10 16:00:00",
        "end": "2026-03-10 17:00:00",
        "is_read_only": true,
        "raw": {
          "batch_name": "Offline B87",
          "event_timezone": "Asia/Kolkata",
          "event_start": "2026-03-10 16:00:00",
          "event_end": "2026-03-10 17:00:00",
          ...
        }
      }
    ],
    "total_count": 1
  }
}
```

**Frontend Action**: 
- ✅ No code changes required
- ✅ Verify batch times now display correctly in UI
- ✅ Check multi-timezone scenarios if applicable

---

## 2. New Endpoint - Teacher Availability

### `GET /api/employee-events/v1/teacher/{contact_id}/availability`

**Purpose**: Get teacher's daily schedule with busy blocks and free time slots.

**Use Cases**:
- Show availability before creating new events
- Highlight free/busy periods in calendar view
- Validate scheduling conflicts

### Request

```
GET /api/employee-events/v1/teacher/123/availability?date=2026-03-10
Authorization: Bearer <app_access_token>
```

**Parameters**:
- `contact_id` (path): Teacher's contact ID (same as calendar endpoint)
- `date` (query): Date in `YYYY-MM-DD` format

### Response

```json
{
  "success": true,
  "message": "Teacher daily availability retrieved successfully",
  "data": {
    "teacher_contact_id": 123,
    "teacher_employee_id": 456,
    "teacher_name": "John Trainer",
    "date": "2026-03-10",
    "shift_start": "09:00:00",
    "shift_end": "18:00:00",
    "is_week_off": false,
    "busy_blocks": [
      {
        "start": "10:00:00",
        "end": "11:00:00",
        "source": "trainer_batch",
        "event_id": "trainer_100_20260310",
        "title": "Morning Batch A"
      },
      {
        "start": "14:00:00",
        "end": "16:00:00",
        "source": "leave",
        "event_id": "leave_456",
        "title": "Approved Leave"
      },
      {
        "start": "16:00:00",
        "end": "17:00:00",
        "source": "employee_event",
        "event_id": "employee_789",
        "title": "Meeting"
      }
    ],
    "free_slots": [
      {
        "start": "09:00:00",
        "end": "10:00:00",
        "duration_minutes": 60
      },
      {
        "start": "11:00:00",
        "end": "14:00:00",
        "duration_minutes": 180
      },
      {
        "start": "17:00:00",
        "end": "18:00:00",
        "duration_minutes": 60
      }
    ],
    "total_busy_minutes": 240,
    "total_free_minutes": 300,
    "warnings": []
  }
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `teacher_contact_id` | int | Teacher's contact ID |
| `teacher_employee_id` | int | Teacher's employee ID |
| `teacher_name` | string | Teacher's full name |
| `date` | string | Queried date (YYYY-MM-DD) |
| `shift_start` | string\|null | Shift start time (HH:MM:SS) or null if week-off |
| `shift_end` | string\|null | Shift end time (HH:MM:SS) or null if week-off |
| `is_week_off` | boolean | True if this date is teacher's weekly off day |
| `busy_blocks` | array | List of busy time blocks |
| `free_slots` | array | List of available time slots within shift |
| `total_busy_minutes` | int | Total busy time in minutes |
| `total_free_minutes` | int | Total free time in minutes |
| `warnings` | array | Configuration warnings (e.g., "workshift_unconfigured") |

### Busy Block Object

```json
{
  "start": "10:00:00",
  "end": "11:00:00",
  "source": "trainer_batch",
  "event_id": "trainer_100_20260310",
  "title": "Morning Batch A"
}
```

**Source Types**:
- `trainer_batch` - Recurring batch schedule
- `employee_event` - One-time event
- `leave` - Approved leave request

### Free Slot Object

```json
{
  "start": "09:00:00",
  "end": "10:00:00",
  "duration_minutes": 60
}
```

### Special Cases

**Week-off Day**:
```json
{
  "date": "2026-03-09",
  "is_week_off": true,
  "shift_start": null,
  "shift_end": null,
  "busy_blocks": [],
  "free_slots": [],
  "total_busy_minutes": 0,
  "total_free_minutes": 0
}
```

**Unconfigured Workshift**:
```json
{
  "date": "2026-03-10",
  "is_week_off": false,
  "shift_start": null,
  "shift_end": null,
  "warnings": ["workshift_unconfigured"],
  ...
}
```

### Error Responses

**Teacher Not Found** (404):
```json
{
  "success": false,
  "error": "EMP_EVENT_TEACHER_NOT_FOUND",
  "message": "Teacher not found or inactive"
}
```

**Invalid Date** (400):
```json
{
  "success": false,
  "error": "EMP_EVENT_INVALID_CALENDAR_QUERY",
  "message": "Invalid date format"
}
```

**Unauthorized** (401):
```json
{
  "success": false,
  "error": "EMP_EVENT_UNAUTHORIZED",
  "message": "Missing or invalid authorization"
}
```

---

## Frontend Integration Examples

### Example 1: Show Availability Summary

```typescript
async function fetchTeacherAvailability(contactId: number, date: string) {
  const response = await fetch(
    `/api/employee-events/v1/teacher/${contactId}/availability?date=${date}`,
    {
      headers: {
        'Authorization': `Bearer ${accessToken}`,
      },
    }
  );
  
  const result = await response.json();
  
  if (result.success) {
    const { data } = result;
    
    if (data.is_week_off) {
      return { status: 'week-off', message: 'Teacher is off today' };
    }
    
    return {
      status: 'available',
      freeSlots: data.free_slots,
      busyBlocks: data.busy_blocks,
      summary: `${data.total_free_minutes} min free, ${data.total_busy_minutes} min busy`,
    };
  }
}
```

### Example 2: Render Free Slots in UI

```typescript
function renderFreeSlots(slots: FreeSlot[]) {
  return slots.map(slot => (
    <div className="free-slot">
      <span>{slot.start} - {slot.end}</span>
      <span className="duration">{slot.duration_minutes} min</span>
    </div>
  ));
}
```

### Example 3: Validate Before Creating Event

```typescript
async function validateEventTime(contactId: number, date: string, startTime: string, endTime: string) {
  const availability = await fetchTeacherAvailability(contactId, date);
  
  if (availability.status === 'week-off') {
    return { valid: false, reason: 'Teacher is off on this day' };
  }
  
  // Check if proposed time overlaps any busy block
  const hasConflict = availability.busyBlocks.some(block => {
    return (startTime < block.end && endTime > block.start);
  });
  
  if (hasConflict) {
    return { valid: false, reason: 'Time slot conflicts with existing event' };
  }
  
  return { valid: true };
}
```

### Example 4: Color-code Calendar Cells

```typescript
function getDateCellClass(availability: AvailabilityData) {
  if (availability.is_week_off) return 'week-off';
  
  const ratio = availability.total_busy_minutes / 
                (availability.total_busy_minutes + availability.total_free_minutes);
  
  if (ratio > 0.8) return 'fully-booked';
  if (ratio > 0.5) return 'mostly-busy';
  if (ratio > 0.2) return 'partly-busy';
  return 'mostly-free';
}
```

---

## UI/UX Recommendations

### Calendar Month View
- Show availability indicator per day (e.g., color dots or badges)
- Display "3 free slots" count on hover
- Gray out week-off days

### Day/Week View
- Render busy blocks as colored bars (blue=batch, green=event, orange=leave)
- Highlight free slots with lighter background
- Show shift boundaries as reference lines

### Event Creation Modal
- Pre-validate selected time against availability
- Show warning if slot conflicts with existing event
- Suggest nearby free slots if conflict detected

---

## Testing Checklist

- [ ] Verify existing calendar shows correct batch times
- [ ] Test availability endpoint for regular workday
- [ ] Test availability endpoint for week-off day
- [ ] Test with teacher having multiple batches
- [ ] Test with teacher having approved leave
- [ ] Test with invalid contact_id (should return 404)
- [ ] Test with invalid date format (should return 400)
- [ ] Verify authorization required (401 without token)

---

## Technical Notes

### Time Format
All times are in `HH:MM:SS` format (24-hour):
- `09:00:00` = 9 AM
- `14:30:00` = 2:30 PM
- `18:00:00` = 6 PM

### Timezone Handling
- **Input**: Date is in `YYYY-MM-DD` format (no timezone info needed)
- **Output**: Times are in teacher's workshift timezone (stored in backend config)
- **Display**: Show times as-is, they're already in correct local timezone

### Performance
- Endpoint optimized for single-day queries
- Typical response time: <500ms
- For calendar month view, consider:
  - Cache availability for visible dates
  - Batch requests if API supports it (future enhancement)
  - Show loading skeleton while fetching

### Data Freshness
- Busy blocks include real-time batches, events, and leave
- Refresh availability when:
  - User creates/updates/deletes event
  - Date selection changes
  - Teacher selection changes

---

## Questions?

Contact backend team or refer to:
- Full API docs: `/var/www/py-workspace/server_1/EMPLOYEE_EVENTS_V1.md`
- Implementation plan: `/memories/session/plan.md`
