#!/usr/bin/env python3
"""
gmail_standup_sync.py — Watch for Gemini standup recap emails and update
TODO.md and Obsidian daily note with action items from the recap.

Usage: python3 gmail_standup_sync.py
"""

import json
import re
import base64
from datetime import datetime, timezone
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

TOKEN_FILE = Path.home() / ".config/gapi/token.json"
CREDS_FILE = Path.home() / ".config/gapi/credentials.json"
STATE_FILE = Path.home() / ".openclaw/workspace/memory/standup-sync-state.json"
WORKSPACE = Path.home() / ".openclaw/workspace"
EMAILS_DIR = WORKSPACE / "transcriptions"
VAULT = Path.home() / "Documents/Obsidian Vault"

JAMES_DISPLAY = "James Kenaley"


def get_service():
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_message_id": None, "processed_ids": []}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def get_body(payload, prefer_plain=True):
    """Recursively extract text from message payload, preferring plain text."""
    parts = payload.get("parts", [])

    if parts:
        # Try plain text first
        for part in parts:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        # Fall back to HTML
        for part in parts:
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    return re.sub(r"<[^>]+>", " ", html)
        # Recurse into nested parts
        for part in parts:
            result = get_body(part)
            if result:
                return result

    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data", "")
    if data:
        text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        if "html" in mime:
            return re.sub(r"<[^>]+>", " ", text)
        return text
    return ""


def fetch_new_standups(service, state):
    """Return list of (msg_id, date_str, body_text) for unprocessed standup emails."""
    results = service.users().messages().list(
        userId="me",
        q="from:gemini-notes@google.com subject:standup",
        maxResults=10,
    ).execute()

    messages = results.get("messages", [])
    processed = set(state.get("processed_ids", []))
    new_msgs = []

    for m in messages:
        if m["id"] in processed:
            continue
        msg = service.users().messages().get(
            userId="me", id=m["id"], format="full"
        ).execute()
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        date_str = headers.get("Date", "")[:22].strip()
        subject = headers.get("Subject", "")
        body = get_body(msg["payload"])
        new_msgs.append((m["id"], date_str, subject, body))

    return new_msgs


def save_email(subject: str, date_str: str, body: str):
    """Save standup email body to workspace emails/ directory."""
    EMAILS_DIR.mkdir(parents=True, exist_ok=True)
    # Extract date from subject like 'Notes: "Marathon Data Standup" Mar 30, 2026'
    date_match = re.search(r"(\w+ \d+,? \d{4})", subject)
    if date_match:
        try:
            dt = datetime.strptime(date_match.group(1), "%b %d, %Y")
            filename = f"standup-{dt.strftime('%Y-%m-%d')}.txt"
        except ValueError:
            filename = f"standup-{date_str[:10].replace('/', '-')}.txt"
    else:
        filename = f"standup-{date_str[:10].replace('/', '-')}.txt"
    filepath = EMAILS_DIR / filename
    if not filepath.exists():
        filepath.write_text(f"Subject: {subject}\nDate: {date_str}\n\n{body}")
        print(f"  Saved email to {filepath}")


def extract_james_todos(body: str) -> list[str]:
    """Extract action items assigned to James from the standup body."""
    todos = []
    # Gemini format: [James Kenaley] Title: description\r\n (may wrap across lines)
    # Grab everything from [James Kenaley] up to the next [Person] or end of section
    pattern = re.compile(
        r"\[James Kenaley\]\s+(.*?)(?=\n\[|\Z)",
        re.IGNORECASE | re.DOTALL
    )
    for match in pattern.finditer(body):
        item = match.group(1).strip().replace("\r\n", " ").replace("\n", " ")
        item = re.sub(r"\s+", " ", item).strip().rstrip(".")
        # Trim footer noise that sometimes bleeds in
        for stop in ["Meeting records", "Document Notes", "Google LLC", "Is the Next Steps"]:
            if stop in item:
                item = item[:item.index(stop)].strip().rstrip(".")
        if item and len(item) > 3:
            todos.append(item)
    return todos


def extract_summary(body: str) -> str:
    """Pull the Summary section from the standup body."""
    match = re.search(r"Summary\s+(.*?)(?:Ad and Campaign|Crawler|Connector|Suggested|$)", body, re.DOTALL)
    if match:
        return match.group(1).strip()[:400]
    return ""


def update_todo_md(todos: list[str], date_label: str):
    """Add new todos from standup to TODO.md under This Week."""
    todo_path = WORKSPACE / "TODO.md"
    if not todo_path.exists():
        return
    content = todo_path.read_text()

    new_items = []
    for todo in todos:
        tag = f"`standup {date_label}`"
        line = f"- [ ] **{todo}** {tag}"
        # Don't add duplicates
        if todo[:30].lower() not in content.lower():
            new_items.append(line)

    if not new_items:
        return

    insert_after = "## 📋 This Week"
    if insert_after in content:
        idx = content.index(insert_after) + len(insert_after)
        insert_block = "\n" + "\n".join(new_items)
        content = content[:idx] + insert_block + content[idx:]
        todo_path.write_text(content)
        print(f"  Added {len(new_items)} todos to TODO.md")


def update_daily_note(date_str: str, summary: str, todos: list[str]):
    """Append standup recap to today's Obsidian daily note."""
    today = datetime.now().strftime("%Y-%m-%d")
    note_path = VAULT / "📋 Daily Notes" / f"{today}.md"
    if not note_path.exists():
        return

    content = note_path.read_text()
    if "## 🤖 Standup Recap" in content:
        return  # Already updated

    recap = f"\n## 🤖 Standup Recap\n*{date_str}*\n\n"
    if summary:
        recap += f"{summary}\n\n"
    if todos:
        recap += "**Your action items:**\n"
        for t in todos:
            recap += f"- [ ] {t}\n"

    note_path.write_text(content + recap)
    print(f"  Updated daily note: {note_path.name}")


def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Checking for standup recaps...")
    service = get_service()
    state = load_state()

    new_msgs = fetch_new_standups(service, state)
    if not new_msgs:
        print("  No new standup emails.")
        return

    for msg_id, date_str, subject, body in new_msgs:
        print(f"  Processing: {subject} ({date_str})")

        todos = extract_james_todos(body)
        summary = extract_summary(body)
        date_label = datetime.now().strftime("%m/%d")

        print(f"  Found {len(todos)} action items for James")
        for t in todos:
            print(f"    - {t}")

        # Save email body to workspace
        save_email(subject, date_str, body)

        update_todo_md(todos, date_label)
        update_daily_note(date_str, summary, todos)

        state.setdefault("processed_ids", []).append(msg_id)
        # Keep only last 50
        state["processed_ids"] = state["processed_ids"][-50:]

    save_state(state)
    print("  Done.")


if __name__ == "__main__":
    main()
