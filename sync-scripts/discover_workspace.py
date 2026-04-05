#!/usr/bin/env python3
"""
discover_workspace.py — Auto-discover bots, noise channels, and key people
from Slack data. Run after the first full Slack sync to generate config files.

Writes:
  ~/.openclaw/workspace/discovered_bots.json
  ~/.openclaw/workspace/discovered_channels.json
  ~/.openclaw/workspace/team.json (suggested, won't overwrite if exists unless --force)

Usage:
  python3 discover_workspace.py                  # Discover from local Slack data
  python3 discover_workspace.py --force          # Overwrite existing team.json
  python3 discover_workspace.py --slack-token xoxp-...  # Fetch fresh user data from Slack
"""

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from shared import WORKSPACE, MESSAGES_DIR, save_json, load_json, USER_SLACK_ID, sanitize_id, get_secret

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    HAS_SLACK = True
except ImportError:
    HAS_SLACK = False


# ── Slack token resolution ──────────────────────────────────────────────────

def get_token():
    """Try to find a Slack user token (keychain → env → .slack_env → openclaw.json)."""
    # Check keychain and env via get_secret
    token = get_secret("SLACK_USER_TOKEN")
    if token:
        return token
    # Check .slack_env
    env_file = WORKSPACE / ".slack_env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("SLACK_USER_TOKEN="):
                return line.split("=", 1)[1].strip()
    # Check openclaw config
    try:
        config_path = Path.home() / ".openclaw" / "openclaw.json"
        with open(config_path) as f:
            for line in f:
                if '"userToken"' in line:
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        val = parts[1].strip().strip('",').strip()
                        if val.startswith("xoxp-"):
                            return val
    except Exception:
        pass
    return ""


# ── Bot discovery ───────────────────────────────────────────────────────────

