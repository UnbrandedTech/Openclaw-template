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
    check_llm_ready,
    sanitize_id,
    USER_NAME,
    USER_TITLE,
)

# ── Config ───────────────────────────────────────────────────────────────────

DOSSIER_TEMPLATE_PATH = Path.home() / ".openclaw" / "workspace" / "references" / "dossier-template.md"
TEAM_JSON_PATH = WORKSPACE / "team.json"

RATE_LIMIT_SECONDS = 0.5


# ── Honcho queries ────────────────────────────────────────────────────────────

def _get_peer_messages(honcho, peer_id: str, max_messages: int = 80) -> list[str]:
    """Fetch recent messages involving a peer from their sessions.

    Uses the session context API which returns recent messages with summaries.
    Falls back to listing sessions by name from the session_peers table.
    """
    messages = []

    # Get sessions this peer is in
    try:
        peer = honcho.peer(peer_id)
        sessions_resp = peer.sessions(page=1, size=10)
        session_list = getattr(sessions_resp, "items", None)
        if session_list is None:
            session_list = sessions_resp if isinstance(sessions_resp, list) else []
    except Exception:
        session_list = []

    for sess in session_list:
        sess_id = getattr(sess, "id", getattr(sess, "name", str(sess)))
        try:
            session = honcho.session(sess_id)
            msgs_resp = session.messages(page=1, size=20, reverse=True)
            msg_items = getattr(msgs_resp, "items", msgs_resp) if hasattr(msgs_resp, "items") else []
            for msg in msg_items:
                content = getattr(msg, "content", "")
                if content and len(content) > 10:
                    messages.append(content)
                if len(messages) >= max_messages:
                    return messages
        except Exception:
            continue

    return messages


def get_person_context(honcho, peer_id: str, person_name: str) -> str:
    """Query Honcho for everything it knows about a person.

    Tries multiple approaches in order:
      1. Peer metadata — structured profile facts
      2. peer.chat() — agentic search (requires deriver to have run)
      3. Raw session messages — direct message content for LLM summarization
    """
    parts = []

    # Try to get structured metadata from the peer
    try:
        peer = honcho.peer(peer_id)
        meta = getattr(peer, "metadata", None) or {}
        if meta:
            meta_lines = []
            if meta.get("title"):
                meta_lines.append(f"Title/Role: {meta['title']}")
            if meta.get("email"):
                meta_lines.append(f"Email: {meta['email']}")
            if meta.get("type"):
                meta_lines.append(f"Type: {meta['type']}")
            if meta.get("is_guest"):
                meta_lines.append("Slack status: Guest/External user")
            if meta_lines:
                parts.append("## Profile\n" + "\n".join(f"- {line}" for line in meta_lines))
    except Exception:
        pass

    # Try peer.chat() for deep knowledge (works after deriver processes messages)
    try:
        agent_peer = honcho.peer("agent-main")
        role_label = f"{USER_NAME} ({USER_TITLE})" if USER_TITLE else USER_NAME
        prompt = (
            f"Tell me everything you know about {person_name}. "
            f"Include: their role, what they're working on, recent conversations, "
            f"their relationship with {role_label}, and any open threads."
        )
        response = agent_peer.chat(prompt, target=peer_id, reasoning_level="medium")
        result = str(response).strip()
        if result:
            parts.append("## Knowledge\n" + result)
    except Exception:
        pass

    # Fallback: read raw messages from the peer's sessions
    if len(parts) <= 1:  # Only metadata, no knowledge
        messages = _get_peer_messages(honcho, peer_id)
        if messages:
            # Truncate to fit in an LLM prompt (~20K chars)
            sample = []
            total_chars = 0
            for msg in messages:
                if total_chars + len(msg) > 20000:
                    break
                sample.append(msg)
                total_chars += len(msg)
            if sample:
                parts.append("## Recent Messages\n" + "\n---\n".join(sample))

    if not parts:
        print(f"  warn: no data for {person_name} in Honcho", file=sys.stderr)

    return "\n\n".join(parts)


