#!/usr/bin/env python3
"""
slack_sync.py — Fetch recent Slack messages using user token and store locally.
Stores messages per channel in JSONL files under:
  ~/.openclaw/workspace/slack_messages/<channel_name>.jsonl

Includes thread replies, channel metadata, and deduplication.
Retention: deletes messages older than 14 days.
Usage: python3 slack_sync.py [--token xoxp-...] [--hours 24] [--verbose] [--skip-threads]
"""

import sys
import json
import time
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

from shared import MESSAGES_DIR, load_json, save_json, atomic_write_text, script_lock, get_secret, get_ssl_context
from config import PRIORITY_CHANNELS, PRIORITY_DM_USERS

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError:
    print("ERROR: slack_sdk not installed. Run: pip3 install slack-sdk --break-system-packages")
    sys.exit(1)

STATE_FILE = MESSAGES_DIR / ".sync_state.json"
RETENTION_DAYS = 14
PRUNE_INTERVAL_HOURS = 24


def get_token():
    """Try to get user token (keychain → env → openclaw.json)."""
    token = get_secret("SLACK_USER_TOKEN")
    if token:
        return token
    try:
        config_path = Path.home() / ".openclaw" / "openclaw.json"
        with open(config_path) as f:
            for line in f:
                line = line.strip()
                if '"userToken"' in line:
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        val = parts[1].strip().strip('",').strip()
                        if val.startswith("xoxp-"):
                            return val
    except Exception:
        pass
    return ""


def load_state():
    return load_json(STATE_FILE)


def save_state(state):
    save_json(STATE_FILE, state)


def slack_api_call_with_retry(fn, max_retries=2, **kwargs):
    """Call a Slack API method with retries on rate limit and transient errors."""
    for attempt in range(max_retries + 1):
        try:
            return fn(**kwargs)
        except SlackApiError as e:
            error = e.response.get("error", "")
            if error == "ratelimited":
                retry_after = int(e.response.headers.get("Retry-After", 5))
                print(f"    Rate limited, waiting {retry_after}s...")
                time.sleep(retry_after)
                continue
            status = getattr(e.response, 'status_code', 0)
            if status >= 500 and attempt < max_retries:
                print(f"    Server error ({status}), retrying in {2 ** attempt}s...")
                time.sleep(2 ** attempt)
                continue
            raise


def load_existing_ts(jsonl_path: Path) -> set:
    """Load existing message timestamps from JSONL for dedup."""
    ts_set = set()
    if not jsonl_path.exists():
        return ts_set
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                ts_set.add(msg.get("ts"))
            except Exception:
                pass
    return ts_set


def append_messages(jsonl_path: Path, messages: list, channel_id: str,
                    channel_name: str, existing_ts: set):
    """Append new messages to JSONL file, deduplicating on ts."""
    new_msgs = [m for m in messages if m.get("ts") not in existing_ts]
    if not new_msgs:
        return 0
    with open(jsonl_path, "a") as f:
        for msg in new_msgs:
            msg["_channel_id"] = channel_id
            msg["_channel_name"] = channel_name
            msg["_synced_at"] = time.time()
            f.write(json.dumps(msg) + "\n")
    return len(new_msgs)


def prune_old_messages(jsonl_path: Path):
    """Remove messages older than RETENTION_DAYS from a JSONL file."""
    if not jsonl_path.exists():
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).timestamp()
    kept = []
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                ts = float(msg.get("ts", 0))
                if ts >= cutoff:
                    kept.append(line)
            except Exception:
                pass
    content = "\n".join(kept) + ("\n" if kept else "")
    atomic_write_text(jsonl_path, content)


def should_prune(state: dict) -> bool:
    """Check if we should run pruning (once per day instead of every sync)."""
    last_prune = state.get("_last_prune_ts", 0)
    return (time.time() - last_prune) > (PRUNE_INTERVAL_HOURS * 3600)


def prune_all(state: dict):
    """Prune old messages from all JSONL files."""
    print("Pruning old messages...")
    for jsonl in MESSAGES_DIR.glob("*.jsonl"):
        prune_old_messages(jsonl)
    state["_last_prune_ts"] = time.time()