def discover_bots_from_api(token: str) -> dict:
    """Fetch all Slack users and identify bots."""
    if not HAS_SLACK:
        print("  slack-sdk not installed, skipping API bot discovery")
        return {"bot_uids": [], "bot_patterns": [], "bot_details": {}}

    client = WebClient(token=token)
    bot_uids = []
    bot_names = []
    bot_details = {}
    cursor = None

    print("  Fetching Slack users...")
    try:
        while True:
            kwargs = {"limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            resp = client.users_list(**kwargs)
            for user in resp.get("members", []):
                uid = user.get("id", "")
                is_bot = user.get("is_bot", False)
                is_app = user.get("is_app_user", False)
                name = user.get("profile", {}).get("display_name") or user.get("real_name") or uid

                if is_bot or is_app or uid == "USLACKBOT":
                    bot_uids.append(uid)
                    bot_names.append(name)
                    bot_details[uid] = name

            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
            time.sleep(0.3)
    except SlackApiError as e:
        print(f"  Slack API error: {e.response.get('error', 'unknown')}")

    print(f"  Found {len(bot_uids)} bot/app users")
    return {
        "bot_uids": sorted(set(bot_uids)),
        "bot_patterns": sorted(set(bot_names)),
        "bot_details": bot_details,
    }


def discover_bots_from_cache() -> dict:
    """Fall back to analyzing local users_cache.json for likely bots."""
    cache_path = MESSAGES_DIR / ".users_cache.json"
    if not cache_path.exists():
        return {"bot_uids": [], "bot_patterns": []}

    cache = load_json(cache_path)
    bot_indicators = {
        "bot", "app", "webhook", "alert", "notification", "integration",
        "slack", "github", "linear", "jira", "sentry", "calendar",
        "drive", "airflow", "elementary", "metabase", "posthog",
    }

    bot_uids = ["USLACKBOT"]
    bot_patterns = []
    for uid, name in cache.items():
        if uid.startswith("_"):
            continue
        name_lower = name.lower() if isinstance(name, str) else ""
        if any(ind in name_lower for ind in bot_indicators):
            bot_uids.append(uid)
            bot_patterns.append(name)

    return {
        "bot_uids": sorted(set(bot_uids)),
        "bot_patterns": sorted(set(bot_patterns)),
    }


# ── Channel discovery ───────────────────────────────────────────────────────

NOISE_PATTERNS = [
    "pipeline", "deploy", "pull-request", "pr-review",
    "airbyte", "connector", "elementary", "airflow",
    "sentry", "alert", "notification", "webhook",
    "log", "fail", "success", "health", "triage",
    "staging", "prod-", "stage-",
]


def discover_channels(bot_uids: set) -> dict:
    """Analyze local Slack data to find noise channels to exclude."""
    channels_meta_path = MESSAGES_DIR / "_channels.json"
    _ = load_json(channels_meta_path)

    exclude = []
    channel_analysis = {}

    for jsonl in sorted(MESSAGES_DIR.glob("*.jsonl")):
        channel_name = jsonl.stem
        if channel_name.startswith("."):
            continue

        # Count messages by bot vs human
        total = 0
        bot_count = 0
        with open(jsonl) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    total += 1
                    uid = msg.get("user", "")
                    if uid in bot_uids or msg.get("subtype") in ("bot_message",):
                        bot_count += 1
                except Exception:
                    pass

        bot_pct = (bot_count / total * 100) if total > 0 else 0
        is_noise_name = any(p in channel_name.lower() for p in NOISE_PATTERNS)
        is_bot_heavy = bot_pct > 85 and total > 10
        is_bot_dm = channel_name.startswith("dm_") and channel_name.split("dm_", 1)[1] in bot_uids

        should_exclude = is_noise_name or is_bot_heavy or is_bot_dm

        channel_analysis[channel_name] = {
            "total_messages": total,
            "bot_pct": round(bot_pct, 1),
            "excluded": should_exclude,
            "reason": (
                "noise_name" if is_noise_name else
                "bot_heavy" if is_bot_heavy else
                "bot_dm" if is_bot_dm else
                "ok"
            ),
        }

        if should_exclude:
            exclude.append(channel_name)

    print(f"  Analyzed {len(channel_analysis)} channels, excluding {len(exclude)}")
    return {
        "exclude_channels": sorted(exclude),
        "channel_analysis": channel_analysis,
    }


# ── People discovery ────────────────────────────────────────────────────────

def discover_people(bot_uids: set) -> dict:
    """Analyze message frequency to find key people to track."""
    users_cache = load_json(MESSAGES_DIR / ".users_cache.json")

    # Count messages per human user across all channels
    msg_counts = Counter()      # uid -> total messages
    dm_counts = Counter()       # uid -> messages in DMs with the user
    channel_presence = {}       # uid -> set of channels

    for jsonl in sorted(MESSAGES_DIR.glob("*.jsonl")):
        channel_name = jsonl.stem
        if channel_name.startswith("."):
            continue

        with open(jsonl) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    uid = msg.get("user", "")
                    if not uid or uid in bot_uids or uid == USER_SLACK_ID:
                        continue
                    if msg.get("subtype") in ("bot_message", "channel_join", "channel_leave"):
                        continue

                    msg_counts[uid] += 1
                    channel_presence.setdefault(uid, set()).add(channel_name)

                    if channel_name.startswith("dm_"):
                        dm_counts[uid] += 1
                except Exception:
                    pass

    # Score people: DM frequency matters most, then overall presence
    scored = []
    for uid, total in msg_counts.most_common():
        name = users_cache.get(uid, uid)
        if not isinstance(name, str) or name == uid:
            continue
        dms = dm_counts.get(uid, 0)
        channels = len(channel_presence.get(uid, set()))
        # Score: DMs weighted 3x, channel breadth weighted 2x
        score = (dms * 3) + (total * 0.5) + (channels * 2)
        scored.append({
            "name": name,
            "uid": uid,
            "total_messages": total,
            "dm_messages": dms,
            "channels": channels,
            "score": round(score, 1),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # Build tracked_people and priorities
    tracked = {}
    deep_reconcile = {}
    priority_dms = {}

    for i, person in enumerate(scored[:20]):  # Top 20
        name = person["name"]
        peer_id = sanitize_id(name)
        if i < 5:
            priority = "high"
            deep_reconcile[peer_id] = name
            priority_dms[person["uid"]] = name
        elif i < 12:
            priority = "medium"
        else:
            priority = "low"

        tracked[name] = {
            "type": "internal",
            "peer_id": peer_id,
            "priority": priority,
            "slack_uid": person["uid"],
            "score": person["score"],
        }

    print(f"  Discovered {len(tracked)} people to track ({len(deep_reconcile)} high priority)")
    return {
        "tracked_people": tracked,
        "deep_reconcile_peers": deep_reconcile,
        "priority_dm_users": priority_dms,
        "all_scored": scored[:30],  # Keep top 30 for review
    }


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Auto-discover workspace config from Slack data")
    parser.add_argument("--slack-token", help="Slack user token (xoxp-...) for fresh API data")
    parser.add_argument("--force", action="store_true", help="Overwrite existing team.json")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be written")
    args = parser.parse_args()

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Discovering workspace config...")

    # ── 1. Discover bots ────────────────────────────────────────────────
    token = args.slack_token or get_token()
    if token and HAS_SLACK:
        bots = discover_bots_from_api(token)
    else:
        print("  No Slack token or SDK — falling back to cache analysis")
        bots = discover_bots_from_cache()

    bot_uids = set(bots.get("bot_uids", []))

    if args.dry_run:
        print(f"\n  Would write discovered_bots.json: {len(bot_uids)} bots")
    else:
        save_json(WORKSPACE / "discovered_bots.json", {
            "bot_uids": bots["bot_uids"],
            "bot_patterns": bots.get("bot_patterns", []),
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        })
        print(f"  Wrote discovered_bots.json ({len(bot_uids)} bots)")

    # ── 2. Discover noise channels ──────────────────────────────────────
    channels = discover_channels(bot_uids)

    if args.dry_run:
        print(f"\n  Would write discovered_channels.json: {len(channels['exclude_channels'])} excluded")
        for ch in channels["exclude_channels"][:10]:
            reason = channels["channel_analysis"].get(ch, {}).get("reason", "")
            print(f"    - {ch} ({reason})")
        if len(channels["exclude_channels"]) > 10:
            print(f"    ... and {len(channels['exclude_channels']) - 10} more")
    else:
        save_json(WORKSPACE / "discovered_channels.json", {
            "exclude_channels": channels["exclude_channels"],
            "channel_analysis": channels["channel_analysis"],
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        })
        print(f"  Wrote discovered_channels.json ({len(channels['exclude_channels'])} excluded)")

    # ── 3. Discover people (heuristic scores for analyze_priorities.py) ──
    people = discover_people(bot_uids)

    # Save scored people data for analyze_priorities.py to use as input
    if not args.dry_run:
        save_json(WORKSPACE / "discovered_people.json", {
            "scored": people["all_scored"],
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        })
        print(f"  Wrote discovered_people.json ({len(people['all_scored'])} scored)")
    else:
        print(f"\n  Would write discovered_people.json: {len(people['all_scored'])} people scored")

    # ── Summary ─────────────────────────────────────────────────────────
    print("\nDiscovery complete:")
    print(f"  Bots:     {len(bot_uids)}")
    print(f"  Excluded: {len(channels['exclude_channels'])} channels")
    print(f"  People:   {len(people['all_scored'])} scored")
    if people["all_scored"]:
        print("\n  Top 5 by interaction score:")
        for p in people["all_scored"][:5]:
            print(f"    {p['name']:25} score={p['score']:.0f}  DMs={p['dm_messages']}  channels={p['channels']}")
    print("\n  Priority ranking will be determined by analyze_priorities.py (LLM)")


if __name__ == "__main__":
    main()
