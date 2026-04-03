#!/usr/bin/env python3
"""
slack_todo_scan.py — Scan recent Slack messages for action items directed at James
and surface them for the agent to review and add to TODO.md.

Outputs a JSON list of candidate todos to stdout.
The agent (Jeff) decides what's worth adding.

Usage: python3 slack_todo_scan.py [--hours 1]
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

MESSAGES_DIR = Path.home() / ".openclaw" / "workspace" / "slack_messages"
STATE_FILE = MESSAGES_DIR / ".todo_scan_state.json"
USERS_CACHE = MESSAGES_DIR / ".users_cache.json"

# James's Slack user IDs (will be detected from messages where he's sender)
# We also look for mentions of "james" or the bot
JAMES_KEYWORDS = ["james", "jkenaley", "kenaley"]

# Signal phrases that suggest something is actionable for James
ACTION_SIGNALS = [
    "can you", "could you", "would you", "will you",
    "please", "need you to", "want you to",
    "hey james", "hi james", "@james",
    "follow up", "follow-up", "followup",
    "don't forget", "dont forget", "remember to",
    "lmk", "let me know",
    "waiting on you", "waiting on james",
    "action item", "todo", "to-do", "to do",
    "when you get a chance",
    "asap", "urgent", "important",
    "can we", "should we", "we need to",
    "heads up",
]

# Noise channels to skip (automated alerts, logs, etc.)
SKIP_CHANNELS = {
    "connectors-health", "pipeline-success", "pipeline-fail",
    "pipeline-logs", "prod-pipeline-logs", "prod-pipeline-fail",
    "prod-pipeline-success", "stage-notifications", "sentry-alerts",
    "deployments", "airbyte-webhook-notifications", "airbyte-job-failures",
    "airbyte-stuck-jobs", "airbyte-connection-tracking",
    "prod-elementary-reports", "stage-elementary-reports",
    "email-logs", "stipe-logs", "data-elementary", "data-airflow",
    "linkedin-videos", "weekly-customer-stories",
}

# Client channels (marathon-{client}) require a direct @mention of James to be actionable.
# Without an explicit mention, messages there are team-wide and not James's task.
CLIENT_CHANNEL_PREFIX = "marathon-"


def load_users():
    if USERS_CACHE.exists():
        with open(USERS_CACHE) as f:
            return json.load(f)
    return {}


def load_scan_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_scan_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def is_actionable(text: str, channel_name: str, users: dict, james_id: str) -> bool:
    """Quick heuristic check — final decision made by agent."""
    text_lower = text.lower()
    is_direct_mention = james_id and f"<@{james_id}>" in text

    # Client channels (marathon-*): ONLY actionable if James is directly @mentioned
    if channel_name.startswith(CLIENT_CHANNEL_PREFIX):
        return is_direct_mention

    # Direct mention of James anywhere else
    if is_direct_mention:
        return True

    # Action signal phrases (non-client channels)
    for signal in ACTION_SIGNALS:
        if signal in text_lower:
            return True

    # In a DM channel — almost always worth surfacing
    if channel_name.startswith("dm_"):
        return len(text.strip()) > 10

    return False


def detect_james_id(messages_dir: Path) -> str:
    """Try to detect James's Slack user ID from sent messages."""
    # Look for a pattern where the user field matches across multiple DMs
    # We can infer this from the dm_ files — messages sent by the same user
    id_counts = {}
    for jsonl in list(messages_dir.glob("dm_U*.jsonl"))[:20]:
        with open(jsonl) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    uid = msg.get("user")
                    if uid and uid.startswith("U"):
                        id_counts[uid] = id_counts.get(uid, 0) + 1
                except Exception:
                    pass
    # The most frequent sender across DMs is likely James
    if id_counts:
        return max(id_counts, key=id_counts.get)
    return ""


def scan_recent(hours: float = 1.0) -> list:
    """Scan messages from last N hours for potential action items."""
    users = load_users()
    scan_state = load_scan_state()
    james_id = scan_state.get("james_id") or detect_james_id(MESSAGES_DIR)
    if james_id:
        scan_state["james_id"] = james_id

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
    candidates = []

    for jsonl in sorted(MESSAGES_DIR.glob("*.jsonl")):
        channel_name = jsonl.stem

        # Skip noise channels
        if channel_name in SKIP_CHANNELS:
            continue
        if channel_name.startswith("."):
            continue

        # Track last processed ts per channel
        last_ts = float(scan_state.get("channels", {}).get(channel_name, {}).get("last_ts", 0))

        new_last_ts = last_ts
        with open(jsonl) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    ts = float(msg.get("ts", 0))

                    # Only look at new messages since last scan
                    if ts <= last_ts:
                        continue

                    if ts > new_last_ts:
                        new_last_ts = ts

                    # Skip messages James sent himself (we want things directed AT him)
                    if msg.get("user") == james_id:
                        continue

                    # Skip bot messages
                    if msg.get("subtype") in ("bot_message", "channel_join", "channel_leave"):
                        continue

                    text = msg.get("text", "").strip()
                    if not text or len(text) < 5:
                        continue

                    if is_actionable(text, channel_name, users, james_id):
                        sender_id = msg.get("user", "")
                        sender_name = users.get(sender_id, sender_id) or sender_id
                        ts_dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

                        candidates.append({
                            "channel": channel_name,
                            "sender": sender_name,
                            "sender_id": sender_id,
                            "ts": ts,
                            "ts_human": ts_dt,
                            "text": text[:500],  # truncate very long messages
                        })
                except Exception:
                    pass

        # Update state
        if "channels" not in scan_state:
            scan_state["channels"] = {}
        if channel_name not in scan_state["channels"]:
            scan_state["channels"][channel_name] = {}
        if new_last_ts > last_ts:
            scan_state["channels"][channel_name]["last_ts"] = str(new_last_ts)

    save_scan_state(scan_state)

    # Sort by timestamp, most recent first
    candidates.sort(key=lambda x: x["ts"], reverse=True)
    return candidates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=1.0, help="Hours to look back")
    parser.add_argument("--all", action="store_true", help="Scan all stored messages (ignores last-scan state)")
    args = parser.parse_args()

    if args.all:
        # Reset scan state to re-process everything
        if STATE_FILE.exists():
            STATE_FILE.unlink()

    candidates = scan_recent(hours=args.hours if not args.all else 24 * 14)
    print(json.dumps(candidates, indent=2))


if __name__ == "__main__":
    main()
