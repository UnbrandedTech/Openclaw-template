#!/usr/bin/env python3
"""
Morning briefing — runs at 8am weekdays.
Pulls calendar, PRs, Linear, and Slack highlights.
Outputs a plain-text summary for Jeff to deliver via Slack DM.
"""

import shutil
import subprocess
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

from shared import WORKSPACE, USER_FIRST_NAME, USER_SLACK_ID, get_secret

TODAY = datetime.now().strftime("%A, %B %-d")
SLACK_STORE = WORKSPACE / "slack_messages"
LINEAR_API_KEY = get_secret("LINEAR_API_KEY")


def run(cmd, **kwargs):
    if isinstance(cmd, str):
        import shlex
        cmd = shlex.split(cmd)
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    return result.stdout.strip()


# ── 1. Calendar ──────────────────────────────────────────────────────────────
def get_calendar():
    try:
        raw = run(["khal", "list", "today", "today", "--format", "{title} {start-time}-{end-time} {location}"], stderr=subprocess.DEVNULL)
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
            ["gh", "pr", "list", "--author", "@me",
             "--json", "number,title,reviewDecision,statusCheckRollup,url",
             "--jq", '.[] | {number, title, reviewDecision, url, checks: (.statusCheckRollup // [] | map(select(.conclusion == "FAILURE")) | length)}'],
            cwd=str(Path.home() / "Projects" / "monorepo"),
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
    """Find messages mentioning the user from the last 18 hours that look actionable."""
    if not SLACK_STORE.exists():
        return []

    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=18)
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
                        if datetime.fromtimestamp(ts, tz=timezone.utc) < cutoff:
                            continue
                        text = msg.get("text", "")
                        user = msg.get("user", "")
                        # Skip user's own messages
                        if user == USER_SLACK_ID:
                            continue
                        # Include if mentioning user or has action keywords
                        if USER_SLACK_ID in text or action_keywords.search(text):
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
    sections.append(f"*Good morning, {USER_FIRST_NAME}. Here's your {TODAY} briefing.*")

    # Calendar
    events = get_calendar()
    if events:
        sections.append("\n*📅 Today's meetings:*")
        for e in events:
            sections.append(f"  • {e}")
    else:
        sections.append("\n*📅 Calendar:* Nothing scheduled today.")

    # PRs (only if gh CLI is available)
    if shutil.which("gh"):
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

    # Linear (only if API key is configured)
    if LINEAR_API_KEY:
        tickets = get_linear()
        if tickets:
            sections.append("\n*📋 Linear (your tickets):*")
            for t in tickets[:6]:
                state = t.get("state", {}).get("name", "")
                sections.append(f"  • {t['identifier']} {t['title']} ({state})")

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
