#!/usr/bin/env python3
"""
sync_meeting_transcripts.py — Pull ALL meeting transcriptions/summaries from Gmail
and save them locally. Supports Gemini Notes, Grain, Fireflies, Otter, Fathom, etc.

Usage: python3 sync_meeting_transcripts.py [--full]
  --full: Re-scan everything (ignore state, don't re-download existing files)
  default: Incremental (only fetch emails newer than last run)
"""

import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

WORKSPACE = Path.home() / ".openclaw/workspace"
EMAILS_DIR = WORKSPACE / "transcriptions"
STATE_FILE = WORKSPACE / "memory/transcript-sync-state.json"
GOG = Path.home() / ".local/bin/gog"
ACCOUNT = os.environ.get("GOG_ACCOUNT", "user@company.com")  # Set your Google account

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
    # Try parsing gog format: "2026-04-02 16:17"
    match = re.match(r"(\d{4}-\d{2}-\d{2})", date_str)
    if match:
        return match.group(1)
    return datetime.now().strftime("%Y-%m-%d")


def extract_meeting_name(subject: str) -> str:
    """Pull meeting name from subject line."""
    # Gemini: Notes: "Marathon Data Standup" Mar 20, 2026
    m = re.search(r'["""](.+?)["""]', subject)
    if m:
        return m.group(1)
    
    # Grain: Meeting summary: "Quick Sync on Onboarding Flow"
    m = re.search(r'summary:\s*["""]?(.+?)["""]?\s*$', subject, re.IGNORECASE)
    if m:
        return m.group(1).strip('""\' ')
    
    # Fallback: strip common prefixes
    for prefix in ["Notes:", "Meeting summary:", "Recording ready:", "Meeting notes:"]:
        if subject.startswith(prefix):
            return subject[len(prefix):].strip().strip('""\' ')
    
    return subject


def parse_thread_content(raw_json: str) -> str:
    """Extract readable text from gog gmail read --json output."""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return raw_json  # Return raw if not JSON
    
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


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"downloaded_ids": [], "last_run": None}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Keep last 500 IDs
    state["downloaded_ids"] = state["downloaded_ids"][-500:]
    state["last_run"] = datetime.now().isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2))


def main():
    full_mode = "--full" in sys.argv
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Syncing meeting transcripts...")
    EMAILS_DIR.mkdir(parents=True, exist_ok=True)
    
    state = load_state()
    downloaded = set(state.get("downloaded_ids", []))
    
    # Collect all threads from all queries
    all_threads = {}  # id -> thread dict
    for query in QUERIES:
        print(f"  Searching: {query}")
        threads = gog_search(query, MAX_PER_QUERY)
        for t in threads:
            all_threads[t["id"]] = t
        print(f"    Found {len(threads)} threads")
    
    print(f"\n  Total unique threads: {len(all_threads)}")
    
    # Filter to unprocessed
    to_process = {tid: t for tid, t in all_threads.items() if tid not in downloaded or full_mode}
    print(f"  New threads to download: {len(to_process)}")
    
    saved = 0
    errors = 0
    
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
        
        # Skip if file already exists (even in full mode)
        if filepath.exists():
            downloaded.add(tid)
            continue
        
        print(f"  Downloading: {subject}")
        raw = gog_read(tid)
        if not raw:
            errors += 1
            continue
        
        content = parse_thread_content(raw)
        filepath.write_text(content)
        downloaded.add(tid)
        saved += 1
        print(f"    -> {filename}")
    
    state["downloaded_ids"] = list(downloaded)
    save_state(state)
    
    print(f"\n  Done. Saved {saved} new transcripts, {errors} errors.")
    if saved > 0:
        print(f"  Files in {EMAILS_DIR}")


if __name__ == "__main__":
    main()
