#!/usr/bin/env python3
"""
sync_calendar.py — Parse vdirsyncer .ics calendar files and output structured JSON.

Reads .ics files from the vdirsyncer calendars directory, extracts event details,
and writes two files:
  1. ~/.openclaw/workspace/calendar_events.json — event list sorted by date descending
  2. ~/.openclaw/workspace/calendar_attendees.json — attendee frequency map

Usage: python3 sync_calendar.py [--days 30] [--dry-run]
  --days N:    Lookback window in days from today (default: 30)
  --dry-run:   Show stats without writing files
"""

import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from icalendar import Calendar
except ImportError:
    print("ERROR: icalendar not installed. Run: pip3 install icalendar", file=sys.stderr)
    sys.exit(1)

from shared import WORKSPACE, save_json, script_lock, USER_EMAIL

CALENDARS_BASE = Path.home() / ".local" / "share" / "vdirsyncer" / "calendars"

EVENTS_FILE = WORKSPACE / "calendar_events.json"
ATTENDEES_FILE = WORKSPACE / "calendar_attendees.json"


def normalize_dt(dt_value) -> datetime | None:
    """Convert a date or datetime to a timezone-aware datetime.

    Returns None if the value cannot be converted.
    """
    if dt_value is None:
        return None
    if isinstance(dt_value, datetime):
        if dt_value.tzinfo is None:
            return dt_value.replace(tzinfo=timezone.utc)
        return dt_value
    if isinstance(dt_value, date):
        return datetime(dt_value.year, dt_value.month, dt_value.day, tzinfo=timezone.utc)
    return None


def extract_email(value) -> str:
    """Extract a bare email address from a CalAddress or string like 'mailto:user@example.com'."""
    if value is None:
        return ""
    s = str(value)
    if s.lower().startswith("mailto:"):
        s = s[7:]
    return s.strip().lower()


def extract_name(prop) -> str:
    """Extract the CN (common name) parameter from a vCalendar property."""
    if prop is None:
        return ""
    params = getattr(prop, "params", {})
    cn = params.get("CN", "")
    return str(cn) if cn else ""


def extract_partstat(prop) -> str:
    """Extract the PARTSTAT parameter from an attendee property."""
    if prop is None:
        return ""
    params = getattr(prop, "params", {})
    return str(params.get("PARTSTAT", "")).upper()


def user_declined(component) -> bool:
    """Check if the user declined this event based on PARTSTAT."""
    if not USER_EMAIL:
        return False

    attendees = component.get("ATTENDEE")
    if attendees is None:
        return False

    # ATTENDEE can be a single value or a list
    if not isinstance(attendees, list):
        attendees = [attendees]

    user_lower = USER_EMAIL.lower()
    for att in attendees:
        email = extract_email(att)
        if email == user_lower:
            partstat = extract_partstat(att)
            if partstat == "DECLINED":
                return True
    return False


def parse_event(component) -> dict | None:
    """Parse a VEVENT component into a dict. Returns None if the event should be skipped."""
    # Skip cancelled events
    status = str(component.get("STATUS", "")).upper()
    if status == "CANCELLED":
        return None

    # Skip events the user declined
    if user_declined(component):
        return None

    # Extract dtstart
    dtstart_prop = component.get("DTSTART")
    if dtstart_prop is None:
        return None
    dtstart = normalize_dt(dtstart_prop.dt)
    if dtstart is None:
        return None

    # Extract dtend (may be missing for all-day events)
    dtend = None
    dtend_prop = component.get("DTEND")
    if dtend_prop is not None:
        dtend = normalize_dt(dtend_prop.dt)

    # Summary / title
    summary = str(component.get("SUMMARY", "")).strip()

    # Organizer
    organizer_prop = component.get("ORGANIZER")
    organizer = {
        "name": extract_name(organizer_prop),
        "email": extract_email(organizer_prop),
    }

    # Attendees
    attendees_raw = component.get("ATTENDEE")
    attendees = []
    if attendees_raw is not None:
        if not isinstance(attendees_raw, list):
            attendees_raw = [attendees_raw]
        for att in attendees_raw:
            attendees.append({
                "name": extract_name(att),
                "email": extract_email(att),
                "status": extract_partstat(att) or "UNKNOWN",
            })

    # Location
    location = str(component.get("LOCATION", "")).strip()

    # Description (truncated)
    description = str(component.get("DESCRIPTION", "")).strip()
    if len(description) > 500:
        description = description[:500]

    # UID
    uid = str(component.get("UID", "")).strip()

    return {
        "summary": summary,
        "dtstart": dtstart.isoformat(),
        "dtend": dtend.isoformat() if dtend else None,
        "organizer": organizer,
        "attendees": attendees,
        "location": location,
        "description": description,
        "uid": uid,
    }


