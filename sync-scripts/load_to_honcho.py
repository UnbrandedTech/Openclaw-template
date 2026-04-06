#!/usr/bin/env python3
"""
load_to_honcho.py — Push transcripts, calendar events, and GitHub activity into Honcho.

Reads local data files and loads them into Honcho as sessions, peers, and messages:
  - Transcripts (.txt files) -> one session per file
  - Calendar events (JSON)   -> single calendar-events session
  - GitHub activity (JSON)   -> one session per repo

Tracks sync state to avoid re-sending data.
Usage: python3 load_to_honcho.py [--dry-run] [--verbose] [--reset] [--sources transcripts,calendar,github]
"""

import argparse
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from shared import (
    WORKSPACE,
    get_honcho,
    load_json,
    save_json,
    sanitize_id,
    script_lock,
    HONCHO_BASE_URL,
    HONCHO_WORKSPACE,
)

TRANSCRIPTIONS_DIR = WORKSPACE / "transcriptions"
CALENDAR_FILE = WORKSPACE / "calendar_events.json"
GITHUB_FILE = WORKSPACE / "github_activity.json"
SYNC_STATE_FILE = WORKSPACE / "memory" / "honcho-load-state.json"

BATCH_SIZE = 100
PEER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
ALL_SOURCES = ("transcripts", "calendar", "github")

ATTENDEE_MIN_MEETINGS = 3


def safe_peer_id(raw: str) -> str:
    """Sanitize a raw string into a valid Honcho peer ID."""
    pid = sanitize_id(raw)
    if not pid or not PEER_ID_PATTERN.fullmatch(pid):
        return f"peer-{sanitize_id(raw)}"
    return pid


# ── Transcript loading ─────────────────────────────────────────────────────


def collect_transcript_files(sync_state: dict) -> list[Path]:
    """Return transcript .txt files that haven't been synced yet."""
    if not TRANSCRIPTIONS_DIR.exists():
        return []
    loaded = set(sync_state.get("transcripts", {}).get("files_loaded", []))
    files = []
    for p in sorted(TRANSCRIPTIONS_DIR.glob("*.txt")):
        if p.name not in loaded:
            files.append(p)
    return files


MAX_MSG_CHARS = 24000  # Honcho limit is 25000; leave headroom


def _chunk_text(text: str, max_chars: int = MAX_MSG_CHARS) -> list[str]:
    """Split text into chunks that fit within Honcho's message size limit.

    Tries to break on paragraph boundaries, falls back to hard split.
    """
    if len(text) <= max_chars:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_chars:
            chunks.append(text)
            break
        # Try to break on a double newline (paragraph boundary)
        split_at = text.rfind("\n\n", 0, max_chars)
        if split_at < max_chars // 2:
            # No good paragraph break; try single newline
            split_at = text.rfind("\n", 0, max_chars)
        if split_at < max_chars // 2:
            # Hard split
            split_at = max_chars
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def load_transcripts(honcho, new_files: list[Path], sync_state: dict, *, verbose: bool):
    """Push transcript files into Honcho, chunking long transcripts."""
    ingest_peer = honcho.peer("transcript-ingest", metadata={
        "source": "transcript",
        "display_name": "Transcript Ingest",
    })

    sent = 0
    errors = 0
    for path in new_files:
        stem = path.stem
        session_id = f"transcript-{sanitize_id(stem)}"
        session = honcho.session(session_id, metadata={
            "source": "transcript",
            "type": "meeting_transcript",
        })

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"  WARNING: Could not read {path}: {e}")
            errors += 1
            continue

        if not text.strip():
            if verbose:
                print(f"  Skipping empty transcript: {path.name}")
            continue

        chunks = _chunk_text(text)
        msgs = []
        for i, chunk in enumerate(chunks):
            meta = {"filename": path.name}
            if len(chunks) > 1:
                meta["chunk"] = i + 1
                meta["total_chunks"] = len(chunks)
            msgs.append(ingest_peer.message(
                chunk,
                metadata=meta,
                configuration={"reasoning": {"enabled": False}},
            ))

        try:
            for i in range(0, len(msgs), BATCH_SIZE):
                session.add_messages(msgs[i:i + BATCH_SIZE])
            sent += 1
            if verbose or len(chunks) > 1:
                print(f"  transcript: {path.name} ({len(chunks)} chunk{'s' if len(chunks) > 1 else ''})")
        except Exception as e:
            errors += 1
            print(f"  ERROR sending transcript {path.name}: {e}")
            continue

        # Track immediately so a crash doesn't re-send
        state = sync_state.setdefault("transcripts", {"files_loaded": []})
        state["files_loaded"].append(path.name)
        state["last_synced_at"] = time.time()
        save_json(SYNC_STATE_FILE, sync_state)

    print(f"  Transcripts: sent {sent}, errors {errors}")
    return sent, errors


