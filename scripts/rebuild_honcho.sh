#!/bin/bash
set -e

# Rebuild Honcho from scratch using existing local exports
# Usage: ./scripts/rebuild_honcho.sh [--skip-dossiers] [--skip-analysis]
#
# This does NOT re-download Slack/email/calendar data — it uses whatever
# is already in ~/.openclaw/workspace/. Run sync scripts first if you
# want fresh data.

OPENCLAW_DIR="$HOME/.openclaw"
WORKSPACE="$OPENCLAW_DIR/workspace"
VENV_PYTHON="$OPENCLAW_DIR/venv/bin/python3"
SCRIPTS="$WORKSPACE/scripts"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; }
step() { echo -e "\n${BLUE}━━━ $1 ━━━${NC}\n"; }

SKIP_DOSSIERS=false
SKIP_ANALYSIS=false

for arg in "$@"; do
    case $arg in
        --skip-dossiers) SKIP_DOSSIERS=true ;;
        --skip-analysis) SKIP_ANALYSIS=true ;;
    esac
done

# ── Preflight ─────────────────────────────────────────────────────
if [ ! -d "$WORKSPACE" ]; then
    err "Workspace not found at $WORKSPACE. Run setup.sh first."
    exit 1
fi

if [ ! -f "$VENV_PYTHON" ]; then
    err "Python venv not found. Run setup.sh first."
    exit 1
fi

SLACK_COUNT=$(ls "$WORKSPACE/slack_messages/"*.jsonl 2>/dev/null | wc -l | tr -d ' ')
TRANSCRIPT_COUNT=$(ls "$WORKSPACE/transcriptions/"*.txt 2>/dev/null | wc -l | tr -d ' ')
echo ""
echo "Local data available:"
echo "  Slack channels:  $SLACK_COUNT JSONL files"
echo "  Transcripts:     $TRANSCRIPT_COUNT files"
echo "  Calendar:        $([ -f "$WORKSPACE/calendar_events.json" ] && echo "yes" || echo "no")"
echo "  GitHub:          $([ -f "$WORKSPACE/github_activity.json" ] && echo "yes" || echo "no")"
echo ""

if [ "$SLACK_COUNT" = "0" ]; then
    err "No Slack data found. Run slack_sync.py first."
    exit 1
fi