def build_attendee_map(events: list[dict]) -> dict:
    """Build a frequency map of attendees from the event list.

    Returns: {email: {name, meeting_count, last_met_date}}
    Excludes the user's own email.
    """
    freq: dict[str, dict] = {}
    user_lower = USER_EMAIL.lower() if USER_EMAIL else ""

    for event in events:
        event_date = event["dtstart"][:10]  # YYYY-MM-DD

        for att in event.get("attendees", []):
            email = att.get("email", "").lower()
            if not email or email == user_lower:
                continue

            if email not in freq:
                freq[email] = {
                    "name": att.get("name", ""),
                    "meeting_count": 0,
                    "last_met_date": event_date,
                }

            freq[email]["meeting_count"] += 1
            # Update name if we have one now and didn't before
            if att.get("name") and not freq[email]["name"]:
                freq[email]["name"] = att["name"]
            # Track most recent meeting date
            if event_date > freq[email]["last_met_date"]:
                freq[email]["last_met_date"] = event_date

    return freq


def main():
    parser = argparse.ArgumentParser(description="Parse vdirsyncer calendar files to JSON")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days (default: 30)")
    parser.add_argument("--dry-run", action="store_true", help="Show stats without writing files")
    args = parser.parse_args()

    with script_lock("sync_calendar"):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Syncing calendar events...")
        print(f"  Calendars base: {CALENDARS_BASE}")
        print(f"  Lookback: {args.days} days")
        if USER_EMAIL:
            print(f"  User email: {USER_EMAIL}")
        else:
            print("  WARNING: USER_EMAIL not set — decline filtering disabled")

        if not CALENDARS_BASE.exists():
            print(f"  ERROR: Calendar directory not found: {CALENDARS_BASE}", file=sys.stderr)
            sys.exit(1)

        # Compute the cutoff date
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

        # Find all .ics files recursively
        ics_files = list(CALENDARS_BASE.rglob("*.ics"))
        print(f"  Found {len(ics_files)} .ics files")

        events = []
        parse_errors = 0

        for ics_path in ics_files:
            try:
                raw = ics_path.read_bytes()
                cal = Calendar.from_ical(raw)

                for component in cal.walk("VEVENT"):
                    event = parse_event(component)
                    if event is None:
                        continue

                    # Filter by date window
                    dtstart = normalize_dt(datetime.fromisoformat(event["dtstart"]))
                    if dtstart is None or dtstart < cutoff:
                        continue

                    events.append(event)

            except Exception as e:
                parse_errors += 1
                print(f"  Parse error in {ics_path.name}: {e}")
                continue

        # Sort by dtstart descending
        events.sort(key=lambda e: e["dtstart"], reverse=True)

        # Build attendee map
        attendee_map = build_attendee_map(events)

        # Summary
        print(f"\n  Events parsed: {len(events)}")
        print(f"  Unique attendees: {len(attendee_map)}")
        print(f"  Parse errors: {parse_errors}")

        # Top 5 attendees by meeting count
        if attendee_map:
            top = sorted(attendee_map.items(), key=lambda x: x[1]["meeting_count"], reverse=True)[:5]
            print("\n  Top 5 attendees by meeting count:")
            for email, info in top:
                name = info["name"] or email
                print(f"    {name}: {info['meeting_count']} meetings (last: {info['last_met_date']})")

        if args.dry_run:
            print("\n  Dry run — no files written.")
            return

        # Write output files
        save_json(EVENTS_FILE, events)
        print(f"\n  Wrote {len(events)} events to {EVENTS_FILE}")

        save_json(ATTENDEES_FILE, attendee_map)
        print(f"  Wrote {len(attendee_map)} attendees to {ATTENDEES_FILE}")


if __name__ == "__main__":
    main()
