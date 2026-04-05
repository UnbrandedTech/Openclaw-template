#!/bin/bash
# Create OpenClaw cron jobs
# Run this after the gateway is started

PYTHON="$HOME/.openclaw/venv/bin/python3"
SCRIPTS="$HOME/.openclaw/workspace/scripts"

echo "Creating cron jobs..."

# Slack sync (every 15 min — uses Haiku since it just runs scripts and checks output)
openclaw cron add \
    --name "slack-cycle" \
    --every 900000 \
    --model "vertex/gemini-2.5-flash" \
    --message "Run all three Slack scripts in order:
1. \`$PYTHON $SCRIPTS/slack_sync.py\`
2. \`$PYTHON $SCRIPTS/slack_todo_scan.py\`
3. \`$PYTHON $SCRIPTS/honcho_slack_sync.py\`
Report only if you added a TODO item or hit an error." \
    --timeout 420 \
    --delivery none 2>/dev/null && log "Created slack-cycle" || warn "slack-cycle may already exist"

# Background sync (every 1 hr)
openclaw cron add \
    --name "background-sync" \
    --every 3600000 \
    --model "vertex/gemini-2.5-flash" \
    --message "Run these background syncs in order:
1. \`vdirsyncer sync 2>/dev/null\` (Google Calendar)
2. \`$PYTHON $SCRIPTS/honcho_obsidian_sync.py --update-dossiers\`
No report unless errors." \
    --timeout 180 \
    --delivery none 2>/dev/null && log "Created background-sync" || warn "background-sync may already exist"

# Linear + PR reviews (every 30 min, only if Linear is configured)
if [ -n "${LINEAR_API_KEY:-}" ]; then
    openclaw cron add \
        --name "linear-pr-cycle" \
        --every 1800000 \
        --model "vertex/gemini-2.5-flash" \
        --message "Run: \`$PYTHON $SCRIPTS/task_orchestrator.py check-reviews\`
Report only on new review comments, CI failures, or new Urgent Linear tickets." \
        --timeout 180 \
        --delivery none 2>/dev/null && log "Created linear-pr-cycle" || warn "linear-pr-cycle may already exist"
else
    warn "Skipping linear-pr-cycle (LINEAR_API_KEY not set)"
fi

# Morning setup (8am weekdays — Flash assembles daily note + delivers briefing)
openclaw cron add \
    --name "morning-setup" \
    --cron "0 8 * * 1-5" \
    --tz "America/Denver" \
    --model "vertex/gemini-2.5-flash" \
    --message "Morning setup: 1) Create today's Obsidian daily note with meetings + top TODOs. 2) Run morning_briefing.py and DM the output to user on Slack." \
    --timeout 180 \
    --delivery none 2>/dev/null && log "Created morning-setup" || warn "morning-setup may already exist"

# End-of-day wrap (5pm weekdays)
openclaw cron add \
    --name "eod" \
    --cron "0 17 * * 1-5" \
    --tz "America/Denver" \
    --model "vertex/claude-sonnet-4-6" \
    --message "End-of-day wrap. Run these steps in order:

## Step 1: Meeting transcripts
Run: \`$PYTHON $SCRIPTS/sync_meeting_transcripts.py\`

## Step 2: Dossier updates
Run: \`$PYTHON $SCRIPTS/update_dossiers.py --priority high --out /tmp/dossier-gather.json\`
For each person in the JSON: read \`~/.openclaw/workspace/references/dossier-template.md\` for format, merge current_dossier + honcho_context into a consolidated YAML-frontmatter profile. Full rewrite, not append.
Rules: no em dashes, no log entries, Current Focus = NOW, Open Items = still open only.

## Step 3: EOD summary
Append EOD summary to today's memory file. Push 3-5 key learnings to Honcho via honcho_write.py.

Report only errors or notable action items found." \
    --timeout 600 \
    --delivery none 2>/dev/null && log "Created eod" || warn "eod may already exist"

log "All cron jobs created"
echo ""
echo "Verify with: openclaw cron list"