echo -e "${RED}This will DROP the honcho database and rebuild from scratch.${NC}"
echo -e "${RED}All Honcho memory, peer cards, and conclusions will be lost.${NC}"
echo ""
echo -n "Type 'yes' to continue: "
read -r REPLY
if [ "$REPLY" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

# ── Step 1: Drop and recreate database ────────────────────────────
step "Step 1: Resetting Honcho database"

# Stop the OpenClaw gateway (holds connections to Honcho)
if command -v openclaw &>/dev/null; then
    log "Stopping OpenClaw gateway..."
    openclaw gateway stop 2>/dev/null || true
    sleep 2
fi

if command -v psql &>/dev/null && psql -d postgres -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw honcho; then
    # Terminate any remaining connections
    log "Terminating active connections..."
    psql -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'honcho' AND pid <> pg_backend_pid();" 2>/dev/null || true
    sleep 1

    # Wipe all data by dropping and recreating the public schema
    # (avoids needing CREATEDB privilege to drop/recreate the whole database)
    log "Wiping honcho data..."
    # Drop all tables but keep the database and extensions
    # (extensions like pgvector need superuser to recreate)
    TABLES=$(psql -d honcho -t -c "SELECT tablename FROM pg_tables WHERE schemaname = 'public';" 2>/dev/null | tr -d ' ' | grep -v '^$')
    if [ -n "$TABLES" ]; then
        for table in $TABLES; do
            psql -d honcho -c "DROP TABLE IF EXISTS public.\"$table\" CASCADE;" 2>/dev/null
        done
    fi
    # Also drop alembic version so migrations re-run from scratch
    psql -d honcho -c "DROP TABLE IF EXISTS alembic_version CASCADE;" 2>/dev/null || true
    # Drop any sequences, types, etc
    psql -d honcho -c "DO \$\$ DECLARE r RECORD; BEGIN FOR r IN (SELECT typname FROM pg_type WHERE typnamespace = 'public'::regnamespace AND typtype = 'e') LOOP EXECUTE 'DROP TYPE IF EXISTS public.' || r.typname || ' CASCADE'; END LOOP; END \$\$;" 2>/dev/null || true
    log "Tables dropped (extensions preserved)"

    # Re-run Honcho migrations if running self-hosted from source
    HONCHO_DIR="${HONCHO_PROJECT_DIR:-$HOME/Projects/Personal/honcho}"
    if [ -f "$HONCHO_DIR/alembic.ini" ]; then
        log "Running Honcho migrations..."
        (cd "$HONCHO_DIR" && "$HONCHO_DIR/.venv/bin/alembic" upgrade heads 2>&1) || warn "Alembic migrations had errors"
        log "Migrations complete"
    fi

    # Restart Honcho server so it picks up the fresh schema
    if pgrep -f "src/main.py.*18790" &>/dev/null; then
        log "Restarting Honcho server..."
        pkill -f "src/main.py.*18790" 2>/dev/null || true
        sleep 2
        (cd "$HONCHO_DIR" && "$HONCHO_DIR/.venv/bin/fastapi" run src/main.py --port 18790 --host 127.0.0.1 &>/dev/null &)
        sleep 3
        if curl -s http://localhost:18790/ &>/dev/null; then
            log "Honcho server restarted"
        else
            warn "Honcho server may not have started — check manually"
        fi
    fi

    log "Database reset complete"
else
    # Database doesn't exist, try to create it
    log "Creating honcho database..."
    createdb honcho 2>/dev/null || { err "Could not create database (may need: sudo -u postgres createdb -O $USER honcho)"; exit 1; }
    psql -d honcho -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null || true
    log "Created honcho database"
fi

# Clear sync state files so scripts re-process everything
rm -f "$WORKSPACE/slack_messages/.honcho_sync_state.json"
rm -f "$WORKSPACE/memory/honcho-load-state.json"
log "Cleared sync state"

# ── Step 2: Re-discover workspace ─────────────────────────────────
step "Step 2: Re-discovering workspace"

"$VENV_PYTHON" "$SCRIPTS/discover_workspace.py" --force 2>&1
log "Discovery complete"

# ── Step 3: Load Slack into Honcho ────────────────────────────────
step "Step 3: Loading Slack messages into Honcho"

"$VENV_PYTHON" "$SCRIPTS/honcho_slack_sync.py" --reset 2>&1
log "Slack messages loaded"

# ── Step 4: Load transcripts, calendar, GitHub ────────────────────
step "Step 4: Loading transcripts, calendar, and GitHub"

"$VENV_PYTHON" "$SCRIPTS/load_to_honcho.py" --reset 2>&1
log "Data loaded"

# ── Step 5: Priority analysis ─────────────────────────────────────
if [ "$SKIP_ANALYSIS" = false ]; then
    step "Step 5: Analyzing priorities"

    ANALYZE_FLAGS=""
    SERVICES_BIZ=$(python3 -c "import json; print(json.load(open('$WORKSPACE/user.json')).get('services_business', False))" 2>/dev/null)
    if [ "$SERVICES_BIZ" = "True" ]; then
        ANALYZE_FLAGS="--services-business"
    fi

    "$VENV_PYTHON" "$SCRIPTS/analyze_priorities.py" $ANALYZE_FLAGS 2>&1
    log "Priority analysis complete"
else
    warn "Skipping priority analysis"
fi

# ── Step 6: Generate dossiers ─────────────────────────────────────
if [ "$SKIP_DOSSIERS" = false ]; then
    step "Step 6: Generating dossiers"

    "$VENV_PYTHON" "$SCRIPTS/generate_initial_dossiers.py" --type all --priority all --force 2>&1
    log "Dossiers generated"
else
    warn "Skipping dossier generation"
fi

# ── Done ──────────────────────────────────────────────────────────
step "Rebuild complete"
echo "  Honcho has been rebuilt from local data."
echo "  Review: ~/.openclaw/workspace/team.json"
echo "  Dossiers: ~/Documents/Obsidian Vault/People/"
echo ""
