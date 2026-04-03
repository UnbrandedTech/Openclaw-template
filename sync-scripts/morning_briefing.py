#!/usr/bin/env python3
"""
Morning briefing — runs at 8am weekdays.
Pulls calendar, PRs, Linear, and Slack highlights.
Outputs a plain-text summary for Jeff to deliver via Slack DM.
"""

import subprocess
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
import re

TODAY = datetime.now().strftime("%A, %B %-d")
WORKSPACE = Path.home() / ".openclaw/workspace"
SLACK_STORE = WORKSPACE / "slack_messages"
LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY", "")
SLACK_USER_ID = os.environ.get("SLACK_USER_ID", "")


def run(cmd, **kwargs):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, **kwargs)
    return result.stdout.strip()


# ── 1. Calendar ──────────────────────────────────────────────────────────────
def get_calendar():
    try:
        raw = run("khal list today today --format '{title} {start-time}-{end-time} {location}' 2>/dev/null")
        if not raw or "No events" in raw:
            return []
        events = []
        for line in raw.splitlines():
            line = line.strip()
            if line and not line.startswith("Today"):
                events.append(line)
        return events
    except Exception:
        return []


# ── 2. Open PRs needing attention ────────────────────────────────────────────
def get_prs():
    try:
        raw = run(
            "cd $HOME/Projects/monorepo && "
            "gh pr list --author '@me' --json number,title,reviewDecision,statusCheckRollup,url "
            "--jq '.[] | {number, title, reviewDecision, url, checks: (.statusCheckRollup // [] | map(select(.conclusion == \"FAILURE\")) | length)}'"
        )
        if not raw:
            return []
        prs = []
        for line in raw.splitlines():
            try:
                pr = json.loads(line)
                prs.append(pr)
            except Exception:
                pass
        return prs
    except Exception:
        return []


# ── 3. Linear tickets ─────────────────────────────────────────────────────────
def get_linear():
    try:
        import urllib.request
        query = """
        {
          issues(filter: {
            assignee: { isMe: { eq: true } }
            state: { type: { in: ["started", "unstarted"] } }
          }, orderBy: updatedAt, first: 10) {
            nodes {
              identifier
              title
              state { name }
              priority
            }
          }
        }
        """
        data = json.dumps({"query": query}).encode()
        req = urllib.request.Request(
            "https://api.linear.app/graphql",
            data=data,
            headers={"Authorization": LINEAR_API_KEY, "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        nodes = result.get("data", {}).get("issues", {}).get("nodes", [])
        return nodes
    except Exception:
        return []


# ── 4. Slack highlights (from local store) ────────────────────────────────────
def get_slack_highlights():
    """Find messages mentioning James from the last 18 hours that look actionable."""
    if not SLACK_STORE.exists():
        return []

    cutoff = datetime.now() - timedelta(hours=18)
    mentions = []
    action_keywords = re.compile(
        r'\b(can you|could you|please|urgent|asap|blocked|need you|waiting on you|lmk|let me know|your thoughts|thoughts\?)\b',
        re.IGNORECASE
    )

    for f in SLACK_STORE.glob("*.jsonl"):
        try:
            with open(f) as fh:
                for line in fh:
                    try:
                        msg = json.loads(line)
                        ts = float(msg.get("ts", 0))
                        if datetime.fromtimestamp(ts) < cutoff:
                            continue
                        text = msg.get("text", "")
                        user = msg.get("user", "")
                        # Skip James's own messages
                        if user == JAMES_SLACK_ID:
                            continue
                        # Include if mentioning James or has action keywords
                        if JAMES_SLACK_ID in text or action_keywords.search(text):
                            channel = msg.get("channel_name", f.stem)
                            mentions.append({
                                "channel": channel,
                                "user": msg.get("user_name", user),
                                "text": text[:120].replace("\n", " "),
                            })
                    except Exception:
                        pass
        except Exception:
            pass

    # Dedup by text content
    seen = set()
    deduped = []
    for m in mentions:
        key = m["text"][:80]
        if key not in seen:
            seen.add(key)
            deduped.append(m)

    return deduped[:5]  # cap at 5


# ── Assemble briefing ─────────────────────────────────────────────────────────
def main():
    sections = []
    sections.append(f"*Good morning, James. Here's your {TODAY} briefing.*")

    # Calendar
    events = get_calendar()
    if events:
        sections.append("\n*📅 Today's meetings:*")
        for e in events:
            sections.append(f"  • {e}")
    else:
        sections.append("\n*📅 Calendar:* Nothing scheduled today.")

    # PRs
    prs = get_prs()
    if prs:
        needs_attention = [p for p in prs if p.get("reviewDecision") in ("CHANGES_REQUESTED",) or p.get("checks", 0) > 0]
        in_review = [p for p in prs if p.get("reviewDecision") == "REVIEW_REQUIRED"]
        approved = [p for p in prs if p.get("reviewDecision") == "APPROVED"]

        sections.append("\n*🔀 Open PRs:*")
        if needs_attention:
            for p in needs_attention:
                tag = "⚠️ changes requested" if p.get("reviewDecision") == "CHANGES_REQUESTED" else "❌ CI failing"
                sections.append(f"  • #{p['number']} {p['title']} — {tag}")
        if in_review:
            for p in in_review:
                sections.append(f"  • #{p['number']} {p['title']} — awaiting review")
        if approved:
            for p in approved:
                sections.append(f"  • #{p['number']} {p['title']} — approved, ready to merge")
        if not prs:
            sections.append("  No open PRs.")
    else:
        sections.append("\n*🔀 PRs:* None open.")

    # Linear
    tickets = get_linear()
    if tickets:
        sections.append("\n*📋 Linear (your tickets):*")
        for t in tickets[:6]:
            state = t.get("state", {}).get("name", "")
            sections.append(f"  • {t['identifier']} {t['title']} ({state})")
    else:
        sections.append("\n*📋 Linear:* No active tickets assigned to you.")

    # Slack highlights
    highlights = get_slack_highlights()
    if highlights:
        sections.append("\n*💬 Slack — needs your attention:*")
        for h in highlights:
            sections.append(f"  • #{h['channel']} — {h['user']}: {h['text']}")

    sections.append("\nHave a good one. 🚀")
    print("\n".join(sections))


if __name__ == "__main__":
    main()
