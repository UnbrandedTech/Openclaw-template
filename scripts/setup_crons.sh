#!/bin/bash
# Create OpenClaw cron jobs
# Run this after the gateway is started

PYTHON=$(which python3)
SCRIPTS="$HOME/.openclaw/workspace/scripts"

echo "Creating cron jobs..."

# Slack sync (every 15 min)
openclaw cron add \
    --name "slack-cycle" \
    --every 900000 \
    --model "anthropic/claude-sonnet-4-6" \
    --message "Run both Slack scripts in order:
1. \`$PYTHON $SCRIPTS/slack_sync.py\`
2. \`$PYTHON $SCRIPTS/slack_todo_scan.py\`
Report only if you added a TODO item or hit an error." \
    --timeout 300 \
    --delivery none 2>/dev/null && log "Created slack-cycle" || warn "slack-cycle may already exist"

# Honcho Slack sync (every 15 min)
openclaw cron add \
    --name "honcho-slack-sync" \
    --every 900000 \
    --model "anthropic/claude-haiku-4-5" \
    --message "Run: \`$PYTHON $SCRIPTS/honcho_slack_sync.py\`
No report unless errors." \
    --timeout 120 \
    --delivery none 2>/dev/null && log "Created honcho-slack-sync" || warn "honcho-slack-sync may already exist"

# Linear + PR reviews (every 30 min)
openclaw cron add \
    --name "linear-pr-cycle" \
    --every 1800000 \
    --model "anthropic/claude-sonnet-4-6" \
    --message "Run: \`$PYTHON $SCRIPTS/task_orchestrator.py check-reviews\`
Report only on new review comments, CI failures, or new Urgent Linear tickets." \
    --timeout 180 \
    --delivery none 2>/dev/null && log "Created linear-pr-cycle" || warn "linear-pr-cycle may already exist"

# Google Calendar sync (every 1 hr)
openclaw cron add \
    --name "gcal-sync" \
    --every 3600000 \
    --model "anthropic/claude-haiku-4-5" \
    --message "Run \`vdirsyncer sync 2>/dev/null\` to sync Google Calendar. No report unless error." \
    --timeout 60 \
    --delivery none 2>/dev/null && log "Created gcal-sync" || warn "gcal-sync may already exist"

# Honcho-Obsidian sync (every 1 hr)
openclaw cron add \
    --name "honcho-obsidian-sync" \
    --every 3600000 \
    --model "anthropic/claude-haiku-4-5" \
    --message "Run: \`$PYTHON $SCRIPTS/honcho_obsidian_sync.py --update-dossiers\`
No report unless errors." \
    --timeout 120 \
    --delivery none 2>/dev/null && log "Created honcho-obsidian-sync" || warn "honcho-obsidian-sync may already exist"

# Dossier update (5pm weekdays)
openclaw cron add \
    --name "dossier-update" \
    --cron "0 17 * * 1-5" \
    --tz "America/Denver" \
    --model "anthropic/claude-sonnet-4-6" \
    --message "You are updating Obsidian people dossiers from Honcho memory.

## Step 1: Gather
Run: \`$PYTHON $SCRIPTS/update_dossiers.py --priority high --out /tmp/dossier-gather.json\`

## Step 2: Merge
For each person in the JSON: read \`~/.openclaw/workspace/references/dossier-template.md\` for format, merge current_dossier + honcho_context into a consolidated YAML-frontmatter profile. Full rewrite, not append.

Rules: no em dashes, no log entries, Current Focus = NOW, Open Items = still open only. Report only errors." \
    --timeout 300 2>/dev/null && log "Created dossier-update" || warn "dossier-update may already exist"

# EOD wrap (5:15pm weekdays)
openclaw cron add \
    --name "eod-wrap" \
    --cron "15 17 * * 1-5" \
    --tz "America/Denver" \
    --model "anthropic/claude-sonnet-4-6" \
    --message "End-of-day: 1) Run gmail standup sync. 2) Append EOD summary to today's memory file. 3) Push 3-5 key learnings to Honcho via honcho_write.py." \
    --timeout 180 \
    --delivery none 2>/dev/null && log "Created eod-wrap" || warn "eod-wrap may already exist"

# Morning setup (8am weekdays)
openclaw cron add \
    --name "morning-setup" \
    --cron "0 8 * * 1-5" \
    --tz "America/Denver" \
    --model "anthropic/claude-sonnet-4-6" \
    --message "Morning setup: 1) Create today's Obsidian daily note with meetings + top TODOs. 2) Run morning_briefing.py and DM the output to user on Slack." \
    --timeout 180 \
    --delivery none 2>/dev/null && log "Created morning-setup" || warn "morning-setup may already exist"

# Transcript sync (6pm weekdays)
openclaw cron add \
    --name "transcript-sync" \
    --cron "0 18 * * 1-5" \
    --tz "America/Denver" \
    --message "Run: python3 ~/.openclaw/workspace/scripts/sync_meeting_transcripts.py — report only if new transcripts or errors." \
    --timeout 120 \
    --delivery none 2>/dev/null && log "Created transcript-sync" || warn "transcript-sync may already exist"

log "All cron jobs created"
echo ""
echo "Verify with: openclaw cron list"
