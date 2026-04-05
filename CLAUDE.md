# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

An install kit that bootstraps an OpenClaw-based personal AI agent ("Jeff") on a fresh Mac. The agent integrates Slack, Google Workspace (Gmail/Calendar/Drive), Obsidian, Honcho (memory), and Linear into an automated workflow with cron-driven sync cycles.

## Running Setup

```bash
./setup.sh                    # Full setup
./setup.sh --skip-deps        # Skip Homebrew/Node/Python install
./setup.sh --skip-google      # Skip Google Workspace setup
./setup.sh --skip-slack       # Skip Slack setup
./setup.sh --dry-run          # Don't start gateway at end
```

Setup is interactive (prompts for name, timezone, email). All scripts are sourced from `setup.sh`, not run as subprocesses — they share shell state.

Python dependencies are installed into a venv at `~/.openclaw/venv`. Cron jobs use `~/.openclaw/venv/bin/python3`.

## Architecture

### Data Flow

```
External Sources          Local Cache              Memory            Outputs
─────────────────    ──────────────────    ──────────────    ─────────────────
Slack messages    →  JSONL per channel  →  Honcho sessions →  Dossier updates
Gmail/transcripts →  local .md files    →  Honcho peers    →  Morning briefings
Google Calendar   →  vdirsyncer/khal    →                  →  Meeting prep
Linear tickets    →  task_orchestrator  →                  →  Claude Code tasks
```

Honcho is the central memory layer. All external data flows through local caching (for dedup/state tracking) before being pushed to Honcho, where it's available for cross-session reasoning.

### Directory Layout

- **`setup.sh`** — Orchestrator. Runs phases 1-10 sequentially, sourcing scripts from `scripts/`.
- **`scripts/`** — One-time setup scripts (install deps, configure integrations). Each is idempotent.
- **`sync-scripts/`** — Python scripts that run on cron via OpenClaw. Copied to `~/.openclaw/workspace/scripts/` at install time.
- **`workspace/`** — Agent identity/config files. Copied to `~/.openclaw/workspace/` (won't overwrite existing).
- **`templates/`** — `openclaw.json` (agent config) and `dossier-template.md` (people profiles).

### Shared Modules (`sync-scripts/`)

- **`shared.py`** — Path constants (`WORKSPACE`, `MESSAGES_DIR`, `VAULT_PATH`, `PEOPLE_DIR`), Honcho connection (`get_honcho()`), atomic JSON/text writes (`save_json`, `atomic_write_text`), ID sanitization (`sanitize_id`), and cron overlap prevention (`script_lock`). All sync scripts import from here.
- **`config.py`** — Consolidated configuration: `BOT_UIDS`, `BOT_PATTERNS`, `EXCLUDE_CHANNELS`, `TRACKED_PEOPLE`, `DEEP_RECONCILE_PEERS`, `PRIORITY_DM_USERS`, `PRIORITY_CHANNELS`. When the team or channel list changes, update here only.

### Sync Scripts (Python)

Each script has a single responsibility and tracks its own sync state to avoid reprocessing:

| Script | Purpose |
|--------|---------|
| `slack_sync.py` | Fetch Slack messages → JSONL per channel (14-day retention) |
| `slack_todo_scan.py` | Scan recent Slack for action items directed at user |
| `honcho_slack_sync.py` | Push local Slack JSONL → Honcho sessions (batch 100) |
| `honcho_obsidian_sync.py` | Bidirectional sync: Obsidian dossiers ↔ Honcho peers |
| `honcho_write.py` | CLI to push facts/conclusions into Honcho directly |
| `update_dossiers.py` | Gather Honcho context per person for agent LLM merge |
| `sync_meeting_transcripts.py` | Gmail → local transcripts + extract action items → TODO.md + Obsidian |
| `morning_briefing.py` | Generate daily briefing from calendar + PRs + Linear + Slack |
| `task_orchestrator.py` | Linear ticket → Claude Code launch commands |

### Cron Schedule (registered via `scripts/setup_crons.sh`)

| Job | Frequency | Model | What it does |
|-----|-----------|-------|-------------|
| slack-cycle | 15 min | Gemini Flash | slack_sync + todo scan + honcho push |
| background-sync | 1 hour | Gemini Flash | gcal + Obsidian/Honcho dossier sync |
| linear-pr-cycle | 30 min | Gemini Flash | Check PRs + urgent Linear tickets (conditional) |
| morning-setup | 8am weekdays | Gemini Flash | Daily note + briefing + Slack DM |
| eod | 5pm weekdays | Claude Sonnet | Transcripts + dossier merge + EOD summary |

All models accessed via **Vertex AI** (GCP project `YOUR_PROJECT_ID`). Claude Sonnet is only used for the EOD job (dossier merging requires quality writing). Everything else uses Gemini 2.5 Flash (~$0.15/M input) to keep costs under $5/month.

## Key Dependencies

- **OpenClaw** — Agent platform (installed via npm globally)
- **Google Cloud SDK** — `gcloud auth application-default login` for Vertex AI auth
- **Honcho** — Memory system (cloud or self-hosted Postgres + Ollama with nomic-embed-text)
- **Python packages** — See `requirements.txt`. Installed into `~/.openclaw/venv`.
- **gogcli** — Google OAuth CLI for Gmail/Calendar
- **vdirsyncer** + **khal** — CalDAV sync and local calendar
- **gh** — GitHub CLI (for PR checks and code checkin)
- **Linear API** — Ticket management (via task_orchestrator)

## Conventions

- All setup scripts use `set -e` and are idempotent (check before installing).
- Sync scripts use `shared.script_lock()` (flock-based) to prevent concurrent cron runs.
- State files are written atomically via `shared.save_json()` (temp file + `os.replace`).
- Sync scripts track state in per-script JSON files to avoid duplicate processing.
- Slack messages cached as JSONL at `~/.openclaw/workspace/slack_messages/<channel>.jsonl`.
- Credentials live in `.slack_env` / `.google_env` (gitignored) and `~/.openclaw/workspace/TOOLS.md`.
- Agent identity is defined by workspace markdown files (SOUL.md, USER.md, IDENTITY.md) loaded every session.
- The `sed -i ''` pattern in setup.sh is macOS-specific (no backup extension).

## Environment Variables

| Variable | Default | Used by |
|----------|---------|---------|
| `OBSIDIAN_VAULT` | `~/Documents/Obsidian Vault` | shared.py, setup_obsidian.sh |
| `HONCHO_BASE_URL` | `http://localhost:18790` | shared.py |
| `HONCHO_WORKSPACE` | `openclaw` | shared.py |
| `GOG_ACCOUNT` | (required) | sync_meeting_transcripts.py |
| `SLACK_USER_TOKEN` | — | slack_sync.py |
| `SLACK_USER_ID` | — | morning_briefing.py |
| `LINEAR_API_KEY` | — | morning_briefing.py, task_orchestrator.py |

## Target Runtime Environment

macOS 14+, Homebrew, Node.js 22, Python 3.12. The setup scripts and `sed` flags are Mac-specific — adapting to Linux requires changing `sed -i ''` to `sed -i` and replacing Homebrew with apt/dnf equivalents.
