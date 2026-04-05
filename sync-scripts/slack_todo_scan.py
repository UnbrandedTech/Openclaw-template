#!/usr/bin/env python3
"""
slack_todo_scan.py — Scan recent Slack messages for action items directed at the user
and surface them for the agent to review and add to TODO.md.

Outputs a JSON list of candidate todos to stdout.
The agent (Jeff) decides what's worth adding.

Usage: python3 slack_todo_scan.py [--hours 1]
"""

import json
import argparse
from datetime import datetime, timezone

from shared import MESSAGES_DIR, load_json, save_json, script_lock, USER_SLACK_ID, USER_FIRST_NAME
from config import EXCLUDE_CHANNELS, CLIENT_CHANNEL_PREFIX
STATE_FILE = MESSAGES_DIR / ".todo_scan_state.json"
USERS_CACHE = MESSAGES_DIR / ".users_cache.json"

# Signal phrases that suggest something is actionable for the user
ACTION_SIGNALS = [
    "can you", "could you", "would you", "will you",
    "please", "need you to", "want you to",
    f"hey {USER_FIRST_NAME.lower()}", f"hi {USER_FIRST_NAME.lower()}",
    "follow up", "follow-up", "followup",
    "don't forget", "dont forget", "remember to",
    "lmk", "let me know",
    "waiting on you", f"waiting on {USER_FIRST_NAME.lower()}",
    "action item", "todo", "to-do", "to do",
    "when you get a chance",
    "asap", "urgent", "important",
    "can we", "should we", "we need to",
    "heads up",
]


def load_users():
    if USERS_CACHE.exists():
        with open(USERS_CACHE) as f:
            return json.load(f)
    return {}


def load_scan_state():
    return load_json(STATE_FILE)


def save_scan_state(state):
    save_json(STATE_FILE, state)


def is_actionable(text: str, channel_name: str, users: dict, user_id: str) -> bool:
    """Quick heuristic check — final decision made by agent."""
    text_lower = text.lower()
    is_direct_mention = user_id and f"<@{user_id}>" in text

    # Client channels: ONLY actionable if user is directly @mentioned
    if channel_name.startswith(CLIENT_CHANNEL_PREFIX):
        return is_direct_mention

    # Direct mention of user anywhere else
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


def scan_recent(hours: float = 1.0) -> list:
    """Scan messages from last N hours for potential action items."""
    users = load_users()
    scan_state = load_scan_state()
    user_id = USER_SLACK_ID

    candidates = []

    for jsonl in sorted(MESSAGES_DIR.glob("*.jsonl")):
        channel_name = jsonl.stem

        # Skip noise channels
        if channel_name in EXCLUDE_CHANNELS:
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

                    # Skip messages the user sent (we want things directed AT them)
                    if msg.get("user") == user_id:
                        continue

                    # Skip bot messages
                    if msg.get("subtype") in ("bot_message", "channel_join", "channel_leave"):
                        continue

                    text = msg.get("text", "").strip()
                    if not text or len(text) < 5:
                        continue

                    if is_actionable(text, channel_name, users, user_id):
                        sender_id = msg.get("user", "")
                        sender_name = users.get(sender_id, sender_id) or sender_id
                        ts_dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

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

    with script_lock("slack_todo_scan"):
        if args.all:
            # Reset scan state to re-process everything
            if STATE_FILE.exists():
                STATE_FILE.unlink()

        candidates = scan_recent(hours=args.hours if not args.all else 24 * 14)
        print(json.dumps(candidates, indent=2))


if __name__ == "__main__":
    main()
