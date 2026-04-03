# OpenClaw Setup

One-script setup for OpenClaw on a fresh Mac with Claude Max.

## Quick Start

```bash
git clone https://github.com/MarathonData/openclaw-setup.git
cd openclaw-setup
./setup.sh
```

The script will:
1. Install prerequisites (Node.js, Python, Homebrew packages)
2. Install OpenClaw
3. Set up workspace files (AGENTS.md, SOUL.md, etc.)
4. Configure Honcho (memory system)
5. Set up Slack integration
6. Set up Google Workspace (Gmail, Calendar, Drive)
7. Create Obsidian vault structure
8. Create cron jobs
9. Start the gateway

## What You Need

- macOS 14+
- Claude Max subscription (or Anthropic API key)
- Slack workspace admin access (to create a bot)
- Google Workspace account (@marathondataco.com)

## After Setup

1. Open the TUI: `openclaw tui`
2. Say hello, the agent will introduce itself
3. It will start monitoring Slack, calendar, and email on cron cycles

## Customization

Edit files in `workspace/` before running setup, or edit them after in `~/.openclaw/workspace/`.

- `workspace/SOUL.md` — Agent personality and rules
- `workspace/USER.md` — Info about you
- `workspace/IDENTITY.md` — Agent identity card
- `workspace/HEARTBEAT.md` — What to check each heartbeat cycle
- `workspace/TOOLS.md` — API keys, credentials, local notes

## Structure

```
├── setup.sh                 # Main setup script
├── scripts/
│   ├── install_deps.sh      # Homebrew, Node, Python
│   ├── install_openclaw.sh  # OpenClaw + workspace init
│   ├── setup_honcho.sh      # Honcho memory system
│   ├── setup_slack.sh       # Slack app + sync scripts
│   ├── setup_google.sh      # gogcli + vdirsyncer + khal
│   ├── setup_obsidian.sh    # Vault structure + dossier system
│   └── setup_crons.sh       # Create all cron jobs
├── templates/
│   ├── openclaw.json        # Base config template
│   └── dossier-template.md  # People dossier format
├── workspace/               # Gets copied to ~/.openclaw/workspace/
│   ├── AGENTS.md
│   ├── SOUL.md
│   ├── USER.md
│   ├── IDENTITY.md
│   ├── HEARTBEAT.md
│   └── TOOLS.md
└── sync-scripts/            # Gets copied to ~/.openclaw/workspace/scripts/
    ├── slack_sync.py
    ├── slack_todo_scan.py
    ├── honcho_slack_sync.py
    ├── honcho_obsidian_sync.py
    ├── honcho_write.py
    ├── update_dossiers.py
    ├── sync_meeting_transcripts.py
    └── morning_briefing.py
```
