#!/usr/bin/env python3
"""
update_dossiers.py — Gather Honcho context for dossier updates.

Queries Honcho for each tracked person and writes a JSON bundle to stdout
(or a temp file). The calling agent does the LLM merge step.

Usage:
  python3 update_dossiers.py [--person "Jane Doe"] [--priority high]
  python3 update_dossiers.py --list   # Just list tracked people
"""

import argparse
import json
import sys
import time
from pathlib import Path

from shared import PEOPLE_DIR, get_honcho, sanitize_id, USER_NAME, USER_TITLE
from config import TRACKED_PEOPLE


def get_honcho_context(honcho, peer_id: str, person_name: str) -> str:
    """Query Honcho for everything it knows about this person."""
    agent_peer = honcho.peer("agent-main")
    role_label = f"{USER_NAME} ({USER_TITLE})" if USER_TITLE else USER_NAME
    prompt = (
        f"Tell me everything you know about {person_name} at the company. "
        f"Include: their role and responsibilities, what they're currently working on, "
        f"recent conversations or decisions involving them, their communication style and preferences, "
        f"their relationship with {role_label}, notable opinions, commitments made or owed, "
        f"and any open threads or blockers. Be specific. Skip anything you don't have data on."
    )
    try:
        response = agent_peer.chat(prompt, target=peer_id, reasoning_level="medium")
        return str(response).strip()
    except Exception as e:
        # Fallback to peer card
        try:
            peer = honcho.peer(peer_id)
            card = peer.get_card()
            if card:
                return "\n".join(f"- {f}" for f in card)
        except Exception:
            pass
        print(f"  warn: {person_name} honcho error: {e}", file=sys.stderr)
        return ""


def read_dossier(person_name: str) -> str:
    md_file = PEOPLE_DIR / f"{person_name}.md"
    if md_file.exists():
        return md_file.read_text(encoding="utf-8", errors="replace")
    return ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--person", help="Gather for a single person")
    parser.add_argument("--priority", choices=["high", "medium", "low", "all"], default="all")
    parser.add_argument("--list", action="store_true", help="List tracked people and exit")
    parser.add_argument("--out", help="Write JSON to file instead of stdout")
    args = parser.parse_args()

    if args.list:
        for name, info in TRACKED_PEOPLE.items():
            print(f"{info['priority']:6}  {name}")
        return

    honcho = get_honcho()

    targets = {}
    if args.person:
        if args.person in TRACKED_PEOPLE:
            targets[args.person] = TRACKED_PEOPLE[args.person]
        else:
            targets[args.person] = {
                "type": "internal",
                "peer_id": sanitize_id(args.person),
                "priority": "medium",
            }
    else:
        for name, info in TRACKED_PEOPLE.items():
            if args.priority == "all" or info["priority"] == args.priority:
                targets[name] = info

    results = {}
    for person_name, info in targets.items():
        print(f"  querying {person_name}...", file=sys.stderr)
        context = get_honcho_context(honcho, info["peer_id"], person_name)
        current = read_dossier(person_name)
        results[person_name] = {
            "contact_type": info["type"],
            "priority": info["priority"],
            "dossier_path": str(PEOPLE_DIR / f"{person_name}.md"),
            "has_existing_dossier": bool(current),
            "current_dossier": current,
            "honcho_context": context,
        }
        time.sleep(0.3)

    output = json.dumps(results, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(output)
        print(f"  wrote {len(results)} entries to {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
