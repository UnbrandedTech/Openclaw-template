# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A self-configuring setup kit for OpenClaw — an AI agent platform. The setup script authenticates the user, downloads their Slack/email/calendar/GitHub history, loads it into Honcho (memory), uses AI to identify key people and clients, and generates Obsidian dossiers. Then it sets up cron jobs to keep everything current.

## Running Setup

```bash
./setup.sh                    # Full setup (12 phases, interactive wizard)
./setup.sh --no-wizard        # Plain text mode (no gum TUI)
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

Step 4: LLM Priority Analysis (reasoning model)
  analyze_priorities.py → team.json (tracked_people, clients, priorities)

Step 5: Generate Dossiers (fast model)
  generate_initial_dossiers.py → Obsidian Vault/People/*.md + Clients/*.md
```

### Directory Layout

- **`setup.sh`** — Orchestrator. 12 phases, sources scripts from `scripts/`.
- **`scripts/`** — One-time setup scripts. Each is idempotent.
- **`sync-scripts/`** — Python scripts for cron + setup. Copied to `~/.openclaw/workspace/scripts/`.
- **`workspace/`** — Agent identity markdown files. Copied to `~/.openclaw/workspace/`.
- **`templates/`** — Config templates: `openclaw.json`, `user.json`, `team.json`, dossier/company templates, `client_secret.json` (gitignored).

### Shared Modules

- **`shared.py`** — Path constants, user identity (from `user.json`), `call_llm()` (multi-provider LLM dispatch), `get_secret()`/`set_secret()` (keychain-first credential access), Honcho client (`get_honcho()`), atomic writes (`save_json`), ID sanitization (`sanitize_id`), cron overlap prevention (`script_lock`). All scripts import from here.
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
| `analyze_priorities.py` | LLM-based priority ranking (reasoning model) |
| `generate_initial_dossiers.py` | Bulk dossier + client profile generation (fast model) |
| `update_dossiers.py` | Incremental dossier updates from Honcho |
| `morning_briefing.py` | Daily briefing from calendar + PRs + Slack |
| `task_orchestrator.py` | Linear/GitHub ticket → Claude Code tasks |

### Cron Schedule

| Job | Frequency | Model Role | What it does |
|-----|-----------|------------|-------------|
| slack-cycle | 15 min | fast | Slack sync + todo scan + Honcho push |
| background-sync | 1 hour | fast | Calendar + Obsidian/Honcho dossier sync |
| linear-pr-cycle | 30 min | fast | PRs + Linear tickets (conditional on `LINEAR_API_KEY`) |
| morning-setup | 8am weekdays | fast | Daily note + briefing + Slack DM |
| eod | 5pm weekdays | reasoning | Transcripts + dossier merge + EOD summary |

Model roles (`fast`, `reasoning`) are mapped to specific provider/model in `openclaw.json`. Default: Vertex AI (Gemini Flash + Claude Sonnet). Supported: OpenAI, Anthropic, Ollama, AWS Bedrock.

## Config Files (all auto-generated, editable post-setup)

| File | Location | Purpose |
|------|----------|---------|
| `user.json` | `~/.openclaw/workspace/` | User identity (name, email, Slack ID, timezone, keychain preference) |
| `team.json` | `~/.openclaw/workspace/` | Tracked people, clients, priority channels (LLM-generated) |
| `discovered_bots.json` | `~/.openclaw/workspace/` | Auto-detected bot UIDs + display name patterns |
| `discovered_channels.json` | `~/.openclaw/workspace/` | Auto-detected noise channels to exclude |
| `discovered_people.json` | `~/.openclaw/workspace/` | Heuristic people scores (input to analyze_priorities) |
| `openclaw.json` | `~/.openclaw/` | AI provider auth profile + model role mapping |

## Key Dependencies

- **OpenClaw** — Agent platform (npm)
- **Google Cloud SDK** — Required for Vertex AI provider (`gcloud auth application-default login`)
- **Honcho** — Memory system (cloud or self-hosted)
- **Python packages** — See `requirements.txt` (installed into venv)
- **gogcli** — Google OAuth CLI for Gmail (only if using Google email provider)
- **vdirsyncer + khal** — Calendar sync (Google Calendar or CalDAV)
- **gh** — GitHub CLI (optional)
- **icalendar** — Python library for .ics calendar parsing
- **secret-tool** — Linux keychain access via libsecret (optional, for encrypted credential storage)
- **gum** — Charm TUI toolkit for the setup wizard (optional, auto-installed by install_deps.sh)

## Conventions

- Setup scripts use `set -e` and are idempotent.
- Sync scripts use `script_lock()` (flock) to prevent cron overlap.
- State files written atomically via `save_json()` (temp + `os.replace`).
- No hardcoded names, IDs, or company data — everything from `user.json` + `team.json`.
- No hardcoded model names — scripts use `call_llm(prompt, role="fast"|"reasoning")` which reads from `openclaw.json`.
- All config auto-discovered during setup, editable afterward.
- `sedi()` wrapper in setup.sh handles cross-platform `sed -i` (macOS needs `''` arg, Linux doesn't).
- `store_secret()` in setup.sh stores credentials in system keychain or `.env` file based on `USE_KEYCHAIN`.
- Secrets accessed via `get_secret(name)` from shared.py: checks keychain first, then env vars, then `.env` file. Controlled by `"keychain": true` in `user.json`.
- Wizard mode uses `gum` for styled TUI (auto-detected, `--no-wizard` to disable). Helper functions: `wizard_choose()`, `wizard_confirm()`, `wizard_input()`, `wizard_spin()`, `run_phase()` (with retry/skip error recovery).

## Environment Variables

| Variable | Default | Used by |
|----------|---------|---------|
| `OBSIDIAN_VAULT` | `~/Documents/Obsidian Vault` | shared.py, setup_obsidian.sh |
| `HONCHO_BASE_URL` | `http://localhost:18790` | shared.py |
| `HONCHO_WORKSPACE` | `openclaw` | shared.py |
| `GOG_ACCOUNT` | (required for Google email) | sync_meeting_transcripts.py |
| `IMAP_PASSWORD` | — | sync_meeting_transcripts.py (if using IMAP provider) |
| `SLACK_USER_TOKEN` | — | slack_sync.py |
| `OPENAI_API_KEY` | — | shared.py (if using OpenAI provider) |
| `ANTHROPIC_API_KEY` | — | shared.py (if using Anthropic provider) |
| `LINEAR_API_KEY` | — | morning_briefing.py, task_orchestrator.py |