# ── Calendar loading ───────────────────────────────────────────────────────


def load_calendar(honcho, sync_state: dict, *, verbose: bool):
    """Push calendar events into Honcho."""
    if not CALENDAR_FILE.exists():
        if verbose:
            print("  No calendar_events.json found — skipping calendar.")
        return 0, 0

    raw = load_json(CALENDAR_FILE)
    events = raw if isinstance(raw, list) else raw.get("events", raw.get("items", []))
    if not events:
        print("  No calendar events found.")
        return 0, 0

    prev_count = sync_state.get("calendar", {}).get("event_count", 0)
    if len(events) <= prev_count:
        print(f"  Calendar: no new events (already synced {prev_count}).")
        return 0, 0

    # Only send events beyond what we already synced
    new_events = events[prev_count:]

    calendar_peer = honcho.peer("calendar-sync", metadata={
        "source": "calendar",
        "display_name": "Calendar Sync",
    })

    session = honcho.session("calendar-events", metadata={
        "source": "calendar",
        "type": "event_log",
    })

    # Count attendee appearances across ALL events (not just new) for peer creation
    attendee_counts: Counter = Counter()
    for ev in events:
        attendees = ev.get("attendees", [])
        for att in attendees:
            email = att.get("email", "")
            if email:
                attendee_counts[email] += 1

    # Create peers for frequent attendees
    frequent_peers = {}
    for email, count in attendee_counts.items():
        if count >= ATTENDEE_MIN_MEETINGS:
            peer_id = safe_peer_id(email.split("@")[0])
            display = att_display_name(events, email)
            peer = honcho.peer(peer_id, metadata={
                "source": "calendar",
                "email": email,
                "display_name": display,
                "meeting_count": count,
            })
            frequent_peers[email] = peer

    # Build messages for new events
    honcho_msgs = []
    for ev in new_events:
        title = ev.get("summary", ev.get("title", "(no title)"))
        date = ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", ev.get("date", "unknown")))
        if isinstance(date, dict):
            date = str(date)
        attendees = ev.get("attendees", [])
        names = ", ".join(
            a.get("displayName", a.get("email", "unknown"))
            for a in attendees
        ) or "none"
        location = ev.get("location", "none")

        text = f"{title} | {date} | Attendees: {names} | Location: {location}"
        meta = {}
        if ev.get("id"):
            meta["event_id"] = ev["id"]

        honcho_msgs.append(calendar_peer.message(
            text,
            metadata=meta,
            configuration={"reasoning": {"enabled": False}},
        ))

    # Send in batches
    sent = 0
    errors = 0
    for i in range(0, len(honcho_msgs), BATCH_SIZE):
        batch = honcho_msgs[i:i + BATCH_SIZE]
        try:
            session.add_messages(batch)
            sent += len(batch)
        except Exception as e:
            errors += 1
            print(f"  ERROR sending calendar batch: {e}")
        if i + BATCH_SIZE < len(honcho_msgs):
            time.sleep(0.5)

    # Update sync state
    sync_state["calendar"] = {
        "last_synced_at": time.time(),
        "event_count": len(events),
    }
    save_json(SYNC_STATE_FILE, sync_state)

    print(f"  Calendar: sent {sent} events, errors {errors}")
    return sent, errors


