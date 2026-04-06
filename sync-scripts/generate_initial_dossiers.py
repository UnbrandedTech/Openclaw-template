#!/usr/bin/env python3
"""
generate_initial_dossiers.py -- Generate initial Obsidian dossiers and client profiles from Honcho data.

Creates person dossiers in ~/Documents/Obsidian Vault/People/ and
client company profiles in ~/Documents/Obsidian Vault/Clients/.

Usage:
  python3 generate_initial_dossiers.py                          # Generate all
  python3 generate_initial_dossiers.py --priority high          # High-priority people only
  python3 generate_initial_dossiers.py --type clients           # Clients only
  python3 generate_initial_dossiers.py --type people            # People only
  python3 generate_initial_dossiers.py --force                  # Overwrite existing files
  python3 generate_initial_dossiers.py --dry-run                # Preview without writing
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from shared import (
    WORKSPACE,
    PEOPLE_DIR,
    CLIENTS_DIR,
    get_honcho,
    load_json,
    call_llm,
    sanitize_id,
    USER_NAME,
    USER_TITLE,
)

# ── Config ───────────────────────────────────────────────────────────────────

DOSSIER_TEMPLATE_PATH = Path.home() / ".openclaw" / "workspace" / "references" / "dossier-template.md"
TEAM_JSON_PATH = WORKSPACE / "team.json"

RATE_LIMIT_SECONDS = 0.5


# ── Honcho queries ────────────────────────────────────────────────────────────

def get_person_context(honcho, peer_id: str, person_name: str) -> str:
    """Query Honcho for everything it knows about a person."""
    agent_peer = honcho.peer("agent-main")
    role_label = f"{USER_NAME} ({USER_TITLE})" if USER_TITLE else USER_NAME
    prompt = (
        f"Tell me everything you know about {person_name}. "
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


def get_company_context(honcho, company_name: str, channels: list, contacts: list) -> str:
    """Query Honcho for everything it knows about a client company.

    Queries the company's Slack channel sessions rather than a company peer
    (companies don't have Honcho peers, only people do).
    """
    agent_peer = honcho.peer("agent-main")

    contact_hint = ""
    if contacts:
        contact_hint = f" Key contacts: {', '.join(contacts)}."

    prompt = (
        f"Tell me everything you know about the company {company_name} and our relationship with them. "
        f"Include: what they do, active projects or engagements we have with them, "
        f"meeting cadence, Slack communication patterns, key decision makers, "
        f"deliverables and timelines, relationship health, and any notable context.{contact_hint} "
        f"Be specific. Skip anything you don't have data on."
    )

    # Try querying each channel session for this company
    for ch in channels:
        session_id = f"slack-{ch}"
        try:
            response = agent_peer.chat(prompt, session=session_id, reasoning_level="medium")
            result = str(response).strip()
            if result:
                return result
        except Exception:
            continue

    # Fallback: query without a specific target/session
    try:
        response = agent_peer.chat(prompt, reasoning_level="medium")
        return str(response).strip()
    except Exception as e:
        print(f"  warn: {company_name} honcho error: {e}", file=sys.stderr)
        return ""


# ── Dossier generation ────────────────────────────────────────────────────────

def load_dossier_template() -> str:
    """Load the dossier template from the references directory."""
    if DOSSIER_TEMPLATE_PATH.exists():
        return DOSSIER_TEMPLATE_PATH.read_text(encoding="utf-8", errors="replace")
    return ""


def build_person_prompt(person_name: str, person_info: dict, honcho_context: str, template: str, company: str = "") -> str:
    """Build the Flash prompt for generating a person dossier."""
    contact_type = person_info.get("type", "internal")
    priority = person_info.get("priority", "medium")

    company_note = ""
    if company:
        company_note = f"\nThis person works at the client company: {company}. Include a 'company' field in the YAML frontmatter.\n"

    prompt = (
        f"You are generating an Obsidian dossier for {person_name}.\n\n"
        f"Contact type: {contact_type}\n"
        f"Priority: {priority}\n"
        f"{company_note}\n"
        f"## Dossier Template\n\n"
        f"Follow this template format exactly:\n\n"
        f"{template}\n\n"
        f"## Raw Context from Memory\n\n"
        f"{honcho_context}\n\n"
        f"## Instructions\n\n"
        f"Format the raw context into a dossier using the template above.\n"
        f"Output YAML frontmatter followed by a markdown body.\n"
        f"Rules:\n"
        f"- Only include facts that are explicitly stated in the raw context.\n"
        f"- Do not infer or speculate about personal details.\n"
        f"- No em dashes. Use commas or periods instead.\n"
        f"- For fields with no data, omit them entirely rather than guessing.\n"
        f"- The 'last_updated' field should be set to today's date: {datetime.now().strftime('%Y-%m-%d')}.\n"
        f"- Output ONLY the dossier content (frontmatter + markdown). No explanations or wrapper.\n"
        f"- Do not wrap the output in a code fence.\n"
    )
    return prompt


def build_company_prompt(company_name: str, company_info: dict, honcho_context: str) -> str:
    """Build the Flash prompt for generating a client company profile."""
    contacts = company_info.get("contacts", [])
    contact_links = "\n".join(f'  - "[[{c}]]"' for c in contacts) if contacts else '  - "(none yet)"'
    company_type = company_info.get("type", "client")
    domain = company_info.get("domain", "")

    prompt = (
        f"You are generating an Obsidian company profile for {company_name}.\n\n"
        f"## Target Format\n\n"
        f"```yaml\n"
        f"---\n"
        f'company_name: "{company_name}"\n'
        f"type: {company_type}\n"
        f'domain: "{domain}"\n'
        f"key_contacts:\n"
        f"{contact_links}\n"
        f"relationship_status: active\n"
        f'last_updated: "{datetime.now().strftime("%Y-%m-%d")}"\n'
        f"---\n\n"
        f"# {company_name}\n\n"
        f"## Overview\n"
        f"What the company does, how they relate to us.\n\n"
        f"## Active Engagements\n"
        f"Current projects, deliverables, timelines.\n\n"
        f"## Communication\n"
        f"Slack channels, meeting cadence, key decision makers.\n\n"
        f"## Notes\n"
        f"Anything notable about the relationship.\n"
        f"```\n\n"
        f"## Raw Context from Memory\n\n"
        f"{honcho_context}\n\n"
        f"## Instructions\n\n"
        f"Format the raw context into a company profile using the target format above.\n"
        f"Output YAML frontmatter followed by a markdown body.\n"
        f"Rules:\n"
        f"- Only include facts that are explicitly stated in the raw context.\n"
        f"- No em dashes. Use commas or periods instead.\n"
        f"- Fill in sections where data exists. For sections with no data, write a brief placeholder like 'No data yet.'\n"
        f"- The 'last_updated' field should be set to today's date: {datetime.now().strftime('%Y-%m-%d')}.\n"
        f"- Output ONLY the profile content (frontmatter + markdown). No explanations or wrapper.\n"
        f"- Do not wrap the output in a code fence.\n"
    )
    return prompt


# ── File writing ──────────────────────────────────────────────────────────────

def write_dossier(path: Path, content: str, dry_run: bool = False) -> bool:
    """Write a dossier file, creating parent directories as needed."""
    if dry_run:
        print(f"  [dry-run] Would write: {path}")
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"  Wrote: {path}")
        return True
    except Exception as e:
        print(f"  ERROR writing {path}: {e}", file=sys.stderr)
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate initial Obsidian dossiers and client profiles from Honcho data."
    )
    parser.add_argument(
        "--priority",
        choices=["high", "medium", "all"],
        default="all",
        help="Filter people by priority level (default: all)",
    )
    parser.add_argument(
        "--type",
        choices=["people", "clients", "all"],
        default="all",
        dest="gen_type",
        help="What to generate: people dossiers, client profiles, or all (default: all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing dossier/profile files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be generated without writing files",
    )
    args = parser.parse_args()

    team = load_json(TEAM_JSON_PATH)
    tracked_people = team.get("tracked_people", {})
    clients = team.get("clients", {})

    template = load_dossier_template()
    if not template:
        print("  warn: dossier template not found, using basic format", file=sys.stderr)

    honcho = get_honcho()

    stats = {"people_written": 0, "people_skipped": 0, "clients_written": 0, "clients_skipped": 0, "errors": 0}

    # ── Generate person dossiers ───────────────────────────────────────────
    if args.gen_type in ("people", "all"):
        # Collect all people to process: tracked_people + client contacts
        people_to_process = {}

        # Add tracked people (filtered by priority)
        for name, info in tracked_people.items():
            if args.priority == "all" or info.get("priority") == args.priority:
                people_to_process[name] = {"info": info, "company": ""}

        # Add client contacts as people (skip if already tracked by name or peer_id)
        existing_peer_ids = {info.get("peer_id") for info in tracked_people.values()}
        for company_name, company_info in clients.items():
            for contact_name in company_info.get("contacts", []):
                contact_peer_id = sanitize_id(contact_name)
                # Skip if this person is already in tracked_people (by name or peer_id)
                if contact_name in people_to_process or contact_peer_id in existing_peer_ids:
                    continue
                contact_info = {
                    "type": "client",
                    "peer_id": contact_peer_id,
                    "priority": "medium",
                }
                people_to_process[contact_name] = {
                    "info": contact_info,
                    "company": company_name,
                }

        total_people = len(people_to_process)
        print(f"\n--- Generating person dossiers ({total_people} people) ---\n")

        for i, (person_name, entry) in enumerate(people_to_process.items(), 1):
            info = entry["info"]
            company = entry["company"]
            md_path = PEOPLE_DIR / f"{person_name}.md"

            if md_path.exists() and not args.force:
                print(f"  [{i}/{total_people}] skip (exists): {person_name}")
                stats["people_skipped"] += 1
                continue

            print(f"  [{i}/{total_people}] {person_name}... ", end="", flush=True)
            context = get_person_context(honcho, info["peer_id"], person_name)

            if not context:
                print("no data, skipping")
                stats["errors"] += 1
                continue

            content = call_llm(
                build_person_prompt(person_name, info, context, template, company=company),
                role="fast",
            )

            if not content:
                print("LLM returned empty")
                stats["errors"] += 1
                continue

            if write_dossier(md_path, content, dry_run=args.dry_run):
                stats["people_written"] += 1
                print("done")
            else:
                print("write failed")

            time.sleep(RATE_LIMIT_SECONDS)

    # ── Generate client company profiles ───────────────────────────────────
    if args.gen_type in ("clients", "all"):
        total_clients = len(clients)
        print(f"\n--- Generating client profiles ({total_clients} clients) ---\n")

        for i, (company_name, company_info) in enumerate(clients.items(), 1):
            md_path = CLIENTS_DIR / f"{company_name}.md"

            if md_path.exists() and not args.force:
                print(f"  [{i}/{total_clients}] skip (exists): {company_name}")
                stats["clients_skipped"] += 1
                continue

            channels = company_info.get("channels", [])
            contacts = company_info.get("contacts", [])

            print(f"  [{i}/{total_clients}] {company_name}... ", end="", flush=True)
            context = get_company_context(honcho, company_name, channels, contacts)

            if not context:
                print("no data, skipping")
                stats["errors"] += 1
                continue

            content = call_llm(
                build_company_prompt(company_name, company_info, context),
                role="fast",
            )

            if not content:
                print("LLM returned empty")
                stats["errors"] += 1
                continue

            if write_dossier(md_path, content, dry_run=args.dry_run):
                stats["clients_written"] += 1
                print("done")
            else:
                print("write failed")

            time.sleep(RATE_LIMIT_SECONDS)

    # ── Summary ────────────────────────────────────────────────────────────
    print("\n--- Done ---")
    print(f"  People written:  {stats['people_written']}")
    print(f"  People skipped:  {stats['people_skipped']}")
    print(f"  Clients written: {stats['clients_written']}")
    print(f"  Clients skipped: {stats['clients_skipped']}")
    print(f"  Errors:          {stats['errors']}")


if __name__ == "__main__":
    main()
