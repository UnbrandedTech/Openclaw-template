#!/usr/bin/env python3
"""
honcho_obsidian_sync.py — Bidirectional sync between Obsidian vault and Honcho memory.

Forward sync (Obsidian -> Honcho):
  - People dossiers -> Honcho peers + peer cards + session per person
  - Client profiles -> Honcho peers (type: client) + session per client
  - Daily Notes -> Session per day
  - Reference / Projects -> Session per file

Reverse sync (Honcho -> Obsidian):
  - Reads Honcho peer cards (maintained by the deriver from Slack messages etc.)
  - Compares with existing dossier content
  - Appends new insights to the dossier's "## Honcho Insights" section

Usage:
  python3 honcho_obsidian_sync.py [--dry-run] [--verbose] [--reset]
  python3 honcho_obsidian_sync.py --update-dossiers [--dry-run] [--verbose]
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from honcho import Honcho
except ImportError:
    print("ERROR: honcho-ai not installed. Run: pip3 install honcho-ai")
    sys.exit(1)

VAULT_PATH = Path.home() / "Documents" / "Obsidian Vault"
PEOPLE_DIR = VAULT_PATH / "👥 People"
MESSAGES_DIR = Path.home() / ".openclaw" / "workspace" / "slack_messages"
SYNC_STATE_FILE = MESSAGES_DIR / ".honcho_obsidian_state.json"

HONCHO_BASE_URL = "http://localhost:18790"
HONCHO_WORKSPACE = "openclaw"

CATEGORIES = {
    "people": "👥 People",
    "clients": "🏢 Clients",
    "daily": "📋 Daily Notes",
    "reference": "📚 Reference",
    "projects": "🏃 Active Projects",
}

SKIP_DIRS = {"🤖 Jeff", "🗄️ Archive", "💡 Ideas"}


def load_json(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_json(path: Path, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def sanitize_id(name: str) -> str:
    """Convert a filename/name to a valid Honcho ID."""
    pid = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-").lower()
    pid = re.sub(r"-{2,}", "-", pid)
    return pid or "unknown"


def scan_vault() -> list[dict]:
    """Scan the vault for markdown files, categorizing each."""
    files = []
    for cat_key, subdir in CATEGORIES.items():
        cat_path = VAULT_PATH / subdir
        if not cat_path.is_dir():
            continue
        for md_file in sorted(cat_path.glob("*.md")):
            files.append({
                "path": md_file,
                "category": cat_key,
                "name": md_file.stem,
                "mtime": md_file.stat().st_mtime,
            })
    return files


def get_changed_files(files: list[dict], state: dict) -> list[dict]:
    """Filter to files that changed since last sync."""
    changed = []
    for f in files:
        key = str(f["path"])
        last_mtime = state.get(key, {}).get("mtime", 0)
        if f["mtime"] > last_mtime:
            changed.append(f)
    return changed


def read_file(path: Path) -> str:
    """Read a markdown file, truncating to 24000 chars if needed."""
    content = path.read_text(encoding="utf-8", errors="replace")
    if len(content) > 24000:
        content = content[:24000] + "\n\n[... truncated]"
    return content


# ---------------------------------------------------------------------------
# Peer card extraction from dossiers
# ---------------------------------------------------------------------------

def extract_peer_card(content: str, person_name: str) -> list[str]:
    """Extract key facts from a dossier markdown file as a peer card (list of strings)."""
    facts = []
    lines = content.split("\n")

    # Extract role/title line (e.g., "**Role:** Co-founder, Marathon Data")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("**Role:**"):
            role = stripped.replace("**Role:**", "").strip()
            facts.append(f"{person_name}: {role}")
        elif stripped.startswith("**Rate:**"):
            facts.append(stripped.replace("**", ""))
        elif stripped.startswith("**Started:**"):
            facts.append(f"Started: {stripped.replace('**Started:**', '').strip()}")
        elif stripped.startswith("**GitHub:**"):
            facts.append(f"GitHub: {stripped.replace('**GitHub:**', '').strip()}")

    # Extract sections by header
    sections = {}
    current_section = None
    current_lines = []
    for line in lines:
        if line.startswith("## "):
            if current_section:
                sections[current_section] = current_lines
            current_section = line[3:].strip()
            current_lines = []
        elif current_section:
            current_lines.append(line)
    if current_section:
        sections[current_section] = current_lines

    # Relationship section -> single summary fact
    if "Relationship" in sections:
        rel_text = " ".join(l.strip() for l in sections["Relationship"] if l.strip())
        if rel_text:
            # Take first 200 chars as relationship summary
            facts.append(f"Relationship: {rel_text[:200]}")

    # "What They Talk About" / "What They're Working On" -> bullet facts
    for key in ("What They Talk About", "What They're Working On"):
        if key in sections:
            for line in sections[key]:
                stripped = line.strip()
                if stripped.startswith("- "):
                    fact = stripped[2:].strip()
                    if fact:
                        facts.append(fact)

    # Notes section -> bullet facts
    if "Notes" in sections:
        for line in sections["Notes"]:
            stripped = line.strip()
            if stripped.startswith("- "):
                fact = stripped[2:].strip()
                if fact:
                    facts.append(fact)

    # Pattern sections -> first sentence as fact
    for section_name, section_lines in sections.items():
        if section_name.startswith("Pattern:"):
            text = " ".join(l.strip() for l in section_lines if l.strip())
            if text:
                # First sentence
                first_sent = text.split(". ")[0] + "."
                facts.append(f"{section_name}: {first_sent[:200]}")

    # Cap at 40 facts
    return facts[:40]


# ---------------------------------------------------------------------------
# Forward sync: Obsidian -> Honcho
# ---------------------------------------------------------------------------

# Module-level cache for card facts we set (peer_id -> set of fact strings)
_card_facts_cache: dict[str, set] = {}


def sync_people(honcho, files: list[dict], author_peer, dry_run: bool = False):
    """Sync people dossiers: create peer + set peer card + session per person."""
    count = 0
    for f in files:
        name = f["name"]
        peer_id = sanitize_id(name)
        session_id = f"obsidian-people-{peer_id}"
        content = read_file(f["path"])

        # Extract peer card facts
        card_facts = extract_peer_card(content, name)

        if dry_run:
            print(f"  [people] {name} -> peer:{peer_id} ({len(card_facts)} card facts, {len(content)} chars)")
            count += 1
            continue

        peer = honcho.peer(peer_id, metadata={
            "display_name": name,
            "source": "obsidian",
            "category": "person",
        })

        # Set the peer card from dossier facts and track what we set
        if card_facts:
            try:
                peer.set_card(card_facts)
                # Save our card facts in state so reverse sync can exclude them
                state_key = f"_card_facts_{peer_id}"
                # Will be saved to state file later in main()
                _card_facts_cache[peer_id] = set(card_facts)
                print(f"  [people] {name}: set {len(card_facts)} card facts")
            except Exception as e:
                print(f"  [people] {name}: card error: {e}")

        session = honcho.session(session_id, metadata={
            "source": "obsidian",
            "category": "people",
            "subject": name,
            "vault_path": str(f["path"]),
        })
        session.add_peers([author_peer, peer])

        msg = author_peer.message(
            content,
            metadata={
                "file": f["name"] + ".md",
                "updated_at": datetime.fromtimestamp(f["mtime"], tz=timezone.utc).isoformat(),
                "type": "dossier",
            },
            created_at=datetime.fromtimestamp(f["mtime"], tz=timezone.utc),
        )
        session.add_messages([msg])
        print(f"  [people] {name}: synced")
        count += 1
        time.sleep(0.3)
    return count


def sync_clients(honcho, files: list[dict], author_peer, dry_run: bool = False):
    """Sync client profiles: create client peer + session."""
    count = 0
    for f in files:
        name = f["name"]
        peer_id = f"client-{sanitize_id(name)}"
        session_id = f"obsidian-client-{sanitize_id(name)}"
        content = read_file(f["path"])

        if dry_run:
            print(f"  [client] {name} -> peer:{peer_id}, session:{session_id} ({len(content)} chars)")
            count += 1
            continue

        peer = honcho.peer(peer_id, metadata={
            "display_name": name,
            "source": "obsidian",
            "category": "client",
        })

        session = honcho.session(session_id, metadata={
            "source": "obsidian",
            "category": "client",
            "subject": name,
            "vault_path": str(f["path"]),
        })
        session.add_peers([author_peer, peer])

        msg = author_peer.message(
            content,
            metadata={
                "file": f["name"] + ".md",
                "updated_at": datetime.fromtimestamp(f["mtime"], tz=timezone.utc).isoformat(),
                "type": "client_profile",
            },
            created_at=datetime.fromtimestamp(f["mtime"], tz=timezone.utc),
        )
        session.add_messages([msg])
        print(f"  [client] {name}: synced")
        count += 1
        time.sleep(0.3)
    return count


def sync_documents(honcho, files: list[dict], category: str, author_peer, dry_run: bool = False):
    """Sync daily notes, reference docs, or project docs as sessions."""
    count = 0
    for f in files:
        name = f["name"]
        session_id = f"obsidian-{category}-{sanitize_id(name)}"
        content = read_file(f["path"])

        if dry_run:
            print(f"  [{category}] {name} -> session:{session_id} ({len(content)} chars)")
            count += 1
            continue

        session = honcho.session(session_id, metadata={
            "source": "obsidian",
            "category": category,
            "subject": name,
            "vault_path": str(f["path"]),
        })
        session.add_peers([author_peer])

        msg = author_peer.message(
            content,
            metadata={
                "file": f["name"] + ".md",
                "updated_at": datetime.fromtimestamp(f["mtime"], tz=timezone.utc).isoformat(),
                "type": f"{category}_note",
            },
            created_at=datetime.fromtimestamp(f["mtime"], tz=timezone.utc),
        )
        session.add_messages([msg])
        print(f"  [{category}] {name}: synced")
        count += 1
        time.sleep(0.3)
    return count


# ---------------------------------------------------------------------------
# Reverse sync: Honcho -> Obsidian dossiers
# ---------------------------------------------------------------------------

def get_dossier_existing_content(path: Path) -> str:
    """Read dossier and return content, stripping any existing Honcho Insights section."""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def strip_markdown(text: str) -> str:
    """Strip common markdown formatting for comparison."""
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)  # bold
    text = re.sub(r"\*([^*]+)\*", r"\1", text)  # italic
    text = re.sub(r"`([^`]+)`", r"\1", text)  # code
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # links
    text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)  # headers
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)  # list items
    return text.lower()


def find_new_insights(honcho_card: list[str], dossier_content: str) -> list[str]:
    """Find card facts from Honcho that aren't already reflected in the dossier.

    Uses multiple overlap checks to avoid flagging facts we extracted ourselves.
    """
    if not honcho_card:
        return []
    cleaned_dossier = strip_markdown(dossier_content)
    new_facts = []
    for fact in honcho_card:
        fact_lower = fact.lower().strip()
        if len(fact_lower) < 15:
            continue

        # Split fact into meaningful chunks and check each
        # If any 30-char window from the fact appears in the dossier, skip it
        found = False
        words = fact_lower.split()
        # Check overlapping windows of ~5 words
        for i in range(len(words)):
            window = " ".join(words[i:i+5])
            if len(window) >= 20 and window in cleaned_dossier:
                found = True
                break

        # Also check if the core content (strip prefix like "Name:") is present
        if not found and ":" in fact_lower:
            core = fact_lower.split(":", 1)[1].strip()
            if len(core) >= 20:
                for i in range(0, len(core) - 20, 10):
                    if core[i:i+25] in cleaned_dossier:
                        found = True
                        break

        if not found:
            new_facts.append(fact)
    return new_facts


def detect_conflict(fact: str, dossier_content: str) -> bool:
    """Check if a new fact potentially conflicts with existing dossier content.

    Heuristic: if the fact shares significant keyword overlap with an existing
    dossier line but wasn't caught by substring matching, it's likely an update
    or contradiction (e.g., "Preston now supports eng cuts" vs dossier's
    "Preston has brought up cutting eng multiple times").
    """
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "before", "after", "about", "between", "under", "and", "but", "or",
        "not", "no", "that", "this", "it", "its", "they", "them", "their",
        "he", "she", "his", "her", "who", "which", "what", "when", "where",
        "how", "than", "then", "also", "just", "very", "more",
    }

    fact_words = {w for w in fact.lower().split() if w not in stop_words and len(w) > 2}
    if len(fact_words) < 3:
        return False

    # Check each non-empty line in the dossier for keyword overlap
    for line in dossier_content.split("\n"):
        line_clean = strip_markdown(line).strip()
        if len(line_clean) < 15:
            continue
        line_words = {w for w in line_clean.split() if w not in stop_words and len(w) > 2}
        if not line_words:
            continue
        overlap = fact_words & line_words
        # If >40% of the fact's keywords appear in this line, it's related
        if len(overlap) >= max(3, len(fact_words) * 0.4):
            return True

    return False


def get_conclusion_source(honcho, peer_id: str, fact_content: str) -> str:
    """Try to find the source session/channel for a conclusion by querying matching conclusions."""
    try:
        peer = honcho.peer("agent-main")
        scope = peer.conclusions_of(peer_id)
        results = scope.query(fact_content, top_k=1, distance=0.3)
        if results and results[0].session_id:
            sid = results[0].session_id
            # Map session ID back to channel name if it's a slack session
            if sid.startswith("slack-"):
                return f"#{sid[6:]}"
            return sid
    except Exception:
        pass
    return ""


def update_dossier_with_insights(path: Path, new_facts: list[dict],
                                  dry_run: bool = False) -> bool:
    """Append new Honcho insights to a dossier file. Returns True if updated.

    Each fact is a dict with keys: content, is_conflict, source.
    Conflicts get a warning flag with source attribution.
    """
    if not new_facts:
        return False

    content = path.read_text(encoding="utf-8", errors="replace")
    today = datetime.now().strftime("%Y-%m-%d")

    # Build the new block
    lines = []
    for fact in new_facts:
        if fact["is_conflict"]:
            source_note = f" (from {fact['source']})" if fact["source"] else ""
            lines.append(f"- \u26a0\ufe0f {fact['content']}{source_note}")
        else:
            lines.append(f"- {fact['content']}")

    # Check if there's already a Honcho Insights section
    insights_header = "## Honcho Insights"
    if insights_header in content:
        insert_point = content.index(insights_header) + len(insights_header)
        rest = content[insert_point:]
        next_header = re.search(r"\n## ", rest)
        if next_header:
            section_end = insert_point + next_header.start()
        else:
            section_end = len(content)

        new_block = f"\n\n### {today}\n" + "\n".join(lines) + "\n"
        content = content[:section_end] + new_block + content[section_end:]
    else:
        new_block = f"\n{insights_header}\n\n### {today}\n" + "\n".join(lines) + "\n"
        content = content.rstrip() + "\n" + new_block

    if not dry_run:
        path.write_text(content, encoding="utf-8")
    return True


def update_dossiers(honcho, state: dict, dry_run: bool = False, verbose: bool = False):
    """Reverse sync: read Honcho peer cards and update Obsidian dossiers.

    Only surfaces facts that Honcho learned on its own (from Slack, etc.),
    NOT facts we extracted from the dossier and set as the card.
    Flags potential conflicts with existing dossier content and includes
    source attribution for manual reconciliation.
    """
    if not PEOPLE_DIR.is_dir():
        print("No People directory found.")
        return 0

    updated = 0
    for md_file in sorted(PEOPLE_DIR.glob("*.md")):
        person_name = md_file.stem
        peer_id = sanitize_id(person_name)
        dossier_content = get_dossier_existing_content(md_file)

        try:
            peer = honcho.peer(peer_id)
            honcho_card = peer.get_card()
        except Exception as e:
            if verbose:
                print(f"  [skip] {person_name}: no Honcho peer ({e})")
            continue

        if not honcho_card:
            if verbose:
                print(f"  [skip] {person_name}: no Honcho card yet")
            continue

        # Filter out facts we set ourselves from the dossier
        our_facts = set(state.get(f"_card_{peer_id}", []))
        honcho_only = [f for f in honcho_card if f not in our_facts]

        new_raw = find_new_insights(honcho_only, dossier_content)
        if not new_raw:
            if verbose:
                print(f"  [ok] {person_name}: card has {len(honcho_card)} facts, none new")
            continue

        # Check each new fact for conflicts and get source attribution
        enriched_facts = []
        conflicts = 0
        for fact in new_raw:
            is_conflict = detect_conflict(fact, dossier_content)
            source = ""
            if is_conflict:
                conflicts += 1
                source = get_conclusion_source(honcho, peer_id, fact)
            enriched_facts.append({
                "content": fact,
                "is_conflict": is_conflict,
                "source": source,
            })

        if dry_run:
            print(f"  [would update] {person_name}: {len(enriched_facts)} new insights ({conflicts} conflicts)")
            for ef in enriched_facts[:5]:
                flag = "\u26a0\ufe0f " if ef["is_conflict"] else ""
                src = f" (from {ef['source']})" if ef["source"] else ""
                print(f"    + {flag}{ef['content']}{src}")
            if len(enriched_facts) > 5:
                print(f"    ... and {len(enriched_facts) - 5} more")
            updated += 1
        else:
            if update_dossier_with_insights(md_file, enriched_facts):
                conflict_note = f" ({conflicts} flagged)" if conflicts else ""
                print(f"  [updated] {person_name}: +{len(enriched_facts)} insights{conflict_note}")
                updated += 1

        time.sleep(0.2)

    return updated


# ---------------------------------------------------------------------------
# Deep reconciliation: Honcho peer.chat() for key people (Friday weekly)
# ---------------------------------------------------------------------------

# Only run deep reconciliation for people where relationship dynamics shift fast
DEEP_RECONCILE_PEERS = {
    "tom-montgomery": "Tom Montgomery",
    "preston-rutherford": "Preston Rutherford",
    "ashley-spencer": "Ashley Spencer",
    "phil": "Phil",
    "theja-talla": "Theja Talla",
    "chris-dolan": "Chris Dolan",
}


def deep_reconcile_dossiers(honcho, dry_run: bool = False, verbose: bool = False):
    """Weekly deep reconciliation using peer.chat() for key people.

    Asks Honcho's dialectic agent to synthesize what's changed about each person,
    then appends a structured update to their dossier. More expensive (LLM call
    per person) but catches nuanced shifts that card-diffing misses.
    """
    if not PEOPLE_DIR.is_dir():
        print("No People directory found.")
        return 0

    agent_peer = honcho.peer("agent-main")
    updated = 0

    for peer_id, display_name in DEEP_RECONCILE_PEERS.items():
        md_file = PEOPLE_DIR / f"{display_name}.md"
        if not md_file.exists():
            if verbose:
                print(f"  [skip] {display_name}: no dossier file")
            continue

        dossier_content = md_file.read_text(encoding="utf-8", errors="replace")

        # Ask Honcho what's changed, providing the current dossier as context
        prompt = (
            f"I have a dossier on {display_name} at the company. "
            f"Based on recent Slack conversations and other signals, what has "
            f"changed or is new about {display_name} in the last week? "
            f"Focus on: role changes, relationship shifts, new responsibilities, "
            f"notable opinions or decisions, and anything that contradicts what "
            f"the dossier currently says. Be specific and cite the context "
            f"(channel, topic) where you learned each thing. "
            f"If nothing meaningful has changed, say 'No significant changes.'"
        )

        if dry_run:
            print(f"  [would reconcile] {display_name} via peer.chat()")
            updated += 1
            continue

        try:
            response = agent_peer.chat(
                prompt,
                target=peer_id,
                reasoning_level="medium",
            )
        except Exception as e:
            print(f"  [error] {display_name}: peer.chat() failed: {e}")
            continue

        response_text = str(response).strip()

        # Skip if nothing meaningful
        if not response_text or "no significant changes" in response_text.lower():
            if verbose:
                print(f"  [ok] {display_name}: no significant changes")
            continue

        # Append to dossier under a weekly reconciliation section
        today = datetime.now().strftime("%Y-%m-%d")
        week_header = f"### Weekly Reconciliation ({today})"

        insights_header = "## Honcho Insights"
        if insights_header in dossier_content:
            insert_point = dossier_content.index(insights_header) + len(insights_header)
            rest = dossier_content[insert_point:]
            next_header = re.search(r"\n## ", rest)
            section_end = insert_point + (next_header.start() if next_header else len(rest))

            new_block = f"\n\n{week_header}\n{response_text}\n"
            dossier_content = (
                dossier_content[:section_end] + new_block + dossier_content[section_end:]
            )
        else:
            new_block = f"\n{insights_header}\n\n{week_header}\n{response_text}\n"
            dossier_content = dossier_content.rstrip() + "\n" + new_block

        md_file.write_text(dossier_content, encoding="utf-8")
        # Truncate preview for output
        preview = response_text[:120].replace("\n", " ")
        print(f"  [reconciled] {display_name}: {preview}...")
        updated += 1
        time.sleep(1)  # rate limit between LLM calls

    return updated


# ---------------------------------------------------------------------------
# Bot peer management
# ---------------------------------------------------------------------------

BOT_PATTERNS = {
    "Bot", "Agent", "Slack Agent", "Oracle", "Elementary",
    "Airflow", "Marathon", "GitHub", "Linear", "Jira", "Sentry",
    "GithubActionsSlackBot", "Google Calendar", "Google Drive",
    "Grain", "Loom", "HubSpot", "Figma", "GIF Monger", "Canva",
    "Notion", "Zapier", "Acquire.com", "Apollo.io", "Tactiq",
    "Metabase", "PostHog", "Intercom Notifications", "Statuspage",
    "Featurebase", "Customer.io", "Adobe Express", "Cohere",
    "Asana", "Calendly", "Reclaim", "Gumloop", "Lindy", "ChatPRD",
    "Cursor", "Claude Desktop", "Claude", "Novu", "Motion",
    "OneDrive and SharePoint", "Manus", "Fathom", "Slackbot",
    "OpenClaw", "dv-bot-test", "KS test", "TestUploader",
    "elementary_reports_local",
}

BOT_UIDS = {
    "U05R5F8CL66", "U05RSPHRANB", "U05TSNUD62H", "U05UM0LA31P",
    "U05UZSV40GP", "U061FENT6LC", "U061LT2RJG4", "U0627HFQF7A",
    "U063EQK072P", "U065J646Y8G", "U066SC5M6DD", "U06G4QE60HW",
    "U06JVPGGDR8", "U06K93ETKBJ", "U06PTFS7AQZ", "U06UE3LBYM6",
    "U074XK2KHA7", "U077AT77BHB", "U077DMYCBSP", "U07G04LRQ93",
    "U07PB67HV7E", "U07RP4JHRM2", "U07T8D9EZGT", "U07T98XNYA3",
    "U07U1JC7YAE", "U07UNN426QJ", "U07V1CZMJCR", "U084N1JAZFC",
    "U087V78RTNU", "U088Y2U808K", "U08BD2VKL90", "U08F8AV3407",
    "U08K1HLE872", "U08T1J0GEBH", "U09744MTSJK", "U09CKMKT1S8",
    "U09J18CN6MQ", "U09P1HM2HJ8", "U09PVPWQRM4", "U09SGV6P695",
    "U09V8V4DFGX", "U0A06MF6Q75", "U0A06MKP9EF", "U0A06MUS0RH",
    "U0A0706BW4D", "U0A09KQA16Z", "U0A09KUSN0M", "U0A0ACGJVA6",
    "U0A0CKX2REG", "U0A0E0BUNRJ", "U0A0GLME78C", "U0A14N4A64Q",
    "U0A17C6949W", "U0A17D6SA6L", "U0ABUQG4MKM", "U0ACSP1DM6E",
    "U0ADQA0KR1P", "U0AK9KHKYK1", "U0AP6CCS6F7", "U0APV7TV5M5",
    "U0529QJ71A8", "U0529QZA65A", "U0574RW9SRH", "U058HC31EQJ",
    "U087R1USW56", "U093H7DB27R", "U09SC9G3VMM", "U0A9F049U58",
    "USLACKBOT",
}


def disable_bot_observation(honcho, verbose: bool = False):
    """Set observe_me=false on known bot peers to save reasoning compute."""
    disabled = 0
    for uid in BOT_UIDS:
        peer_id = sanitize_id(uid)
        try:
            honcho.peer(peer_id, configuration={"observe_me": False})
            disabled += 1
        except Exception:
            pass
    if disabled:
        print(f"  Disabled observation on {disabled} bot peers")
    return disabled


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Bidirectional Obsidian/Honcho sync")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be synced")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output")
    parser.add_argument("--reset", action="store_true", help="Reset state and re-sync all files")
    parser.add_argument("--update-dossiers", action="store_true",
                        help="Reverse sync: update dossiers from Honcho insights")
    parser.add_argument("--deep-reconcile", action="store_true",
                        help="Weekly deep reconciliation via peer.chat() for key people (expensive)")
    parser.add_argument("--skip-forward", action="store_true",
                        help="Skip forward sync (Obsidian -> Honcho)")
    parser.add_argument("--disable-bots", action="store_true",
                        help="Set observe_me=false on known bot peers")
    parser.add_argument("--base-url", default=HONCHO_BASE_URL)
    parser.add_argument("--workspace", default=HONCHO_WORKSPACE)
    args = parser.parse_args()

    if not VAULT_PATH.is_dir():
        print(f"ERROR: Vault not found at {VAULT_PATH}")
        sys.exit(1)

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting Honcho Obsidian sync...")

    # Connect to Honcho (needed for both directions)
    honcho = Honcho(base_url=args.base_url, workspace_id=args.workspace)

    # Disable bot observation if requested
    if args.disable_bots:
        disable_bot_observation(honcho, args.verbose)

    # --- Forward sync: Obsidian -> Honcho ---
    if not args.skip_forward:
        state = {} if args.reset else load_json(SYNC_STATE_FILE)
        if args.reset:
            print("  Reset: re-syncing all files")

        all_files = scan_vault()
        changed = get_changed_files(all_files, state)

        if changed:
            print(f"Found {len(changed)} changed files (of {len(all_files)} total)")

            by_cat = {}
            for f in changed:
                by_cat.setdefault(f["category"], []).append(f)

            if args.dry_run:
                for cat, files in by_cat.items():
                    for f in files:
                        content = read_file(f["path"])
                        card_note = ""
                        if cat == "people":
                            facts = extract_peer_card(content, f["name"])
                            card_note = f", {len(facts)} card facts"
                        print(f"  [{cat}] {f['name']} ({len(content)} chars{card_note})")
                print(f"[DRY RUN] Would sync {len(changed)} files to Honcho.")
            else:
                author_peer = honcho.peer("james-kenaley", metadata={
                    "display_name": "James Kenaley",
                    "source": "obsidian",
                    "role": "vault_owner",
                })

                total = 0
                if "people" in by_cat:
                    total += sync_people(honcho, by_cat["people"], author_peer)
                if "clients" in by_cat:
                    total += sync_clients(honcho, by_cat["clients"], author_peer)
                for cat in ("daily", "reference", "projects"):
                    if cat in by_cat:
                        total += sync_documents(honcho, by_cat[cat], cat, author_peer)

                for f in changed:
                    key = str(f["path"])
                    state[key] = {
                        "mtime": f["mtime"],
                        "last_synced": time.time(),
                        "category": f["category"],
                    }
                # Persist card facts we set so reverse sync can filter them
                for peer_id, facts in _card_facts_cache.items():
                    state[f"_card_{peer_id}"] = list(facts)
                save_json(SYNC_STATE_FILE, state)
                print(f"Forward sync: {total} files synced to Honcho.")
        else:
            print(f"No changed files (scanned {len(all_files)} total).")

    # --- Reverse sync: Honcho -> Obsidian dossiers ---
    if args.update_dossiers:
        print("\nReverse sync: checking Honcho for new insights...")
        reverse_state = load_json(SYNC_STATE_FILE)
        updated = update_dossiers(honcho, reverse_state, dry_run=args.dry_run, verbose=args.verbose)
        if updated:
            print(f"Reverse sync: {updated} dossiers updated from Honcho.")
        else:
            print("Reverse sync: no new insights to add.")

    # --- Deep reconciliation: peer.chat() for key people (weekly/Friday) ---
    if args.deep_reconcile:
        print("\nDeep reconciliation: querying Honcho for key people...")
        reconciled = deep_reconcile_dossiers(honcho, dry_run=args.dry_run, verbose=args.verbose)
        if reconciled:
            print(f"Deep reconciliation: {reconciled} dossiers updated.")
        else:
            print("Deep reconciliation: no changes found.")

    print("Done.")


if __name__ == "__main__":
    main()
