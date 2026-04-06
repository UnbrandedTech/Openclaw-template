#!/usr/bin/env python3
"""
analyze_priorities.py — Use an LLM (configured "reasoning" model) to analyze
workspace data and determine priority rankings for team.json.

Gathers data from:
  - discovered_bots.json (to exclude)
  - Slack users cache + message frequency from JSONL files
  - calendar_attendees.json (meeting frequency)
  - github_activity.json (collaboration data, if exists)
  - Channel metadata from _channels.json

Calls the configured reasoning model to classify people, identify clients,
and rank priorities. Merges results into team.json (preserving manual fields
like repo_map).

Usage:
  python3 analyze_priorities.py                     # Run analysis
  python3 analyze_priorities.py --dry-run           # Preview without writing
  python3 analyze_priorities.py --services-business  # Emphasize client detection
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone

from shared import (
    WORKSPACE,
    MESSAGES_DIR,
    load_json,
    save_json,
    call_llm,
    USER_NAME,
    USER_TITLE,
    USER_COMPANY,
)


# ── File paths ─────────────────────────────────────────────────────────────

BOTS_FILE = WORKSPACE / "discovered_bots.json"
USERS_CACHE_FILE = MESSAGES_DIR / ".users_cache.json"
CHANNELS_META_FILE = MESSAGES_DIR / "_channels.json"
ATTENDEES_FILE = WORKSPACE / "calendar_attendees.json"
GITHUB_FILE = WORKSPACE / "github_activity.json"
TEAM_FILE = WORKSPACE / "team.json"




# ── Data gathering ─────────────────────────────────────────────────────────


def load_bot_uids() -> set:
    """Load the set of known bot UIDs to exclude."""
    bots = load_json(BOTS_FILE)
    return set(bots.get("bot_uids", []))


def gather_slack_people(bot_uids: set) -> list[dict]:
    """Count messages per human user across all Slack channels.

    Returns the top 30 people sorted by a weighted score.
    """
    users_cache = load_json(USERS_CACHE_FILE)
    if not users_cache:
        print("  WARNING: No users cache found at", USERS_CACHE_FILE)
        return []

    from shared import USER_SLACK_ID

    msg_counts: Counter = Counter()
    dm_counts: Counter = Counter()
    channel_presence: dict[str, set] = {}

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

    scored = []
    for uid, total in msg_counts.most_common():
        name = users_cache.get(uid, uid)
        if not isinstance(name, str) or name == uid:
            continue
        dms = dm_counts.get(uid, 0)
        channels = len(channel_presence.get(uid, set()))
        score = (dms * 3) + (total * 0.5) + (channels * 2)
        scored.append({
            "name": name,
            "uid": uid,
            "total_messages": total,
            "dm_messages": dms,
            "channel_count": channels,
            "score": round(score, 1),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:30]


def gather_calendar_attendees() -> list[dict]:
    """Load calendar attendee frequency data, returning top 20."""
    attendees = load_json(ATTENDEES_FILE)
    if not attendees:
        return []

    ranked = []
    for email, info in attendees.items():
        if not isinstance(info, dict):
            continue
        ranked.append({
            "name": info.get("name", "") or email.split("@")[0],
            "email": email,
            "meeting_count": info.get("meeting_count", 0),
            "last_met": info.get("last_met_date", ""),
        })

    ranked.sort(key=lambda x: x["meeting_count"], reverse=True)
    return ranked[:20]


def gather_github_collaborators() -> list[dict]:
    """Load GitHub collaborator data if available."""
    github = load_json(GITHUB_FILE)
    if not github:
        return []

    collabs = github.get("collaborators", {})
    if not collabs:
        return []

    result = []
    for username, info in collabs.items():
        if not isinstance(info, dict):
            continue
        result.append({
            "username": username,
            "pr_count": info.get("pr_count", 0),
            "review_count": info.get("review_count", 0),
        })

    result.sort(key=lambda x: x["pr_count"] + x["review_count"], reverse=True)
    return result


def gather_channels(bot_uids: set) -> list[dict]:
    """Gather non-excluded Slack channels with metadata."""
    channels_meta = load_json(CHANNELS_META_FILE)

    # Load discovered channels for exclusion info
    discovered = load_json(WORKSPACE / "discovered_channels.json")
    excluded_set = set(discovered.get("exclude_channels", []))

    channels = []
    for jsonl in sorted(MESSAGES_DIR.glob("*.jsonl")):
        channel_name = jsonl.stem
        if channel_name.startswith(".") or channel_name in excluded_set:
            continue

        meta = channels_meta.get(channel_name, {})
        member_count = meta.get("num_members", 0)

        # Count total messages for context
        total = 0
        with open(jsonl) as f:
            for line in f:
                if line.strip():
                    total += 1

        channels.append({
            "name": channel_name,
            "member_count": member_count,
            "message_count": total,
            "topic": meta.get("topic", {}).get("value", "") if isinstance(meta.get("topic"), dict) else str(meta.get("topic", "")),
        })

    channels.sort(key=lambda x: x["message_count"], reverse=True)
    return channels


# ── Prompt construction ────────────────────────────────────────────────────


def build_prompt(
    slack_people: list[dict],
    calendar_attendees: list[dict],
    github_collabs: list[dict],
    channels: list[dict],
    services_business: bool,
) -> str:
    """Build the analysis prompt for Claude Sonnet."""

    # -- Slack people summary (enriched with profile data) --
    slack_section = "## Top Slack Contacts (by interaction score)\n\n"
    if slack_people:
        slack_section += "| Name | Email | Title | Guest? | Classification | DMs | Channels | Score |\n"
        slack_section += "|------|-------|-------|--------|---------------|-----|----------|-------|\n"
        for p in slack_people:
            guest_tag = "YES (guest)" if p.get("is_guest") else "no"
            classification = p.get("classification", "unknown")
            email = p.get("email", "")
            title = p.get("title", "")
            slack_section += (
                f"| {p['name']} | {email} | {title} | {guest_tag} | {classification} "
                f"| {p['dm_messages']} | {p.get('channel_count', p.get('channels', 0))} | {p['score']} |\n"
            )
    else:
        slack_section += "(No Slack data available)\n"

    # -- Calendar attendees summary --
    calendar_section = "## Top Calendar Attendees (by meeting frequency)\n\n"
    if calendar_attendees:
        calendar_section += "| Name | Email | Meeting Count | Last Met |\n"
        calendar_section += "|------|-------|--------------|----------|\n"
        for a in calendar_attendees:
            calendar_section += (
                f"| {a['name']} | {a['email']} | {a['meeting_count']} "
                f"| {a['last_met']} |\n"
            )
    else:
        calendar_section += "(No calendar data available)\n"

    # -- GitHub collaborators --
    github_section = "## GitHub Collaborators\n\n"
    if github_collabs:
        github_section += "| Username | PRs | Reviews |\n"
        github_section += "|----------|-----|---------|\n"
        for g in github_collabs:
            github_section += f"| {g['username']} | {g['pr_count']} | {g['review_count']} |\n"
    else:
        github_section += "(No GitHub data available)\n"

    # -- Channels --
    channel_section = "## Slack Channels (non-excluded)\n\n"
    if channels:
        channel_section += "| Channel | Members | Messages | Topic |\n"
        channel_section += "|---------|---------|----------|-------|\n"
        for c in channels:
            topic = c["topic"][:60] + "..." if len(c["topic"]) > 60 else c["topic"]
            channel_section += (
                f"| {c['name']} | {c['member_count']} | {c['message_count']} "
                f"| {topic} |\n"
            )
    else:
        channel_section += "(No channel data available)\n"

    # -- Internal domain hint --
    discovered = load_json(WORKSPACE / "discovered_people.json")
    internal_domain = discovered.get("internal_domain", "")

    domain_note = ""
    if internal_domain:
        domain_note = f"""
