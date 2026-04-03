#!/usr/bin/env python3
"""
update_dossiers.py — Gather Honcho context for dossier updates.

Queries Honcho for each tracked person and writes a JSON bundle to stdout
(or a temp file). The calling agent does the LLM merge step.

Usage:
  python3 update_dossiers.py [--person "Tom Montgomery"] [--priority high]
  python3 update_dossiers.py --list   # Just list tracked people
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from honcho import Honcho
except ImportError:
    print("ERROR: honcho-ai not installed.", file=sys.stderr)
    sys.exit(1)

VAULT_PATH = Path.home() / "Documents" / "Obsidian Vault"
PEOPLE_DIR = VAULT_PATH / "👥 People"

HONCHO_BASE_URL = "http://localhost:18790"
HONCHO_WORKSPACE = "openclaw"

TRACKED_PEOPLE = {
    "Tom Montgomery":      {"type": "internal", "peer_id": "tom-montgomery",      "priority": "high"},
    "Preston Rutherford":  {"type": "internal", "peer_id": "preston-rutherford",  "priority": "high"},
    "Ashley Spencer":      {"type": "internal", "peer_id": "ashley-spencer",      "priority": "high"},
    "Viktor Kovtun":       {"type": "internal", "peer_id": "viktor-kovtun",       "priority": "high"},
    "Phil":                {"type": "internal", "peer_id": "phil",                "priority": "medium"},
    "Theja Talla":         {"type": "internal", "peer_id": "theja-talla",         "priority": "medium"},
    "Erich":               {"type": "internal", "peer_id": "erich",               "priority": "medium"},
    "Chris Dolan":         {"type": "internal", "peer_id": "chris-dolan",         "priority": "medium"},
    "Viktor Kovtun":       {"type": "internal", "peer_id": "viktor-kovtun",       "priority": "high"},
    "Oleh Kuchuk":         {"type": "internal", "peer_id": "oleh-kuchuk",         "priority": "low"},
    "Kostiantyn Saliuk":   {"type": "internal", "peer_id": "kostiantyn-saliuk",   "priority": "low"},
    "Ertugrul Goktas":     {"type": "internal", "peer_id": "ertugrul-goktas",     "priority": "low"},
    "Mete Alanli":         {"type": "internal", "peer_id": "mete-alanli",         "priority": "low"},
    "Logan Hohs":          {"type": "internal", "peer_id": "logan-hohs",          "priority": "low"},
}


def sanitize_peer_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def get_honcho_context(honcho, peer_id: str, person_name: str) -> str:
    """Query Honcho for everything it knows about this person."""
    agent_peer = honcho.peer("agent-main")
    prompt = (
        f"Tell me everything you know about {person_name} at the company. "
        f"Include: their role and responsibilities, what they're currently working on, "
        f"recent conversations or decisions involving them, their communication style and preferences, "
        f"their relationship with James (CTO), notable opinions, commitments made or owed, "
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

    honcho = Honcho(base_url=HONCHO_BASE_URL, workspace_id=HONCHO_WORKSPACE)

    targets = {}
    if args.person:
        if args.person in TRACKED_PEOPLE:
            targets[args.person] = TRACKED_PEOPLE[args.person]
        else:
            targets[args.person] = {
                "type": "internal",
                "peer_id": sanitize_peer_id(args.person),
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
