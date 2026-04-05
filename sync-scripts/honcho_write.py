#!/usr/bin/env python3
"""
honcho_write.py — Active write interface to Honcho for the Jeff agent.

Push conclusions (learnings, decisions, context) directly into Honcho memory.
This complements passive observation from conversations with active knowledge capture.

Usage:
  # Push a learning about the owner
  python3 honcho_write.py "Decided to drop the Q3 campaign"

  # Push with domain tag
  python3 honcho_write.py "Acme deal is $57-60K/mo" --domain business

  # Push about a specific person
  python3 honcho_write.py "Sarah wants Friday debrief" --about sarah-jones

  # Push multiple learnings from stdin (one per line)
  echo -e "fact one\\nfact two" | python3 honcho_write.py --stdin

  # Search conclusions
  python3 honcho_write.py --search "campaign"

  # Delete a conclusion by ID
  python3 honcho_write.py --delete obs-123

  # Import MEMORY.md into Honcho as conclusions
  python3 honcho_write.py --import-memory
"""

import argparse
import re
import sys
import time
from pathlib import Path

from shared import HONCHO_BASE_URL, HONCHO_WORKSPACE, get_honcho, WORKSPACE

MEMORY_MD = WORKSPACE / "MEMORY.md"


def push_conclusions(honcho, contents: list[str], observer: str = "agent-main",
                     observed: str = "owner", domain: str = None):
    """Push a list of conclusions to Honcho."""
    peer = honcho.peer(observer)
    scope = peer.conclusions_of(observed) if observed != observer else peer.conclusions

    items = []
    for content in contents:
        content = content.strip()
        if not content or len(content) < 5:
            continue
        if domain:
            content = f"[{domain}] {content}"
        items.append({"content": content})

    if not items:
        print("No valid conclusions to push.")
        return 0

    # Batch in groups of 100
    total = 0
    for i in range(0, len(items), 100):
        batch = items[i:i + 100]
        created = scope.create(batch)
        total += len(created)
        if i + 100 < len(items):
            time.sleep(0.3)

    return total


def search_conclusions(honcho, query: str, observer: str = "agent-main",
                       observed: str = "owner", top_k: int = 10):
    """Search conclusions and print results."""
    peer = honcho.peer(observer)
    scope = peer.conclusions_of(observed) if observed != observer else peer.conclusions
    results = scope.query(query, top_k=top_k)
    if not results:
        print("No matching conclusions found.")
        return
    for c in results:
        print(f"  {c.id} | {c.content}")


def delete_conclusion(honcho, conclusion_id: str, observer: str = "agent-main",
                      observed: str = "owner"):
    """Delete a conclusion by ID."""
    peer = honcho.peer(observer)
    scope = peer.conclusions_of(observed) if observed != observer else peer.conclusions
    scope.delete(conclusion_id)
    print(f"Deleted: {conclusion_id}")


def import_memory_md(honcho):
    """Import MEMORY.md content as structured conclusions into Honcho."""
    if not MEMORY_MD.exists():
        print(f"ERROR: {MEMORY_MD} not found.")
        return 0

    content = MEMORY_MD.read_text()
    conclusions = []

    # Parse sections and extract facts
    lines = content.split("\n")
    current_section = ""
    current_subsection = ""

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("## "):
            current_section = stripped[3:].strip()
            current_subsection = ""
            continue
        if stripped.startswith("### "):
            current_subsection = stripped[4:].strip()
            continue

        # Skip empty lines, headers, metadata
        if not stripped or stripped.startswith("#") or stripped.startswith("---") or stripped.startswith("*Created"):
            continue

        # Build domain tag from section
        domain = ""
        if "Company Context" in current_section or "Financial" in current_section:
            domain = "business"
        elif "People" in current_section:
            domain = "people"
        elif "Slack" in current_section or "Integration" in current_section:
            domain = "integrations"
        elif "Lessons" in current_section:
            domain = "preferences"
        elif "Session" in current_section:
            domain = "history"

        # Extract bullet points and paragraphs
        if stripped.startswith("- **") or stripped.startswith("- "):
            fact = stripped.lstrip("- ").strip()
            if len(fact) > 10:
                prefix = f"[{domain}] " if domain else ""
                context = f" ({current_subsection})" if current_subsection else ""
                conclusions.append(f"{prefix}{fact}{context}")
        elif len(stripped) > 20 and not stripped.startswith("|"):
            # Paragraph text
            prefix = f"[{domain}] " if domain else ""
            conclusions.append(f"{prefix}{stripped}")

    if not conclusions:
        print("No conclusions extracted from MEMORY.md.")
        return 0

    print(f"Extracted {len(conclusions)} conclusions from MEMORY.md")

    # Push as agent-main's conclusions about owner
    total = push_conclusions(honcho, conclusions)
    print(f"Pushed {total} conclusions to Honcho.")
    return total


def main():
    parser = argparse.ArgumentParser(
        description="Push active conclusions to Honcho memory",
        usage="%(prog)s [conclusion] [options]",
    )
    parser.add_argument("conclusion", nargs="*", help="Conclusion text to push")
    parser.add_argument("--domain", "-d", help="Domain tag (business, people, preferences, etc.)")
    parser.add_argument("--about", "-a", default="owner",
                        help="Peer ID this conclusion is about (default: owner)")
    parser.add_argument("--observer", default="agent-main",
                        help="Observer peer ID (default: agent-main)")
    parser.add_argument("--stdin", action="store_true",
                        help="Read conclusions from stdin (one per line)")
    parser.add_argument("--search", "-s", help="Search conclusions by query")
    parser.add_argument("--delete", help="Delete a conclusion by ID")
    parser.add_argument("--import-memory", action="store_true",
                        help="Import MEMORY.md content as conclusions")
    parser.add_argument("--base-url", default=HONCHO_BASE_URL)
    parser.add_argument("--workspace", default=HONCHO_WORKSPACE)
    args = parser.parse_args()

    honcho = get_honcho(args.base_url, args.workspace)

    if args.search:
        search_conclusions(honcho, args.search, args.observer, args.about)
        return

    if args.delete:
        delete_conclusion(honcho, args.delete, args.observer, args.about)
        return

    if args.import_memory:
        import_memory_md(honcho)
        return

    # Collect conclusions from args or stdin
    contents = []
    if args.stdin:
        contents = [line.strip() for line in sys.stdin if line.strip()]
    elif args.conclusion:
        contents = [" ".join(args.conclusion)]
    else:
        parser.print_help()
        return

    total = push_conclusions(honcho, contents, args.observer, args.about, args.domain)
    print(f"Pushed {total} conclusion(s) to Honcho.")


if __name__ == "__main__":
    main()
