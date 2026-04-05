#!/usr/bin/env python3
"""
sync_meeting_transcripts.py — Pull meeting transcripts from email, save locally,
extract action items for the user, and update TODO.md + Obsidian daily note.

Supports email providers:
  - Google Workspace (via gogcli)
  - IMAP (any provider: Outlook, iCloud, Fastmail, ProtonMail Bridge, etc.)

Supports transcript sources: Gemini Notes, Grain, Fireflies, Otter, Fathom, MeetGeek, TL;DV.

Usage: python3 sync_meeting_transcripts.py [--full] [--skip-actions]
  --full:          Re-scan everything (ignore state, don't re-download existing files)
  --skip-actions:  Only download transcripts, skip action item extraction
"""

import email as email_lib
import email.header
import email.utils
import imaplib
import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from shared import WORKSPACE, VAULT_PATH, save_json as _atomic_save_json, USER_NAME, script_lock, load_json, get_secret

EMAILS_DIR = WORKSPACE / "transcriptions"
STATE_FILE = WORKSPACE / "memory/transcript-sync-state.json"
GOG = Path.home() / ".local/bin/gog"
ACCOUNT = os.environ.get("GOG_ACCOUNT", "")

# Load email provider config from user.json
_user_cfg = load_json(WORKSPACE / "user.json")
EMAIL_PROVIDER = _user_cfg.get("email_provider", "google")

# Gmail search queries for known transcript sources
QUERIES = [
    'from:gemini-notes@google.com',
    'from:noreply@grain.co',
    'from:grain.co subject:"meeting summary"',
    'from:fireflies.ai',
    'from:otter.ai',
    'from:fathom.video',
    'from:meetgeek.ai',
    'from:tldv.io',
]

MAX_PER_QUERY = 100


def slugify(text: str, max_len: int = 60) -> str:
    """Convert text to a filesystem-safe slug."""
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r'[^\w\s-]', '', text.lower())
    text = re.sub(r'[-\s]+', '-', text).strip('-')
    return text[:max_len]


def gog_search(query: str, max_results: int = 50) -> list[dict]:
    """Run gog gmail search and return thread list."""
    env = os.environ.copy()
    env["GOG_ACCOUNT"] = ACCOUNT
    env["PATH"] = f"{Path.home() / '.local/bin'}:{env.get('PATH', '')}"

    result = subprocess.run(
        [str(GOG), "gmail", "search", query, "--max", str(max_results), "--json"],
        capture_output=True, text=True, env=env, timeout=60
    )
    if result.returncode != 0:
        print(f"  Search failed for: {query}")
        print(f"  stderr: {result.stderr[:200]}")
        return []

    try:
        data = json.loads(result.stdout)
        return data.get("threads", [])
    except json.JSONDecodeError:
        print(f"  JSON parse error for: {query}")
        return []


def gog_read(thread_id: str) -> str:
    """Read a thread's content."""
    env = os.environ.copy()
    env["GOG_ACCOUNT"] = ACCOUNT
    env["PATH"] = f"{Path.home() / '.local/bin'}:{env.get('PATH', '')}"

    result = subprocess.run(
        [str(GOG), "gmail", "read", thread_id, "--json"],
        capture_output=True, text=True, env=env, timeout=60
    )
    if result.returncode != 0:
        print(f"  Read failed for thread {thread_id}: {result.stderr[:200]}")
        return ""
    return result.stdout


# ── IMAP email functions ───────────────────────────────────────────────────


def _imap_connect() -> imaplib.IMAP4_SSL:
    """Connect to the configured IMAP server."""
    server = _user_cfg.get("imap_server", "")
    port = int(_user_cfg.get("imap_port", 993))
    username = _user_cfg.get("imap_username", "")
    password = get_secret("IMAP_PASSWORD")

    if not all([server, username, password]):
        print("ERROR: IMAP not configured.", file=sys.stderr)
        print("  Set imap_server/imap_username in user.json and IMAP_PASSWORD in .env", file=sys.stderr)
        sys.exit(1)

    conn = imaplib.IMAP4_SSL(server, port)
    conn.login(username, password)
    return conn


def _decode_header(raw: str) -> str:
    """Decode a potentially RFC2047-encoded email header."""
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(data)
    return " ".join(decoded)