CRITICAL CLASSIFICATION RULE:
- Users with @{internal_domain} email addresses are INTERNAL team members. Never classify them as clients.
- Users marked as "Guest" in Slack (is_guest=YES) are EXTERNAL contacts (clients, contractors, vendors).
- Users with a different email domain who are NOT guests may be contractors or partners.
- The "Classification" column already has a preliminary internal/external tag based on these signals. Trust it as a strong hint.
"""

    # -- Services business emphasis --
    services_note = ""
    if services_business:
        services_note = """
IMPORTANT: This user runs a services/consulting business. Pay special attention to:
- Identifying client companies from email domains (e.g., @acme.com contacts are likely from client "Acme")
- Slack channel naming patterns that suggest client projects (e.g., "acme-project", "client-acme", "ext-acme")
- Guest users are almost certainly client contacts
- Internal team members (@{internal_domain}) should NEVER appear as client contacts
- Grouping external contacts by their company/client affiliation
- Marking client channels and their associated contacts
""".replace("{internal_domain}", internal_domain)

    prompt = f"""You are analyzing workspace data for a professional to determine their priority contacts,
client relationships, and important communication channels.

## User Context
- Name: {USER_NAME or '(unknown)'}
- Title: {USER_TITLE or '(unknown)'}
- Company: {USER_COMPANY or '(unknown)'}
- Internal email domain: @{internal_domain or '(unknown)'}
{domain_note}
{services_note}
## Workspace Data

