# OpenClaw Template

A self-configuring setup kit for [OpenClaw](https://docs.openclaw.ai) — an AI agent that monitors your Slack, email, calendar, and GitHub, builds relationship profiles on your team and clients, and delivers daily briefings, action items, and dossier updates.

One script. One login. Your AI assistant is running in under 10 minutes.

## Quick Start

```bash
git clone https://github.com/UnbrandedTech/Openclaw-template.git
cd Openclaw-template
./setup.sh
```

## What It Does

The setup script walks you through authentication, then automatically:

1. **Downloads** 3 months of Slack messages, meeting transcripts, 30 days of calendar events, and optionally GitHub activity
2. **Filters** out bots, automated notifications, and noise channels
3. **Loads** all structured data into [Honcho](https://honcho.dev) (the AI's memory system)
4. **Analyzes** your communication patterns using AI to identify your key people, clients, and priority channels
5. **Generates** Obsidian dossiers for every important person and client company profile

Then it sets up recurring cron jobs so the agent keeps everything current.

## What You Get

- **Morning briefings** — calendar, action items, Slack highlights delivered via DM at 8am
- **Action item extraction** — from every meeting transcript, not just standups
- **People dossiers** — auto-maintained profiles on everyone you work with, synced bidirectionally with Obsidian and Honcho
- **Client profiles** — company-level context for services businesses (who are the contacts, what are the active engagements, what channel do we use)
- **TODO detection** — scans Slack for things directed at you and surfaces them
- **End-of-day wrap** — dossier updates, transcript sync, learnings pushed to memory

## What You Need

- macOS 14+ (Linux support: [#1](https://github.com/UnbrandedTech/Openclaw-template/issues/1))
- An AI provider — one of: Vertex AI (recommended), OpenAI, Anthropic, Ollama (local/free), or AWS Bedrock
- Slack workspace (bot token + user token)
- Google Workspace or Gmail account
- Cost depends on provider: ~$5/month on Vertex AI, varies for OpenAI/Anthropic, free with Ollama

## How It Works

### Setup Flow (12 phases, fully guided)

| Phase | What happens |
|-------|-------------|
| 1-4 | Install dependencies, OpenClaw, workspace files, sync scripts |
| 5-6 | Configure Honcho memory system + Slack integration |
| 7-8 | Choose AI provider + authenticate (Vertex/OpenAI/Anthropic/Ollama/Bedrock), set up Google Workspace tools |
| 9 | Create Obsidian vault structure |
| 10 | Personalization (name, email, timezone auto-detected, GitHub opt-in, services-biz flag) |
| 11 | **Full workspace sync + AI discovery** (the 5-step pipeline) |
| 12 | Start gateway + register cron jobs |

### The Discovery Pipeline (Phase 11)

```
Step 1: Download all data
  Slack (3 months) + email transcripts + calendar (30 days) + GitHub (optional)

Step 2: Filter bots and noise
  Auto-identifies bot users, CI/CD channels, automated notifications

Step 3: Load into Honcho
  Slack messages + transcripts + calendar events + GitHub PRs → memory

Step 4: AI priority analysis (reasoning model)
  Determines who matters, identifies clients, ranks channels

Step 5: Generate dossiers (fast model)
  Creates Obsidian profiles for every tracked person and client company
```

### Ongoing Cron Jobs

| Job | Frequency | Model Role | What it does |
|-----|-----------|------------|-------------|
| slack-cycle | 15 min | fast | Sync Slack + scan for TODOs + push to Honcho |
| background-sync | 1 hour | fast | Calendar + Obsidian/Honcho dossier sync |
| linear-pr-cycle | 30 min | fast | Check PRs + Linear tickets (if configured) |
| morning-setup | 8am weekdays | fast | Daily note + briefing + Slack DM |
| eod | 5pm weekdays | reasoning | Transcripts + dossier merge + EOD summary |

Model roles (`fast` and `reasoning`) map to specific models in `openclaw.json`. Default: Gemini Flash + Claude Sonnet on Vertex AI.

### Cost

Depends on your provider. Vertex AI default: ~$4-5/month (Gemini Flash for 95% of calls, Sonnet for daily EOD only). OpenAI/Anthropic vary. Ollama is free (local).

## Project Structure

```
setup.sh                          # Main orchestrator (12 phases)
scripts/                          # One-time setup scripts
  install_deps.sh                 # Homebrew, Node, Python, gcloud, venv
  install_openclaw.sh             # OpenClaw CLI + workspace init
  setup_honcho.sh                 # Honcho memory system (cloud default)
  setup_slack.sh                  # Slack bot/user tokens
  setup_google.sh                 # gogcli + vdirsyncer (uses shared OAuth)
  setup_obsidian.sh               # Vault directory structure
  setup_crons.sh                  # Register cron jobs with OpenClaw

sync-scripts/                     # Python scripts (run on cron + during setup)
  shared.py                       # Shared utilities, path constants, user config, call_llm()
  config.py                       # Auto-discovered config (bots, channels, people)
  slack_sync.py                   # Slack → local JSONL
  slack_todo_scan.py              # Scan messages for action items
  honcho_slack_sync.py            # Push Slack → Honcho
  honcho_obsidian_sync.py         # Bidirectional Obsidian ↔ Honcho
  honcho_write.py                 # CLI to push facts to Honcho
  sync_meeting_transcripts.py     # Gmail → transcripts + action item extraction
  sync_calendar.py                # Parse calendar .ics → structured JSON
  sync_github.py                  # GitHub PRs/issues (optional)
  load_to_honcho.py               # Push transcripts/calendar/GitHub → Honcho
  discover_workspace.py           # Auto-detect bots, noise channels, score people
  analyze_priorities.py           # LLM-based priority ranking (Sonnet)
  generate_initial_dossiers.py    # Bulk dossier + client profile generation (Flash)
  update_dossiers.py              # Incremental dossier updates from Honcho
  morning_briefing.py             # Daily briefing assembly
  task_orchestrator.py            # Linear/GitHub ticket → Claude Code tasks

templates/                        # Config templates
  openclaw.json                   # Vertex AI agent config
  user.json                       # User identity (populated by setup)
  team.json                       # Team config (populated by discovery)
  dossier-template.md             # Person profile format
  company-template.md             # Client company profile format
  client_secret.json              # OAuth client (gitignored, you provide this)

workspace/                        # Agent identity files → ~/.openclaw/workspace/
  AGENTS.md, SOUL.md, USER.md, IDENTITY.md, HEARTBEAT.md, TOOLS.md
```

## Customization

After setup, edit files at `~/.openclaw/workspace/`:

| File | What it controls |
|------|-----------------|
| `user.json` | Your name, email, Slack ID, timezone, title |
| `team.json` | Tracked people, clients, priority channels (auto-generated, editable) |
| `SOUL.md` | Agent personality and behavioral rules |
| `USER.md` | Context about you for the agent |
| `HEARTBEAT.md` | What the agent checks each heartbeat cycle |

## Roadmap

See [open issues](https://github.com/UnbrandedTech/Openclaw-template/issues) for planned work:

- [Linux support](https://github.com/UnbrandedTech/Openclaw-template/issues/1)
- [Personal email (IMAP) support](https://github.com/UnbrandedTech/Openclaw-template/issues/3)
- [Slack alternatives (Teams, Discord)](https://github.com/UnbrandedTech/Openclaw-template/issues/4)
- [Notion instead of Obsidian](https://github.com/UnbrandedTech/Openclaw-template/issues/5)
- [Hosted Honcho support](https://github.com/UnbrandedTech/Openclaw-template/issues/6)
- [Project management tools (Jira, Asana, GitHub Issues)](https://github.com/UnbrandedTech/Openclaw-template/issues/7)

## License

MIT
