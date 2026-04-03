#!/usr/bin/env python3
"""
honcho_slack_sync.py — Push locally-synced Slack messages into Honcho memory.

Reads JSONL exports from slack_sync.py and loads them into Honcho as:
  - Slack channels/DMs -> Honcho sessions
  - Slack users -> Honcho peers
  - Messages -> Honcho messages (with metadata, thread info, timestamps)

Tracks sync state to avoid re-sending messages.
Usage: python3 honcho_slack_sync.py [--dry-run] [--verbose] [--reset]
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from honcho import Honcho
except ImportError:
    print("ERROR: honcho-ai not installed. Run: pip3 install honcho-ai")
    sys.exit(1)

WORKSPACE = Path.home() / ".openclaw" / "workspace"
MESSAGES_DIR = WORKSPACE / "slack_messages"
SYNC_STATE_FILE = MESSAGES_DIR / ".honcho_sync_state.json"
USERS_CACHE = MESSAGES_DIR / ".users_cache.json"
CHANNELS_META = MESSAGES_DIR / "_channels.json"

HONCHO_BASE_URL = "http://localhost:18790"
HONCHO_WORKSPACE = "openclaw"

BATCH_SIZE = 100  # Honcho max per request
PEER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

# Known bot/service Slack user IDs. observe_me=false + reasoning disabled on their messages.
BOT_UIDS = {
    "U05R5F8CL66", "U05RSPHRANB", "U05TSNUD62H", "U05UM0LA31P",
    "U05UZSV40GP", "U061FENT6LC", "U061LT2RJG4", "U0627HFQF7A",
    "U063EQK072P", "U065J646Y8G", "U066SC5M6DD", "U06G4QE60HW",
    "U06JVPGGDR8", "U06K93ETKBJ", "U06PTFS7AQZ", "U06UE3LBYM6",
    "U074XK2KHA7", "U077AT77BHB", "U077DMYCBSP", "U07G04LRQ93",
    "U07PB67HV7E", "U07RP4JHRM2", "U07T8D9EZGT", "U07T98XNYA3",
    "U07U1JC7YAE", "U07UNN426QJ", "U07V1CZMJCR", "U084N1JAZFC",
    "U087V78RTNU", "U088Y2U808K", "U08BD2VKL90", "U08F8AV3407",
    "U08K1HLE872", "U08T1J0GEBH", "U09744MTSJK", "U09CKMKT1S8",
    "U09J18CN6MQ", "U09P1HM2HJ8", "U09PVPWQRM4", "U09SGV6P695",
    "U09V8V4DFGX", "U0A06MF6Q75", "U0A06MKP9EF", "U0A06MUS0RH",
    "U0A0706BW4D", "U0A09KQA16Z", "U0A09KUSN0M", "U0A0ACGJVA6",
    "U0A0CKX2REG", "U0A0E0BUNRJ", "U0A0GLME78C", "U0A14N4A64Q",
    "U0A17C6949W", "U0A17D6SA6L", "U0ABUQG4MKM", "U0ACSP1DM6E",
    "U0ADQA0KR1P", "U0AK9KHKYK1", "U0AP6CCS6F7", "U0APV7TV5M5",
    "U0529QJ71A8", "U0529QZA65A", "U0574RW9SRH", "U058HC31EQJ",
    "U087R1USW56", "U093H7DB27R", "U0A9F049U58", "USLACKBOT",
}

# Channels to skip (bot-heavy, CI/CD noise). Names match JSONL filenames (without .jsonl).
EXCLUDE_CHANNELS = {
    "prod-pipeline-success",
    "prod-pipeline-logs",
    "prod-pipeline-fail",
    "deployments",
    "pull-requests",
    "airbyte-connection-tracking",
    "airbyte-job-failures",
    "airbyte-stuck-jobs",
    "airbyte-webhook-notifications",
    "connectors-health",
    "eng-triage",
    "dm_USLACKBOT",
    "dm_U05R5F8CL66",    # Google Calendar bot
    "dm_U05RSPHRANB",    # GitHub notifications
    "dm_U06K93ETKBJ",    # Linear notifications
    "dm_U088Y2U808K",    # Reclaim bot
}


def load_json(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_json(path: Path, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def sanitize_peer_id(slack_uid: str) -> str:
    """Convert Slack user ID to a valid Honcho peer ID."""
    pid = re.sub(r"[^A-Za-z0-9_-]+", "-", slack_uid).strip("-").lower()
    if not pid or not PEER_ID_PATTERN.fullmatch(pid):
        return f"slack-{slack_uid}"
    return pid


def load_new_messages(jsonl_path: Path, since_ts: float) -> list[dict]:
    """Load messages from JSONL that were synced after since_ts."""
    msgs = []
    if not jsonl_path.exists():
        return msgs
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                synced_at = msg.get("_synced_at", 0)
                if synced_at > since_ts:
                    msgs.append(msg)
            except Exception:
                pass
    # Sort by Slack timestamp (chronological order)
    msgs.sort(key=lambda m: float(m.get("ts", 0)))
    return msgs


def main():
    parser = argparse.ArgumentParser(description="Push Slack messages to Honcho memory")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be sent without writing")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output")
    parser.add_argument("--reset", action="store_true", help="Reset sync state and re-send all messages")
    parser.add_argument("--base-url", default=HONCHO_BASE_URL, help=f"Honcho API URL (default: {HONCHO_BASE_URL})")
    parser.add_argument("--workspace", default=HONCHO_WORKSPACE, help=f"Honcho workspace (default: {HONCHO_WORKSPACE})")
    args = parser.parse_args()

    if not MESSAGES_DIR.exists():
        print("ERROR: No slack_messages directory. Run slack_sync.py first.")
        sys.exit(1)

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting Honcho Slack sync...")

    # Load supporting data
    user_cache = load_json(USERS_CACHE)
    channel_meta = load_json(CHANNELS_META)
    sync_state = {} if args.reset else load_json(SYNC_STATE_FILE)

    if args.reset:
        print("  Reset: re-sending all messages")

    # Collect JSONL files
    jsonl_files = sorted(MESSAGES_DIR.glob("*.jsonl"))
    if not jsonl_files:
        print("No message files found.")
        return

    # Scan for new messages across all channels
    channels_to_sync = {}
    skipped_excluded = 0
    for jsonl_path in jsonl_files:
        channel_name = jsonl_path.stem
        if channel_name in EXCLUDE_CHANNELS:
            skipped_excluded += 1
            continue
        last_sync = sync_state.get(channel_name, {}).get("last_synced_at", 0)
        new_msgs = load_new_messages(jsonl_path, last_sync)
        if new_msgs:
            channels_to_sync[channel_name] = new_msgs

    if skipped_excluded and args.verbose:
        print(f"Skipped {skipped_excluded} excluded channels (bot/CI noise)")

    if not channels_to_sync:
        print("No new messages to sync.")
        save_json(SYNC_STATE_FILE, sync_state)
        return

    total_msgs = sum(len(m) for m in channels_to_sync.values())
    print(f"Found {total_msgs} new messages across {len(channels_to_sync)} channels")

    if args.dry_run:
        for ch_name, msgs in channels_to_sync.items():
            print(f"  #{ch_name}: {len(msgs)} messages")
            if args.verbose:
                for m in msgs[:3]:
                    uid = m.get("user", "unknown")
                    display = user_cache.get(uid, uid)
                    text = (m.get("text") or "")[:80].replace("\n", " ")
                    print(f"    [{display}] {text}")
                if len(msgs) > 3:
                    print(f"    ... and {len(msgs) - 3} more")
        print(f"\n[DRY RUN] Would send {total_msgs} messages to Honcho.")
        return

    # Connect to Honcho
    honcho = Honcho(base_url=args.base_url, workspace_id=args.workspace)

    # Build peer registry (create once, reuse)
    seen_peers = {}

    def get_or_create_peer(slack_uid: str):
        if slack_uid in seen_peers:
            return seen_peers[slack_uid]
        display_name = user_cache.get(slack_uid, slack_uid)
        peer_id = sanitize_peer_id(slack_uid)
        is_bot = slack_uid in BOT_UIDS
        config = {"observe_me": False} if is_bot else None
        peer = honcho.peer(peer_id, metadata={
            "slack_uid": slack_uid,
            "display_name": display_name,
            "source": "slack",
            "is_bot": is_bot,
        }, configuration=config)
        seen_peers[slack_uid] = peer
        return peer

    # Process each channel
    total_sent = 0
    errors = 0
    for ch_name, msgs in channels_to_sync.items():
        # Build session metadata from channel info
        ch_id = msgs[0].get("_channel_id", "")
        meta_entry = channel_meta.get(ch_id, {})
        session_id = f"slack-{ch_name}"
        session_meta = {
            "source": "slack",
            "slack_channel_id": ch_id,
            "channel_name": ch_name,
        }
        if meta_entry.get("topic"):
            session_meta["topic"] = meta_entry["topic"]
        if meta_entry.get("purpose"):
            session_meta["purpose"] = meta_entry["purpose"]
        if meta_entry.get("is_im"):
            session_meta["type"] = "dm"
        elif meta_entry.get("is_mpim"):
            session_meta["type"] = "group_dm"
        elif meta_entry.get("is_private"):
            session_meta["type"] = "private_channel"
        else:
            session_meta["type"] = "public_channel"

        session = honcho.session(session_id, metadata=session_meta)

        # Collect unique peers in this channel and add to session
        channel_uids = {m.get("user") for m in msgs if m.get("user")}
        channel_peers = [get_or_create_peer(uid) for uid in channel_uids]
        if channel_peers:
            try:
                session.add_peers(channel_peers)
            except Exception as e:
                if args.verbose:
                    print(f"  Note: add_peers for #{ch_name}: {e}")

        # Build Honcho messages
        honcho_msgs = []
        for m in msgs:
            uid = m.get("user")
            if not uid:
                continue
            text = m.get("text", "")
            if not text:
                continue

            peer = get_or_create_peer(uid)
            msg_meta = {"slack_ts": m.get("ts", "")}
            if m.get("thread_ts") and m.get("thread_ts") != m.get("ts"):
                msg_meta["thread_ts"] = m["thread_ts"]
            if m.get("subtype"):
                msg_meta["subtype"] = m["subtype"]
            if m.get("reactions"):
                msg_meta["reactions"] = m["reactions"]

            ts_float = float(m.get("ts", 0))
            created = datetime.fromtimestamp(ts_float, tz=timezone.utc) if ts_float else None

            # Disable reasoning on bot messages and low-value content
            is_bot = uid in BOT_UIDS
            is_noise = (
                is_bot
                or m.get("subtype") in ("channel_join", "channel_leave", "channel_topic", "channel_purpose")
                or (len(text) < 5 and not text.strip().isalpha())  # emoji-only, link-only
            )
            msg_config = {"reasoning": {"enabled": False}} if is_noise else None

            honcho_msgs.append(peer.message(
                text,
                metadata=msg_meta,
                configuration=msg_config,
                created_at=created,
            ))

        # Send in batches
        if honcho_msgs:
            sent = 0
            try:
                for i in range(0, len(honcho_msgs), BATCH_SIZE):
                    batch = honcho_msgs[i:i + BATCH_SIZE]
                    session.add_messages(batch)
                    sent += len(batch)
                    if i + BATCH_SIZE < len(honcho_msgs):
                        time.sleep(0.5)
                total_sent += sent
                print(f"  #{ch_name}: +{sent} messages")
            except Exception as e:
                errors += 1
                print(f"  #{ch_name}: ERROR sending messages: {e}")

        # Update sync state for this channel
        max_synced_at = max(m.get("_synced_at", 0) for m in msgs)
        if ch_name not in sync_state:
            sync_state[ch_name] = {}
        sync_state[ch_name]["last_synced_at"] = max_synced_at
        sync_state[ch_name]["last_run"] = time.time()

    save_json(SYNC_STATE_FILE, sync_state)
    print(f"Sync complete. Sent {total_sent} messages to Honcho, {errors} errors.")


if __name__ == "__main__":
    main()