{slack_section}

{calendar_section}

{github_section}

{channel_section}

## Your Task

Analyze this data and produce a structured JSON output. Consider the following:

1. **Classify each person** into one of these types:
   - `internal_team` — colleagues at the same company
   - `client_contact` — people from client/customer organizations
   - `vendor_contact` — people from vendor/tool/service providers
   - `external` — other external contacts

2. **Identify client companies** from email domains and Slack channel patterns. Group contacts by their company.

3. **Rank by actual importance**, not just volume. Indicators of high importance:
   - Frequent 1:1 DMs (indicates close working relationship)
   - Regular calendar meetings (especially recurring 1:1s or small-group meetings)
   - GitHub collaboration (code review partners)
   - Presence in multiple channels together
   - Someone you meet with weekly in 1:1s may be MORE important than someone who posts a lot in large channels

4. **Identify priority channels** — channels where important work discussions happen (not just high-volume channels).

5. **Detect channel prefixes** that indicate client work (e.g., if multiple channels start with "acme-", the prefix "acme" maps to a client).

Produce your output as JSON wrapped in ```json blocks. The JSON should have this structure:

```json
{{
  "tracked_people": {{
    "Person Name": {{
      "type": "internal_team|client_contact|vendor_contact|external",
      "peer_id": "person-name",
      "priority": "high|medium|low",
      "slack_uid": "U...",
      "email": "person@example.com",
      "company": "Company Name",
      "reason": "Brief explanation of why this priority level"
    }}
  }},
  "clients": {{
    "Company Name": {{
      "type": "client",
      "domain": "company.com",
      "contacts": ["person-name-1", "person-name-2"],
      "channel": "company-project",
      "priority": "high|medium|low"
    }}
  }},
  "priority_dm_users": {{
    "U12345": "Person Name"
  }},
  "priority_channels": {{
    "channel-name": "Brief reason this channel matters"
  }},
  "deep_reconcile_peers": {{
    "person-name": "Person Name"
  }},
  "client_channel_prefix": "prefix-pattern or empty string"
}}
```

Rules:
- `tracked_people`: Include the top 20 most important people. Use the peer_id format: lowercase, hyphens instead of spaces.
- `clients`: Group client contacts by company. Include domain, key contacts (as peer_ids), primary channel, and priority.
- `priority_dm_users`: Map Slack UIDs to names for the top 5-8 most important DM contacts.
- `priority_channels`: The top 5-10 channels that matter most for the user's work.
- `deep_reconcile_peers`: The top 5 most important people (peer_id -> display name) who warrant deep context tracking.
- `client_channel_prefix`: If you detect a common prefix pattern for client channels, include it. Otherwise empty string.
- For each person in tracked_people, include their email if available from calendar data.
- Match people across data sources by name (Slack name may differ slightly from calendar name — use best judgment).

Return ONLY the JSON block, no other text before or after it.
"""
    return prompt


# ── Response parsing ───────────────────────────────────────────────────────


def parse_sonnet_response(response_text: str) -> dict:
    """Extract JSON from Sonnet's response (expects ```json blocks)."""
    # Try to find JSON in code blocks
    pattern = r"```json\s*(.*?)\s*```"
    matches = re.findall(pattern, response_text, re.DOTALL)

    if matches:
        # Use the first (and likely only) JSON block
        try:
            return json.loads(matches[0])
        except json.JSONDecodeError as e:
            print(f"  WARNING: Failed to parse JSON from code block: {e}")

    # Fallback: try to parse the entire response as JSON
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        pass

    # Last resort: find anything that looks like a JSON object
    brace_start = response_text.find("{")
    brace_end = response_text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        try:
            return json.loads(response_text[brace_start:brace_end + 1])
        except json.JSONDecodeError:
            pass

    print("ERROR: Could not parse JSON from Sonnet response.", file=sys.stderr)
    print("  Response preview:", response_text[:500], file=sys.stderr)
    sys.exit(1)


# ── Merge into team.json ──────────────────────────────────────────────────


def merge_into_team(existing: dict, analysis: dict) -> dict:
    """Merge Sonnet's analysis into existing team.json, preserving manual fields."""
    merged = dict(existing)

    # -- tracked_people: merge, preserving manual fields like repo_map --
    existing_people = merged.get("tracked_people", {})
    new_people = analysis.get("tracked_people", {})

    for name, new_info in new_people.items():
        if name in existing_people:
            # Preserve existing manual fields, update analysis fields
            old = existing_people[name]
            for key, val in new_info.items():
                old[key] = val
        else:
            existing_people[name] = new_info

    merged["tracked_people"] = existing_people

    # -- clients: merge, preserving existing client data --
    existing_clients = merged.get("clients", {})
    new_clients = analysis.get("clients", {})

    for company, new_info in new_clients.items():
        if company in existing_clients:
            old = existing_clients[company]
            for key, val in new_info.items():
                if key == "contacts":
                    # Merge contact lists
                    old_contacts = set(old.get("contacts", []))
                    old["contacts"] = sorted(old_contacts | set(val))
                else:
                    old[key] = val
        else:
            existing_clients[company] = new_info

    merged["clients"] = existing_clients

    # -- priority_dm_users: replace with new analysis --
    if "priority_dm_users" in analysis:
        merged["priority_dm_users"] = analysis["priority_dm_users"]

    # -- priority_channels: replace with new analysis --
    if "priority_channels" in analysis:
        merged["priority_channels"] = analysis["priority_channels"]

    # -- deep_reconcile_peers: replace with new analysis --
    if "deep_reconcile_peers" in analysis:
        merged["deep_reconcile_peers"] = analysis["deep_reconcile_peers"]

    # -- client_channel_prefix: update if provided --
    if analysis.get("client_channel_prefix"):
        merged["client_channel_prefix"] = analysis["client_channel_prefix"]

    # -- metadata --
    merged["analyzed_at"] = datetime.now(timezone.utc).isoformat()
    merged["analyzed_by"] = "analyze_priorities.py"

    return merged