def fetch_thread_replies(client, channel_id: str, thread_ts: str) -> list:
    """Fetch all replies in a thread (excluding the parent message)."""
    replies = []
    cursor = None
    try:
        while True:
            kwargs = {"channel": channel_id, "ts": thread_ts, "limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            resp = slack_api_call_with_retry(client.conversations_replies, **kwargs)
            for m in resp.get("messages", []):
                if m.get("ts") != thread_ts:
                    replies.append(m)
            if not resp.get("has_more"):
                break
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
            time.sleep(0.3)
    except SlackApiError as e:
        print(f"    Thread error ({thread_ts}): {e.response.get('error', 'unknown')}")
    return replies


def resolve_users_bulk(client, messages_dir: Path) -> dict:
    """Fetch all workspace users via users.list. Cached for 24h."""
    cache_path = messages_dir / ".users_cache.json"
    cache = {}
    if cache_path.exists():
        with open(cache_path) as f:
            cache = json.load(f)

    last_updated = cache.get("_cache_updated_at", 0)
    if time.time() - last_updated < 86400:
        return cache

    print("Refreshing user cache (bulk)...")
    cursor = None
    try:
        while True:
            kwargs = {"limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            resp = slack_api_call_with_retry(client.users_list, **kwargs)
            for user in resp.get("members", []):
                uid = user.get("id")
                if not uid:
                    continue
                profile = user.get("profile", {})
                cache[uid] = (
                    profile.get("display_name")
                    or profile.get("real_name")
                    or uid
                )
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
            time.sleep(0.3)
    except SlackApiError as e:
        print(f"  User list error: {e.response.get('error', 'unknown')}")

    cache["_cache_updated_at"] = time.time()
    with open(cache_path, "w") as f:
        json.dump(cache, f, indent=2)
    return cache


def save_channel_metadata(channels: list, messages_dir: Path):
    """Store channel metadata (topic, purpose, member count)."""
    meta = {}
    for ch in channels:
        ch_id = ch["id"]
        meta[ch_id] = {
            "name": ch.get("name") or ch.get("name_normalized") or ch_id,
            "topic": (ch.get("topic") or {}).get("value", ""),
            "purpose": (ch.get("purpose") or {}).get("value", ""),
            "num_members": ch.get("num_members", 0),
            "is_im": ch.get("is_im", False),
            "is_mpim": ch.get("is_mpim", False),
            "is_private": ch.get("is_private", False),
        }
    with open(messages_dir / "_channels.json", "w") as f:
        json.dump(meta, f, indent=2)


def get_channel_display_name(channel: dict, user_cache: dict) -> str:
    """Human-readable channel name. Resolves DM user IDs to display names."""
    if channel.get("is_im"):
        user_id = channel.get("user", "")
        display = user_cache.get(user_id, "")
        if display and display != user_id:
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in display)
            return f"dm_{safe}"
        return f"dm_{user_id}"
    return channel.get("name") or channel.get("name_normalized") or channel["id"]


def migrate_dm_filename(channel: dict, user_cache: dict):
    """Rename old dm_USERID.jsonl to dm_DisplayName.jsonl if needed."""
    if not channel.get("is_im"):
        return
    user_id = channel.get("user", "")
    old_path = MESSAGES_DIR / f"dm_{user_id}.jsonl"
    new_name = get_channel_display_name(channel, user_cache)
    new_path = MESSAGES_DIR / f"{new_name}.jsonl"
    if old_path.exists() and not new_path.exists() and old_path != new_path:
        old_path.rename(new_path)
        print(f"  Renamed {old_path.name} -> {new_path.name}")


def sync_channel(client, channel: dict, state: dict, user_cache: dict,
                 fetch_hours: int = 24, verbose: bool = False,
                 skip_threads: bool = False):
    """Sync a channel including thread replies. Returns (status, msg_count)."""
    channel_id = channel["id"]
    channel_name = get_channel_display_name(channel, user_cache)
    jsonl_path = MESSAGES_DIR / f"{channel_name}.jsonl"

    # Migrate old DM filenames
    migrate_dm_filename(channel, user_cache)

    stored_ts = state.get(channel_id, {}).get("latest_ts")
    if stored_ts:
        oldest = float(stored_ts)
    else:
        oldest = (datetime.now(timezone.utc) - timedelta(hours=fetch_hours)).timestamp()

    print(f"  Syncing #{channel_name} (since {datetime.fromtimestamp(oldest, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')})")

    existing_ts = load_existing_ts(jsonl_path)

    all_messages = []
    cursor = None
    latest_ts = oldest

    try:
        while True:
            kwargs = {
                "channel": channel_id,
                "oldest": str(oldest),
                "limit": 200,
            }
            if cursor:
                kwargs["cursor"] = cursor

            resp = slack_api_call_with_retry(
                client.conversations_history, **kwargs
            )
            msgs = resp.get("messages", [])
            all_messages.extend(msgs)

            for m in msgs:
                ts = float(m.get("ts", 0))
                if ts > latest_ts:
                    latest_ts = ts

            if not resp.get("has_more"):
                break
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
            time.sleep(0.5)

    except SlackApiError as e:
        if e.response["error"] == "not_in_channel":
            print("    Skipping (not a member)")
            return ("error", 0)
        print(f"    Error: {e.response['error']}")
        return ("error", 0)

    # Fetch thread replies for new messages with replies
    thread_messages = []
    if not skip_threads:
        threaded = [
            m for m in all_messages
            if int(m.get("reply_count", 0)) > 0
            and m.get("ts") not in existing_ts
        ]
        if threaded:
            print(f"    Fetching replies for {len(threaded)} threads...")
            for m in threaded:
                replies = fetch_thread_replies(client, channel_id, m["ts"])
                thread_messages.extend(replies)
                time.sleep(0.3)

    combined = all_messages + thread_messages
    added = append_messages(jsonl_path, combined, channel_id, channel_name, existing_ts)

    if added:
        thread_note = f" ({len(thread_messages)} from threads)" if thread_messages else ""
        print(f"    +{added} messages{thread_note}")

    # Update state
    if channel_id not in state:
        state[channel_id] = {}
    if latest_ts > oldest:
        state[channel_id]["latest_ts"] = str(latest_ts)
    state[channel_id]["name"] = channel_name
    state[channel_id]["last_check_ts"] = time.time()

    return ("synced", added)


def main():
    parser = argparse.ArgumentParser(description="Sync Slack messages locally")
    parser.add_argument("--token", help="Slack user token (xoxp-...)")
    parser.add_argument("--hours", type=int, default=24,
                        help="Hours to look back on first run (default: 24)")
    parser.add_argument("--verbose", action="store_true",
                        help="Show skipped channels in output")
    parser.add_argument("--skip-threads", action="store_true",
                        help="Skip fetching thread replies (faster)")
    args = parser.parse_args()

    with script_lock("slack_sync"):
        token = args.token or get_token()
        if not token or not token.startswith("xoxp-"):
            print("ERROR: No valid user token found. Set SLACK_USER_TOKEN or pass --token")
            sys.exit(1)

        MESSAGES_DIR.mkdir(parents=True, exist_ok=True)

        client = WebClient(token=token, ssl=get_ssl_context())
        state = load_state()

        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting Slack sync...")

        # 1. Build user cache (bulk fetch via users.list, cached 24h)
        user_cache = resolve_users_bulk(client, MESSAGES_DIR)

        # 2. Get all channels
        channels = []
        cursor = None
        while True:
            resp = slack_api_call_with_retry(
                client.conversations_list,
                types="public_channel,private_channel,im,mpim",
                exclude_archived=True,
                limit=200,
                cursor=cursor
            )
            channels.extend(resp.get("channels", []))
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
            time.sleep(0.3)

        member_channels = [
            c for c in channels
            if c.get("is_member") or c.get("is_im") or c.get("is_mpim")
        ]
        print(f"Found {len(member_channels)} channels/DMs total")

        # 3. Save channel metadata (topic, purpose, member count)
        save_channel_metadata(member_channels, MESSAGES_DIR)

        # 4. Smart filtering: detect channels with new messages.
        # For regular channels: use `updated` (ms timestamp) vs our last_check.
        # For DMs: Slack's `updated` field is BROKEN (can be years stale even with
        # recent messages). Always sync priority DM users; skip other DMs using
        # `updated` as a best-effort heuristic (it works for some, not all).
        active_channels = []
        pre_skipped = 0
        for ch in member_channels:
            ch_id = ch["id"]
            is_priority = ch_id in PRIORITY_CHANNELS
            is_dm = ch.get("is_im")
            is_mpim = ch.get("is_mpim")
            is_priority_dm = is_dm and ch.get("user") in PRIORITY_DM_USERS

            stored = state.get(ch_id, {})
            updated_ms = ch.get("updated", 0)
            last_check_ms = int(stored.get("last_check_ts", 0) * 1000)

            if is_priority or is_priority_dm or is_mpim:
                # Always sync: priority channels, priority DMs, group DMs
                active_channels.append(ch)
            elif is_dm:
                # Non-priority DMs: use `updated` but with a generous staleness
                # window. If we haven't checked in 6+ hours, sync anyway.
                hours_since_check = (time.time() - stored.get("last_check_ts", 0)) / 3600
                if hours_since_check > 6:
                    active_channels.append(ch)
                elif updated_ms and last_check_ms and updated_ms <= last_check_ms:
                    pre_skipped += 1
                else:
                    active_channels.append(ch)
            else:
                # Regular channels: `updated` is generally reliable
                if updated_ms and last_check_ms and updated_ms <= last_check_ms:
                    pre_skipped += 1
                    if args.verbose:
                        name = ch.get("name") or ch_id
                        print(f"  Pre-skip #{name} (updated {updated_ms} <= last_check {last_check_ms})")
                else:
                    active_channels.append(ch)

        print(f"Active: {len(active_channels)}, pre-skipped: {pre_skipped}")

        # 5. Sync active channels
        synced = 0
        errors = 0
        total_msgs = 0
        for ch in active_channels:
            status, count = sync_channel(
                client, ch, state, user_cache,
                fetch_hours=args.hours, verbose=args.verbose,
                skip_threads=args.skip_threads,
            )
            if status == "synced":
                synced += 1
                total_msgs += count
            else:
                errors += 1

        # 6. Prune old messages (daily, not every run)
        if should_prune(state):
            prune_all(state)

        save_state(state)
        print(f"Sync complete. {synced} channels synced (+{total_msgs} msgs), "
              f"{pre_skipped} pre-skipped, {errors} errors. "
              f"Stored in {MESSAGES_DIR}")


if __name__ == "__main__":
    main()