def _query_to_imap_criteria(query: str) -> str:
    """Convert a gogcli-style query string to IMAP SEARCH criteria."""
    from_match = re.match(r'from:(\S+)', query, re.IGNORECASE)
    subject_match = re.search(r'subject:"([^"]+)"', query, re.IGNORECASE)

    parts = []
    if from_match:
        parts.append(f'FROM "{from_match.group(1)}"')
    if subject_match:
        parts.append(f'SUBJECT "{subject_match.group(1)}"')

    return " ".join(parts) if parts else "ALL"


def imap_search(query: str, max_results: int = 50) -> list[dict]:
    """Search for emails via IMAP matching the given query."""
    criteria = _query_to_imap_criteria(query)
    try:
        conn = _imap_connect()
        conn.select("INBOX", readonly=True)

        status, data = conn.search(None, criteria)
        if status != "OK" or not data[0]:
            conn.close()
            conn.logout()
            return []

        ids = data[0].split()
        ids = ids[-max_results:]  # most recent N

        threads = []
        for mid in ids:
            status, msg_data = conn.fetch(mid, "(BODY.PEEK[HEADER])")
            if status != "OK":
                continue
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)
            threads.append({
                "id": mid.decode(),
                "from": _decode_header(msg.get("from", "")),
                "date": msg.get("date", ""),
                "subject": _decode_header(msg.get("subject", "")),
            })

        conn.close()
        conn.logout()
        return threads
    except Exception as e:
        print(f"  IMAP search error for '{query}': {e}", file=sys.stderr)
        return []


def imap_read(msg_id: str) -> str:
    """Read an email's full content via IMAP, returning JSON similar to gog output."""
    try:
        conn = _imap_connect()
        conn.select("INBOX", readonly=True)

        status, msg_data = conn.fetch(msg_id.encode(), "(RFC822)")
        if status != "OK":
            conn.close()
            conn.logout()
            return ""

        raw = msg_data[0][1]
        msg = email_lib.message_from_bytes(raw)

        # Extract text content
        text_parts = []
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        text_parts.append(payload.decode("utf-8", errors="replace"))
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                text_parts.append(payload.decode("utf-8", errors="replace"))

        result = [{
            "from": _decode_header(msg.get("from", "")),
            "date": msg.get("date", ""),
            "subject": _decode_header(msg.get("subject", "")),
            "body": "\n".join(text_parts),
        }]

        conn.close()
        conn.logout()
        return json.dumps(result)
    except Exception as e:
        print(f"  IMAP read error for message {msg_id}: {e}", file=sys.stderr)
        return ""


# ── Dispatch: pick search/read functions based on provider ─────────────────


def search_emails(query: str, max_results: int = 50) -> list[dict]:
    """Search for emails using the configured provider."""
    if EMAIL_PROVIDER == "imap":
        return imap_search(query, max_results)
    return gog_search(query, max_results)


def read_email(thread_id: str) -> str:
    """Read an email thread/message using the configured provider."""
    if EMAIL_PROVIDER == "imap":
        return imap_read(thread_id)
    return gog_read(thread_id)


# ── Source detection ───────────────────────────────────────────────────────


def detect_source(from_addr: str) -> str:
    """Map sender to a source tag."""
    addr = from_addr.lower()
    if "gemini-notes" in addr:
        return "gemini"
    if "grain" in addr:
        return "grain"
    if "fireflies" in addr:
        return "fireflies"
    if "otter" in addr:
        return "otter"
    if "fathom" in addr:
        return "fathom"
    if "meetgeek" in addr:
        return "meetgeek"
    if "tldv" in addr or "tl-dv" in addr:
        return "tldv"
    return "unknown"


def extract_date(date_str: str) -> str:
    """Extract YYYY-MM-DD from various date formats."""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", date_str)
    if match:
        return match.group(1)
    return datetime.now().strftime("%Y-%m-%d")


def extract_meeting_name(subject: str) -> str:
    """Pull meeting name from subject line."""
    # Gemini: Notes: "Team Standup" Mar 20, 2026
    m = re.search(r'["\u201c\u201d](.+?)["\u201c\u201d]', subject)
    if m:
        return m.group(1)

    # Grain: Meeting summary: "Quick Sync on Onboarding Flow"
    m = re.search(r'summary:\s*["\u201c\u201d]?(.+?)["\u201c\u201d]?\s*$', subject, re.IGNORECASE)
    if m:
        return m.group(1).strip('"\u201c\u201d\' ')

    # Fallback: strip common prefixes
    for prefix in ["Notes:", "Meeting summary:", "Recording ready:", "Meeting notes:"]:
        if subject.startswith(prefix):
            return subject[len(prefix):].strip().strip('"\u201c\u201d\' ')

    return subject