# ── Main ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Use Claude Sonnet to analyze workspace data and rank priorities for team.json"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview analysis without writing team.json")
    parser.add_argument(
        "--services-business", action="store_true",
        help="Emphasize client company detection (for consulting/services businesses)",
    )
    args = parser.parse_args()

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Analyzing workspace priorities...")
    print(f"  User: {USER_NAME or '(not set)'} | {USER_TITLE or '(no title)'} @ {USER_COMPANY or '(no company)'}")
    if args.services_business:
        print("  Mode: services/consulting business (client detection emphasized)")

    # ── 1. Gather data ─────────────────────────────────────────────────

    print("\n  Gathering data sources...")

    bot_uids = load_bot_uids()
    print(f"    Bots to exclude: {len(bot_uids)}")

    slack_people = gather_slack_people(bot_uids)
    print(f"    Slack contacts: {len(slack_people)}")

    calendar_attendees = gather_calendar_attendees()
    print(f"    Calendar attendees: {len(calendar_attendees)}")

    github_collabs = gather_github_collaborators()
    print(f"    GitHub collaborators: {len(github_collabs)}")

    channels = gather_channels(bot_uids)
    print(f"    Slack channels: {len(channels)}")

    if not slack_people and not calendar_attendees:
        print("\nERROR: No Slack or calendar data found. Run sync scripts first.", file=sys.stderr)
        sys.exit(1)

    # ── 2. Build prompt ────────────────────────────────────────────────

    prompt = build_prompt(
        slack_people=slack_people,
        calendar_attendees=calendar_attendees,
        github_collabs=github_collabs,
        channels=channels,
        services_business=args.services_business,
    )

    print(f"\n  Prompt size: {len(prompt):,} characters")

    # ── 3. Call Sonnet ─────────────────────────────────────────────────

    if args.dry_run:
        print("\n  [DRY RUN] Would send prompt to configured reasoning model.")
        print("\n  Data summary:")
        if slack_people:
            print("    Top 5 Slack contacts:")
            for p in slack_people[:5]:
                print(f"      {p['name']:25} DMs={p['dm_messages']}  channels={p['channel_count']}  score={p['score']}")
        if calendar_attendees:
            print("    Top 5 calendar attendees:")
            for a in calendar_attendees[:5]:
                print(f"      {a['name']:25} meetings={a['meeting_count']}  email={a['email']}")
        if github_collabs:
            print("    Top 5 GitHub collaborators:")
            for g in github_collabs[:5]:
                print(f"      {g['username']:25} PRs={g['pr_count']}  reviews={g['review_count']}")
        if channels:
            print("    Top 5 channels:")
            for c in channels[:5]:
                print(f"      {c['name']:25} members={c['member_count']}  messages={c['message_count']}")

        # Show existing team.json state
        existing = load_json(TEAM_FILE)
        if existing:
            print(f"\n  Existing team.json: {len(existing.get('tracked_people', {}))} tracked people, "
                  f"{len(existing.get('clients', {}))} clients")
        else:
            print("\n  No existing team.json (will create new)")

        print("\n  [DRY RUN] No files written.")
        return

    print("\n  Calling reasoning model...")
    response_text = call_llm(prompt, role="reasoning", max_tokens=4096)
    print(f"  Response received ({len(response_text):,} characters)")

    # ── 4. Parse response ──────────────────────────────────────────────

    analysis = parse_sonnet_response(response_text)

    people_count = len(analysis.get("tracked_people", {}))
    clients_count = len(analysis.get("clients", {}))
    dm_count = len(analysis.get("priority_dm_users", {}))
    channel_count = len(analysis.get("priority_channels", {}))
    deep_count = len(analysis.get("deep_reconcile_peers", {}))

    print("\n  Analysis results:")
    print(f"    Tracked people:      {people_count}")
    print(f"    Clients identified:  {clients_count}")
    print(f"    Priority DM users:   {dm_count}")
    print(f"    Priority channels:   {channel_count}")
    print(f"    Deep reconcile:      {deep_count}")

    if analysis.get("client_channel_prefix"):
        print(f"    Client channel prefix: {analysis['client_channel_prefix']}")

    # ── 5. Merge and write ─────────────────────────────────────────────

    existing = load_json(TEAM_FILE)
    merged = merge_into_team(existing, analysis)

    save_json(TEAM_FILE, merged)
    print(f"\n  Wrote {TEAM_FILE}")
    print(f"    Total tracked people: {len(merged.get('tracked_people', {}))}")
    print(f"    Total clients: {len(merged.get('clients', {}))}")

    # ── Summary ────────────────────────────────────────────────────────

    print("\nAnalysis complete.")
    if analysis.get("clients"):
        print("\n  Identified clients:")
        for name, info in analysis["clients"].items():
            contacts = ", ".join(info.get("contacts", []))
            print(f"    {name} ({info.get('domain', '?')}) — {info.get('priority', '?')} priority")
            if contacts:
                print(f"      contacts: {contacts}")
            if info.get("channel"):
                print(f"      channel: {info['channel']}")

    if analysis.get("deep_reconcile_peers"):
        print("\n  Deep reconcile peers (top priority for context tracking):")
        for peer_id, display_name in analysis["deep_reconcile_peers"].items():
            print(f"    {peer_id}: {display_name}")


if __name__ == "__main__":
    main()