def att_display_name(events: list, email: str) -> str:
    """Find the best display name for an attendee email across all events."""
    for ev in events:
        for att in ev.get("attendees", []):
            if att.get("email") == email and att.get("displayName"):
                return att["displayName"]
    return email.split("@")[0]


# ── GitHub loading ─────────────────────────────────────────────────────────


def load_github(honcho, sync_state: dict, *, verbose: bool):
    """Push GitHub activity into Honcho."""
    if not GITHUB_FILE.exists():
        if verbose:
            print("  No github_activity.json found — skipping GitHub.")
        return 0, 0

    raw = load_json(GITHUB_FILE)
    repos = raw if isinstance(raw, list) else raw.get("repos", raw.get("repositories", []))
    if not repos:
        print("  No GitHub repos found.")
        return 0, 0

    # Build collaborator peers
    collab_peers: dict[str, object] = {}

    def get_github_peer(username: str):
        if username in collab_peers:
            return collab_peers[username]
        peer_id = safe_peer_id(username)
        peer = honcho.peer(peer_id, metadata={
            "source": "github",
            "github_username": username,
            "display_name": username,
        })
        collab_peers[username] = peer
        return peer

    total_sent = 0
    total_errors = 0

    for repo in repos:
        # Support both flat and nested structures
        if isinstance(repo, dict):
            owner = repo.get("owner", repo.get("nameWithOwner", "").split("/")[0] if "/" in repo.get("nameWithOwner", "") else "unknown")
            name = repo.get("name", repo.get("nameWithOwner", "").split("/")[-1] if "/" in repo.get("nameWithOwner", "") else "unknown")
            prs = repo.get("pullRequests", repo.get("prs", repo.get("pull_requests", [])))
        else:
            continue

        session_id = f"github-{sanitize_id(owner)}-{sanitize_id(name)}"
        session = honcho.session(session_id, metadata={
            "source": "github",
            "repo": f"{owner}/{name}",
        })

        honcho_msgs = []
        for pr in prs:
            if not isinstance(pr, dict):
                continue

            author = pr.get("author", pr.get("user", {}).get("login", "unknown"))
            if isinstance(author, dict):
                author = author.get("login", "unknown")

            peer = get_github_peer(author)

            number = pr.get("number", "")
            title = pr.get("title", "(no title)")
            state = pr.get("state", "unknown")
            review_decision = pr.get("reviewDecision", "")
            url = pr.get("url", pr.get("html_url", ""))

            text = f"PR #{number}: {title}"
            if url:
                text += f"\n{url}"

            meta = {"number": number, "state": state}
            if review_decision:
                meta["reviewDecision"] = review_decision

            honcho_msgs.append(peer.message(
                text,
                metadata=meta,
                configuration={"reasoning": {"enabled": False}},
            ))

        if not honcho_msgs:
            continue

        # Send in batches
        sent = 0
        for i in range(0, len(honcho_msgs), BATCH_SIZE):
            batch = honcho_msgs[i:i + BATCH_SIZE]
            try:
                session.add_messages(batch)
                sent += len(batch)
            except Exception as e:
                total_errors += 1
                print(f"  ERROR sending GitHub batch for {owner}/{name}: {e}")
            if i + BATCH_SIZE < len(honcho_msgs):
                time.sleep(0.5)

        total_sent += sent
        if verbose:
            print(f"  github: {owner}/{name} -> {sent} PRs")

    sync_state["github"] = {"last_synced_at": time.time()}
    save_json(SYNC_STATE_FILE, sync_state)

    print(f"  GitHub: sent {total_sent} PRs, errors {total_errors}")
    return total_sent, total_errors