def parse_thread_content(raw_json: str) -> str:
    """Extract readable text from gog gmail read --json output."""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return raw_json

    messages = data if isinstance(data, list) else data.get("messages", [data])

    parts = []
    for msg in messages:
        if isinstance(msg, dict):
            subject = msg.get("subject", "")
            from_addr = msg.get("from", "")
            date = msg.get("date", "")
            body = msg.get("body", msg.get("text", msg.get("snippet", "")))

            parts.append(f"From: {from_addr}")
            parts.append(f"Date: {date}")
            parts.append(f"Subject: {subject}")
            parts.append("")
            if isinstance(body, str):
                parts.append(body)
            parts.append("\n---\n")
        elif isinstance(msg, str):
            parts.append(msg)

    return "\n".join(parts) if parts else raw_json


# ── Action item extraction ──────────────────────────────────────────────────

def extract_user_action_items(content: str, source: str) -> list[str]:
    """Extract action items assigned to the user from any meeting transcript.

    Supports multiple transcript formats:
    - Gemini Notes: [Full Name] action item text
    - Grain/Fireflies/Otter: "Action Items" section with bullet points mentioning user
    - Generic: bullet points near user's name with action-like language
    """
    if not USER_NAME:
        return []

    items = []

    # Strategy 1: Gemini format — [Full Name] item text
    escaped_name = re.escape(USER_NAME)
    gemini_pattern = re.compile(
        rf"\[{escaped_name}\]\s+(.*?)(?=\n\[|\Z)",
        re.IGNORECASE | re.DOTALL
    )
    for match in gemini_pattern.finditer(content):
        item = match.group(1).strip().replace("\r\n", " ").replace("\n", " ")
        item = re.sub(r"\s+", " ", item).strip().rstrip(".")
        # Trim footer noise
        for stop in ["Meeting records", "Document Notes", "Google LLC", "Is the Next Steps"]:
            if stop in item:
                item = item[:item.index(stop)].strip().rstrip(".")
        if item and len(item) > 3:
            items.append(item)

    # Strategy 2: "Action Items" / "Next Steps" / "Follow-ups" section
    section_pattern = re.compile(
        r"(?:action items?|next steps?|follow[- ]?ups?|to[- ]?do|assignments?)\s*:?\s*\n(.*?)(?=\n(?:#{1,3}\s|\n\n\n)|\Z)",
        re.IGNORECASE | re.DOTALL
    )
    first_name = USER_NAME.split()[0].lower() if USER_NAME else ""
    name_lower = USER_NAME.lower()

    for section_match in section_pattern.finditer(content):
        section_text = section_match.group(1)
        for line in section_text.split("\n"):
            stripped = line.strip()
            if not stripped or len(stripped) < 5:
                continue
            # Remove bullet/number prefix
            cleaned = re.sub(r"^[-*\u2022\d.)\]]+\s*", "", stripped)
            if not cleaned:
                continue
            # Include if the user is mentioned in this line
            line_lower = cleaned.lower()
            if first_name in line_lower or name_lower in line_lower:
                cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
                if cleaned and len(cleaned) > 3 and cleaned not in items:
                    items.append(cleaned)

    # Strategy 3: @mentions or direct assignments anywhere in the text
    assign_pattern = re.compile(
        rf"(?:@{re.escape(first_name)}|assigned to {escaped_name}|{escaped_name} (?:will|to|should|needs? to))\s+(.{{10,200}}?)(?:\.|$)",
        re.IGNORECASE
    )
    for match in assign_pattern.finditer(content):
        item = match.group(1).strip().rstrip(".")
        item = re.sub(r"\s+", " ", item)
        if item and len(item) > 3 and item not in items:
            items.append(item)

    return items


def extract_summary(content: str) -> str:
    """Pull the Summary/Overview section from meeting content."""
    pattern = re.compile(
        r"(?:^|\n)(?:#{0,3}\s*)?(?:summary|overview|key (?:points|takeaways))\s*:?\s*\n(.*?)(?=\n(?:#{1,3}\s|action items?|next steps?|follow)|\Z)",
        re.IGNORECASE | re.DOTALL
    )
    match = pattern.search(content)
    if match:
        return match.group(1).strip()[:500]
    return ""


