# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A self-configuring setup kit for OpenClaw — an AI agent platform. The setup script authenticates the user, downloads their Slack/email/calendar/GitHub history, loads it into Honcho (memory), uses AI to identify key people and clients, and generates Obsidian dossiers. Then it sets up cron jobs to keep everything current.

## Running Setup

```bash
./setup.sh                    # Full setup (12 phases, interactive)
./setup.sh --skip-deps        # Skip Homebrew/Node/Python install
./setup.sh --skip-google      # Skip Google Workspace setup
./setup.sh --skip-slack       # Skip Slack setup
./setup.sh --dry-run          # Don't start gateway at end
```

Python dependencies install into `~/.openclaw/venv`. Cron jobs use `~/.openclaw/venv/bin/python3`.

## Architecture

### Setup Pipeline (Phase 11)

```
Step 1: Download
  Slack API (3mo)    → slack_messages/*.jsonl
  Email (gogcli/IMAP) → transcriptions/*.txt
  vdirsyncer (.ics)   → calendar_events.json + calendar_attendees.json
  gh CLI (optional)  → github_activity.json

Step 2: Filter
  discover_workspace.py → discovered_bots.json + discovered_channels.json + discovered_people.json

Step 3: Load into Honcho
  honcho_slack_sync.py → Honcho sessions (slack-*)
  load_to_honcho.py    → Honcho sessions (transcript-*, calendar-events, github-*)

Step 4: LLM Priority Analysis (Sonnet)
  analyze_priorities.py → team.json (tracked_people, clients, priorities)

Step 5: Generate Dossiers (Flash)
  generate_initial_dossiers.py → Obsidian Vault/People/*.md + Clients/*.md
```

### Directory Layout

- **`setup.sh`** — Orchestrator. 12 phases, sources scripts from `scripts/`.
- **`scripts/`** — One-time setup scripts. Each is idempotent.
- **`sync-scripts/`** — Python scripts for cron + setup. Copied to `~/.openclaw/workspace/scripts/`.
- **`workspace/`** — Agent identity markdown files. Copied to `~/.openclaw/workspace/`.
- **`templates/`** — Config templates: `openclaw.json`, `user.json`, `team.json`, dossier/company templates, `client_secret.json` (gitignored).

### Shared Modules

- **`shared.py`** — Path constants, user identity (from `user.json`), Honcho client (`get_honcho()`), atomic writes (`save_json`), ID sanitization (`sanitize_id`), cron overlap prevention (`script_lock`). All scripts import from here.
- **`config.py`** — Loads auto-discovered config from workspace JSON files: `BOT_UIDS`, `EXCLUDE_CHANNELS` (from discovery), `TRACKED_PEOPLE`, `CLIENTS`, `PRIORITY_*` (from team.json).

### Sync Scripts

| Script | Purpose |
|--------|---------|
| `slack_sync.py` | Fetch Slack messages → JSONL per channel |
| `slack_todo_scan.py` | Scan Slack for action items directed at user |
| `honcho_slack_sync.py` | Push Slack JSONL → Honcho sessions |
| `honcho_obsidian_sync.py` | Bidirectional Obsidian ↔ Honcho sync |
| `honcho_write.py` | CLI to push facts/conclusions into Honcho |
| `sync_meeting_transcripts.py` | Email (Gmail/IMAP) → transcripts + action item extraction |
| `sync_calendar.py` | Parse vdirsyncer .ics → calendar events + attendee frequency |
| `sync_github.py` | GitHub PRs/issues via `gh` CLI (optional) |
| `load_to_honcho.py` | Push transcripts + calendar + GitHub → Honcho |
| `discover_workspace.py` | Auto-detect bots, noise channels, score people |
| `analyze_priorities.py` | Sonnet-based priority ranking after data load |
| `generate_initial_dossiers.py` | Bulk dossier + client profile generation via Flash |
| `update_dossiers.py` | Incremental dossier updates from Honcho |
| `morning_briefing.py` | Daily briefing from calendar + PRs + Slack |
| `task_orchestrator.py` | Linear/GitHub ticket → Claude Code tasks |

### Cron Schedule

| Job | Frequency | Model | What it does |
|-----|-----------|-------|-------------|
| slack-cycle | 15 min | Gemini Flash | Slack sync + todo scan + Honcho push |
| background-sync | 1 hour | Gemini Flash | Calendar + Obsidian/Honcho dossier sync |
| linear-pr-cycle | 30 min | Gemini Flash | PRs + Linear tickets (conditional on `LINEAR_API_KEY`) |
| morning-setup | 8am weekdays | Gemini Flash | Daily note + briefing + Slack DM |
| eod | 5pm weekdays | Claude Sonnet | Transcripts + dossier merge + EOD summary |

Models accessed via Vertex AI. Sonnet only for the daily EOD job. Everything else uses Gemini Flash (~$5/month total).

## Config Files (all auto-generated, editable post-setup)

| File | Location | Purpose |
|------|----------|---------|
| `user.json` | `~/.openclaw/workspace/` | User identity (name, email, Slack ID, timezone) |
| `team.json` | `~/.openclaw/workspace/` | Tracked people, clients, priority channels (LLM-generated) |
| `discovered_bots.json` | `~/.openclaw/workspace/` | Auto-detected bot UIDs + display name patterns |
| `discovered_channels.json` | `~/.openclaw/workspace/` | Auto-detected noise channels to exclude |
| `discovered_people.json` | `~/.openclaw/workspace/` | Heuristic people scores (input to analyze_priorities) |
| `openclaw.json` | `~/.openclaw/` | Vertex AI auth profile + model config |

## Key Dependencies

- **OpenClaw** — Agent platform (npm)
- **Google Cloud SDK** — Vertex AI auth (`gcloud auth application-default login`)
- **Honcho** — Memory system (cloud or self-hosted)
- **Python packages** — See `requirements.txt` (installed into venv)
- **gogcli** — Google OAuth CLI for Gmail (only if using Google email provider)
- **vdirsyncer + khal** — Calendar sync (Google Calendar or CalDAV)
- **gh** — GitHub CLI (optional)
- **icalendar** — Python library for .ics calendar parsing

## Conventions

- Setup scripts use `set -e` and are idempotent.
- Sync scripts use `script_lock()` (flock) to prevent cron overlap.
- State files written atomically via `save_json()` (temp + `os.replace`).
- No hardcoded names, IDs, or company data — everything from `user.json` + `team.json`.
- All config auto-discovered during setup, editable afterward.
- `sed -i ''` in setup.sh is macOS-specific (Linux support tracked in issue #1).

## Environment Variables

| Variable | Default | Used by |
|----------|---------|---------|
| `OBSIDIAN_VAULT` | `~/Documents/Obsidian Vault` | shared.py, setup_obsidian.sh |
| `HONCHO_BASE_URL` | `http://localhost:18790` | shared.py |
| `HONCHO_WORKSPACE` | `openclaw` | shared.py |
| `GOG_ACCOUNT` | (required for Google email) | sync_meeting_transcripts.py |
| `IMAP_PASSWORD` | — | sync_meeting_transcripts.py (if using IMAP provider) |
| `SLACK_USER_TOKEN` | — | slack_sync.py |
| `LINEAR_API_KEY` | — | morning_briefing.py, task_orchestrator.py |
