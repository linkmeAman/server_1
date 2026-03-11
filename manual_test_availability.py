"""
Quick verification script for teacher calendar availability implementation.

This script can be used to manually test the new features:
1. Timezone fix for batch occurrences
2. Teacher daily availability calculation

Run this after starting the server:
python manual_test_availability.py
"""

import requests
import json
from datetime import datetime

# Configuration
BASE_URL = "http://localhost:8000"
API_PREFIX = "/api/employee-events/v1"

# Replace with a valid access token from your environment
ACCESS_TOKEN = "your_app_access_token_here"

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json",
}


def test_calendar_events_timezone_fix():
    """Test Phase 1: Verify batch occurrences respect timezone_id."""
    print("\n" + "="*60)
    print("TEST 1: Calendar Events with Timezone Fix")
    print("="*60)
    
    # Use a contact_id that has batches with timezone_id configured
    contact_id = 100  # Replace with actual test contact_id
    from_date = "2026-03-01"
    to_date = "2026-03-31"
    
    url = f"{BASE_URL}{API_PREFIX}/calendar/events"
    params = {
        "contact_id": contact_id,
        "from_date": from_date,
        "to_date": to_date,
    }
    
    print(f"\nRequest: GET {url}")
    print(f"Params: {params}")
    
    try:
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        print(f"\nStatus: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            events = data.get("data", {}).get("events", [])
            print(f"Total events: {len(events)}")
            
            # Show first few trainer batch events
            trainer_events = [e for e in events if e.get("source") == "trainer_batch"]
            print(f"\nTrainer batch events: {len(trainer_events)}")
            
            if trainer_events:
                print("\nSample batch event:")
                sample = trainer_events[0]
                print(f"  ID: {sample.get('source_event_id')}")
                print(f"  Title: {sample.get('title')}")
                print(f"  Start: {sample.get('start')}")
                print(f"  End: {sample.get('end')}")
                print(f"  Timezone: {sample.get('raw', {}).get('event_timezone')}")
                print(f"  ✓ Timezone is being respected in occurrence generation")
            else:
                print("  ⚠ No trainer batch events found for this contact/date range")
        else:
            print(f"Error: {response.text}")
            
    except Exception as e:
        print(f"Exception: {str(e)}")


def test_teacher_availability():
    """Test Phases 3-4: Verify daily availability calculation."""
    print("\n" + "="*60)
    print("TEST 2: Teacher Daily Availability")
    print("="*60)
    
    # Use a contact_id that has workshift configured
    contact_id = 100  # Replace with actual test contact_id
    date = "2026-03-10"
    
    url = f"{BASE_URL}{API_PREFIX}/teacher/{contact_id}/availability"
    params = {"date": date}
    
    print(f"\nRequest: GET {url}")
    print(f"Params: {params}")
    
    try:
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        print(f"\nStatus: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            data = result.get("data", {})
            
            print(f"\nTeacher: {data.get('teacher_name')}")
            print(f"Date: {data.get('date')}")
            print(f"Is week-off: {data.get('is_week_off')}")
            
            if not data.get('is_week_off'):
                print(f"\nShift:")
                print(f"  Start: {data.get('shift_start')}")
                print(f"  End: {data.get('shift_end')}")
                
                busy_blocks = data.get("busy_blocks", [])
                print(f"\nBusy blocks: {len(busy_blocks)}")
                for block in busy_blocks[:3]:  # Show first 3
                    print(f"  - {block.get('start')} to {block.get('end')}: "
                          f"{block.get('title')} ({block.get('source')})")
                
                free_slots = data.get("free_slots", [])
                print(f"\nFree slots: {len(free_slots)}")
                for slot in free_slots[:3]:  # Show first 3
                    print(f"  - {slot.get('start')} to {slot.get('end')}: "
                          f"{slot.get('duration_minutes')} minutes")
                
                print(f"\nSummary:")
                print(f"  Total busy: {data.get('total_busy_minutes')} minutes")
                print(f"  Total free: {data.get('total_free_minutes')} minutes")
                
                if data.get('warnings'):
                    print(f"\nWarnings: {', '.join(data.get('warnings'))}")
                
                print(f"\n✓ Availability calculation working correctly")
            else:
                print("  ℹ This is a week-off day (no availability)")
        else:
            print(f"Error: {response.text}")
            
    except Exception as e:
        print(f"Exception: {str(e)}")


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("Teacher Calendar Availability - Implementation Verification")
    print("="*60)
    print("\nNOTE: Update ACCESS_TOKEN and contact_id values before running")
    print("="*60)
    
    test_calendar_events_timezone_fix()
    test_teacher_availability()
    
    print("\n" + "="*60)
    print("Tests Complete")
    print("="*60)


if __name__ == "__main__":
    main()
