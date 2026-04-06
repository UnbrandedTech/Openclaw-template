#!/usr/bin/env python3
"""
sync_calendar.py — Parse calendar events and output structured JSON.

Primary: reads .ics files from vdirsyncer calendars directory.
Fallback: fetches events directly from Google Calendar API when no .ics files exist.

Writes:
  1. ~/.openclaw/workspace/calendar_events.json — event list sorted by date descending
  2. ~/.openclaw/workspace/calendar_attendees.json — attendee frequency map

Usage: python3 sync_calendar.py [--days 30] [--dry-run]
  --days N:    Lookback window in days from today (default: 30)
  --dry-run:   Show stats without writing files
"""

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from icalendar import Calendar
    HAS_ICAL = True
except ImportError:
    HAS_ICAL = False

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


# ── Google Calendar API fallback ─────────────────────────────────────────


def _get_gcloud_token() -> str:
    """Get a GCP access token via gcloud."""
    result = subprocess.run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        capture_output=True, text=True, timeout=10,
    )
    return result.stdout.strip()


def _gcal_api(token: str, path: str, params: dict | None = None) -> dict:
    """Make a GET request to the Google Calendar API."""
    url = f"https://www.googleapis.com/calendar/v3{path}"
    if params:
        qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        url = f"{url}?{qs}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def fetch_events_from_google_api(days: int) -> list[dict]:
    """Fetch events directly from Google Calendar API as a fallback."""
    try:
        token = _get_gcloud_token()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("  Google Calendar API fallback: gcloud not available", file=sys.stderr)
        return []

    if not token:
        print("  Google Calendar API fallback: no access token", file=sys.stderr)
        return []

    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=days)).isoformat()
    time_max = now.isoformat()
    user_lower = USER_EMAIL.lower() if USER_EMAIL else ""

    # Get calendar list
    try:
        cal_list = _gcal_api(token, "/users/me/calendarList")
    except Exception as e:
        print(f"  Google Calendar API fallback: failed to list calendars: {e}", file=sys.stderr)
        return []

    events = []
    for cal in cal_list.get("items", []):
        cal_id = cal["id"]
        try:
            result = _gcal_api(token, f"/calendars/{cal_id}/events", {
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": "2500",
            })
        except urllib.error.HTTPError:
            continue
        except Exception:
            continue

        for item in result.get("items", []):
            if item.get("status") == "cancelled":
                continue

            # Check if user declined
            declined = False
            attendees_raw = item.get("attendees", [])
            for att in attendees_raw:
                if att.get("email", "").lower() == user_lower and att.get("responseStatus") == "declined":
                    declined = True
                    break
            if declined:
                continue

            start = item.get("start", {})
            end = item.get("end", {})
            dtstart = start.get("dateTime") or start.get("date", "")
            dtend = end.get("dateTime") or end.get("date")

            organizer = item.get("organizer", {})
            attendees = [
                {
                    "name": att.get("displayName", ""),
                    "email": att.get("email", "").lower(),
                    "status": (att.get("responseStatus") or "unknown").upper(),
                }
                for att in attendees_raw
            ]

            events.append({
                "summary": item.get("summary", ""),
                "dtstart": dtstart,
                "dtend": dtend,
                "organizer": {
                    "name": organizer.get("displayName", ""),
                    "email": organizer.get("email", "").lower(),
                },
                "attendees": attendees,
                "location": item.get("location", ""),
                "description": (item.get("description") or "")[:500],
                "uid": item.get("iCalUID", item.get("id", "")),
            })

    print(f"  Google Calendar API: fetched {len(events)} events from {len(cal_list.get('items', []))} calendars")
    return events


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

        # Compute the cutoff date
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

        # Try vdirsyncer .ics files first
        ics_files = list(CALENDARS_BASE.rglob("*.ics")) if CALENDARS_BASE.exists() else []
        events = []
        parse_errors = 0

        if ics_files and HAS_ICAL:
            print(f"  Found {len(ics_files)} .ics files")

            for ics_path in ics_files:
                try:
                    raw = ics_path.read_bytes()
                    cal = Calendar.from_ical(raw)

                    for component in cal.walk("VEVENT"):
                        event = parse_event(component)
                        if event is None:
                            continue

                        dtstart = normalize_dt(datetime.fromisoformat(event["dtstart"]))
                        if dtstart is None or dtstart < cutoff:
                            continue

                        events.append(event)

                except Exception as e:
                    parse_errors += 1
                    print(f"  Parse error in {ics_path.name}: {e}")
                    continue
        else:
            # Fallback: fetch directly from Google Calendar API
            if not ics_files:
                print("  No .ics files found, trying Google Calendar API...")
            else:
                print("  icalendar not installed, trying Google Calendar API...")
            events = fetch_events_from_google_api(args.days)

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