# ── Main ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Push transcripts, calendar, and GitHub data to Honcho")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be sent without writing")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output")
    parser.add_argument("--reset", action="store_true", help="Reset sync state and re-send everything")
    parser.add_argument(
        "--sources",
        default=",".join(ALL_SOURCES),
        help=f"Comma-separated list of sources (default: {','.join(ALL_SOURCES)})",
    )
    parser.add_argument("--base-url", default=HONCHO_BASE_URL, help=f"Honcho API URL (default: {HONCHO_BASE_URL})")
    parser.add_argument("--workspace", default=HONCHO_WORKSPACE, help=f"Honcho workspace (default: {HONCHO_WORKSPACE})")
    args = parser.parse_args()

    sources = {s.strip().lower() for s in args.sources.split(",") if s.strip()}
    invalid = sources - set(ALL_SOURCES)
    if invalid:
        print(f"ERROR: Unknown source(s): {', '.join(sorted(invalid))}")
        print(f"  Valid sources: {', '.join(ALL_SOURCES)}")
        sys.exit(1)

    with script_lock("load_to_honcho"):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting Honcho data load...")
        print(f"  Sources: {', '.join(sorted(sources))}")

        sync_state = {} if args.reset else load_json(SYNC_STATE_FILE)
        if args.reset:
            print("  Reset: re-sending all data")

        # ── Dry-run summary ────────────────────────────────────────────────

        if args.dry_run:
            if "transcripts" in sources:
                files = collect_transcript_files(sync_state)
                print(f"\n  Transcripts: {len(files)} new file(s)")
                if args.verbose:
                    for f in files[:10]:
                        print(f"    {f.name}")
                    if len(files) > 10:
                        print(f"    ... and {len(files) - 10} more")

            if "calendar" in sources:
                if CALENDAR_FILE.exists():
                    raw = load_json(CALENDAR_FILE)
                    events = raw if isinstance(raw, list) else raw.get("events", raw.get("items", []))
                    prev = sync_state.get("calendar", {}).get("event_count", 0)
                    new_count = max(0, len(events) - prev)
                    print(f"\n  Calendar: {new_count} new event(s) (of {len(events)} total)")
                else:
                    print("\n  Calendar: file not found — nothing to sync")

            if "github" in sources:
                if GITHUB_FILE.exists():
                    raw = load_json(GITHUB_FILE)
                    repos = raw if isinstance(raw, list) else raw.get("repos", raw.get("repositories", []))
                    total_prs = sum(
                        len(r.get("pullRequests", r.get("prs", r.get("pull_requests", []))))
                        for r in repos if isinstance(r, dict)
                    )
                    print(f"\n  GitHub: {len(repos)} repo(s), {total_prs} PR(s)")
                else:
                    print("\n  GitHub: file not found — nothing to sync")

            print("\n[DRY RUN] No data sent.")
            return

        # ── Real run ───────────────────────────────────────────────────────

        honcho = get_honcho(args.base_url, args.workspace)

        grand_sent = 0
        grand_errors = 0

        if "transcripts" in sources:
            new_files = collect_transcript_files(sync_state)
            if new_files:
                s, e = load_transcripts(honcho, new_files, sync_state, verbose=args.verbose)
                grand_sent += s
                grand_errors += e
            else:
                print("  Transcripts: nothing new to sync.")

        if "calendar" in sources:
            s, e = load_calendar(honcho, sync_state, verbose=args.verbose)
            grand_sent += s
            grand_errors += e

        if "github" in sources:
            s, e = load_github(honcho, sync_state, verbose=args.verbose)
            grand_sent += s
            grand_errors += e

        save_json(SYNC_STATE_FILE, sync_state)
        print(f"\nDone. Sent {grand_sent} item(s) to Honcho, {grand_errors} error(s).")


if __name__ == "__main__":
    main()