def update_todo_md(items: list[str], meeting_name: str, date_label: str):
    """Add action items from a meeting to TODO.md."""
    todo_path = WORKSPACE / "TODO.md"
    if not todo_path.exists() or not items:
        return

    content = todo_path.read_text()
    new_lines = []
    for item in items:
        tag = f"`{meeting_name} {date_label}`"
        line = f"- [ ] **{item}** {tag}"
        if item[:30].lower() not in content.lower():
            new_lines.append(line)

    if not new_lines:
        return

    insert_after = "## \U0001f4cb This Week"
    if insert_after in content:
        idx = content.index(insert_after) + len(insert_after)
        content = content[:idx] + "\n" + "\n".join(new_lines) + content[idx:]
        todo_path.write_text(content)
        print(f"    Added {len(new_lines)} items to TODO.md")


def update_daily_note(meeting_name: str, date_str: str, summary: str, items: list[str]):
    """Append meeting recap to today's Obsidian daily note."""
    today = datetime.now().strftime("%Y-%m-%d")
    note_path = VAULT_PATH / "Daily Notes" / f"{today}.md"
    if not note_path.exists():
        return

    content = note_path.read_text()
    # Avoid duplicate entries for the same meeting
    if meeting_name in content:
        return

    recap = f"\n## \U0001f916 {meeting_name}\n*{date_str}*\n\n"
    if summary:
        recap += f"{summary}\n\n"
    if items:
        recap += "**Your action items:**\n"
        for t in items:
            recap += f"- [ ] {t}\n"

    note_path.write_text(content + recap)
    print(f"    Updated daily note with {meeting_name}")


# ── State management ────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"downloaded_ids": [], "last_run": None}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["downloaded_ids"] = state["downloaded_ids"][-500:]
    state["last_run"] = datetime.now().isoformat()
    _atomic_save_json(STATE_FILE, state)


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    if EMAIL_PROVIDER == "google" and not ACCOUNT:
        print("ERROR: Set GOG_ACCOUNT environment variable (email_provider is 'google').", file=sys.stderr)
        sys.exit(1)

    full_mode = "--full" in sys.argv
    skip_actions = "--skip-actions" in sys.argv

    with script_lock("sync_meeting_transcripts"):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Syncing meeting transcripts (provider: {EMAIL_PROVIDER})...")
        EMAILS_DIR.mkdir(parents=True, exist_ok=True)

        state = load_state()
        downloaded = set(state.get("downloaded_ids", []))

        # Collect all threads from all queries
        all_threads = {}
        for query in QUERIES:
            print(f"  Searching: {query}")
            threads = search_emails(query, MAX_PER_QUERY)
            for t in threads:
                all_threads[t["id"]] = t
            print(f"    Found {len(threads)} threads")

        print(f"\n  Total unique threads: {len(all_threads)}")

        to_process = {tid: t for tid, t in all_threads.items() if tid not in downloaded or full_mode}
        print(f"  New threads to process: {len(to_process)}")

        saved = 0
        errors = 0
        total_actions = 0

        for tid, thread in sorted(to_process.items(), key=lambda x: x[1].get("date", ""), reverse=True):
            subject = thread.get("subject", "Unknown")
            from_addr = thread.get("from", "")
            date_str = thread.get("date", "")

            source = detect_source(from_addr)
            date = extract_date(date_str)
            meeting_name = extract_meeting_name(subject)
            slug = slugify(meeting_name)

            filename = f"{source}-{date}-{slug}.txt"
            filepath = EMAILS_DIR / filename

            # Download transcript if not already saved
            if filepath.exists():
                content = filepath.read_text()
                downloaded.add(tid)
            else:
                print(f"  Downloading: {subject}")
                raw = read_email(tid)
                if not raw:
                    errors += 1
                    continue

                content = parse_thread_content(raw)
                filepath.write_text(content)
                downloaded.add(tid)
                saved += 1
                print(f"    -> {filename}")

            # Extract action items from all meetings (not just standups)
            if not skip_actions and content:
                items = extract_user_action_items(content, source)
                if items:
                    total_actions += len(items)
                    print(f"    {len(items)} action item(s) for {USER_NAME or 'user'}")
                    for item in items:
                        print(f"      - {item}")
                    date_label = date or datetime.now().strftime("%m/%d")
                    update_todo_md(items, meeting_name, date_label)
                    summary = extract_summary(content)
                    update_daily_note(meeting_name, date_str, summary, items)

        state["downloaded_ids"] = list(downloaded)
        save_state(state)

        print(f"\n  Done. {saved} new transcripts, {total_actions} action items, {errors} errors.")
        if saved > 0:
            print(f"  Files in {EMAILS_DIR}")


if __name__ == "__main__":
    main()