def get_company_context(honcho, company_name: str, channels: list, contacts: list) -> str:
    """Gather context about a client company from their Slack channel messages.

    Reads messages directly from the company's channel sessions and returns
    them as raw context for the LLM to synthesize into a profile.
    """
    parts = []

    contact_hint = ""
    if contacts:
        contact_hint = f"Key contacts: {', '.join(contacts)}"
        parts.append(f"## Known Contacts\n{contact_hint}")

    # Read messages from the company's Slack channel(s)
    all_messages = []
    for ch in channels:
        session_id = f"slack-{ch}"
        try:
            session = honcho.session(session_id)
            msgs = session.messages(page=1, size=50, reverse=True)
            items = getattr(msgs, "items", msgs) if hasattr(msgs, "items") else []
            for msg in items:
                content = getattr(msg, "content", "")
                if content and len(content) > 10:
                    all_messages.append(content)
        except Exception:
            continue

    if all_messages:
        # Truncate to fit in an LLM prompt
        sample = []
        total_chars = 0
        for msg in all_messages:
            if total_chars + len(msg) > 20000:
                break
            sample.append(msg)
            total_chars += len(msg)
        if sample:
            parts.append(f"## Slack Channel Messages ({len(sample)} messages)\n" + "\n---\n".join(sample))

    if not parts:
        print(f"  warn: no channel data for {company_name}", file=sys.stderr)

    return "\n\n".join(parts)


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
        f"The output must start with YAML frontmatter (between --- lines) followed by markdown:\n\n"
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
        f"Anything notable about the relationship.\n\n"
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

def _strip_code_fences(content: str) -> str:
    """Remove wrapping code fences that LLMs sometimes add."""
    stripped = content.strip()
    if stripped.startswith("```"):
        # Remove opening fence (```yaml, ```markdown, ```, etc.)
        first_newline = stripped.index("\n") if "\n" in stripped else len(stripped)
        stripped = stripped[first_newline + 1:]
    if stripped.rstrip().endswith("```"):
        stripped = stripped.rstrip()[:-3].rstrip()
    return stripped


def write_dossier(path: Path, content: str, dry_run: bool = False) -> bool:
    """Write a dossier file, creating parent directories as needed."""
    content = _strip_code_fences(content)
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

    if not args.dry_run:
        check_llm_ready()

    team = load_json(TEAM_JSON_PATH)
    tracked_people = team.get("tracked_people", {})
    clients = team.get("clients", {})

    template = load_dossier_template()
    if not template:
        print("  warn: dossier template not found, using basic format", file=sys.stderr)

    honcho = get_honcho()

    # Build a UID -> Honcho peer_id lookup from discovered profiles
    # (Honcho peers are created from display names which may differ from team.json names)
    users_cache_path = WORKSPACE / "slack_messages" / ".users_cache.json"
    users_cache = load_json(users_cache_path)
    uid_to_peer_id = {}
    for uid, name in users_cache.items():
        if uid.startswith("_"):
            continue
        uid_to_peer_id[uid] = sanitize_id(name) if isinstance(name, str) and name != uid else uid.lower()

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

            # Try all known aliases for this person when querying Honcho
            peer_id = info["peer_id"]
            aliases = info.get("aliases", [peer_id])
            # Also add UID-resolved display name if not already in aliases
            slack_uid = info.get("slack_uid", "")
            if slack_uid and slack_uid in uid_to_peer_id:
                uid_peer = uid_to_peer_id[slack_uid]
                if uid_peer not in aliases:
                    aliases = [uid_peer] + list(aliases)

            context = ""
            for candidate in aliases:
                context = get_person_context(honcho, candidate, person_name)
                if context.strip():
                    if candidate != peer_id:
                        print(f"[resolved {peer_id}->{candidate}] ", end="", flush=True)
                    break

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

            # Handle both "channel" (string) and "channels" (list) formats
            ch = company_info.get("channels", company_info.get("channel", []))
            channels = [ch] if isinstance(ch, str) else ch
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
