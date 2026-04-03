#!/bin/bash
set -e

# OpenClaw Setup — Fresh Mac
# Usage: ./setup.sh [--skip-deps] [--skip-google] [--skip-slack] [--dry-run]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCLAW_DIR="$HOME/.openclaw"
WORKSPACE="$OPENCLAW_DIR/workspace"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; }
step() { echo -e "\n${BLUE}━━━ $1 ━━━${NC}\n"; }
ask()  { echo -e "${YELLOW}$1${NC}"; read -r REPLY; }

SKIP_DEPS=false
SKIP_GOOGLE=false
SKIP_SLACK=false
DRY_RUN=false

for arg in "$@"; do
    case $arg in
        --skip-deps)   SKIP_DEPS=true ;;
        --skip-google) SKIP_GOOGLE=true ;;
        --skip-slack)  SKIP_SLACK=true ;;
        --dry-run)     DRY_RUN=true ;;
    esac
done

# ─── Phase 1: Dependencies ─────────────────────────────────────────

if [ "$SKIP_DEPS" = false ]; then
    step "Phase 1: Installing dependencies"
    source "$SCRIPT_DIR/scripts/install_deps.sh"
else
    warn "Skipping dependency installation"
fi

# ─── Phase 2: OpenClaw ─────────────────────────────────────────────

step "Phase 2: Installing OpenClaw"
source "$SCRIPT_DIR/scripts/install_openclaw.sh"

# ─── Phase 3: Workspace Files ──────────────────────────────────────

step "Phase 3: Setting up workspace"

mkdir -p "$WORKSPACE"/{memory,scripts,references,transcriptions,slack_messages}

# Copy workspace templates (don't overwrite existing)
for f in AGENTS.md SOUL.md USER.md IDENTITY.md HEARTBEAT.md TOOLS.md; do
    if [ ! -f "$WORKSPACE/$f" ]; then
        cp "$SCRIPT_DIR/workspace/$f" "$WORKSPACE/$f"
        log "Created $f"
    else
        warn "$f already exists, skipping"
    fi
done

# Copy dossier template
cp "$SCRIPT_DIR/templates/dossier-template.md" "$WORKSPACE/references/"
log "Copied dossier template"

# ─── Phase 4: Sync Scripts ─────────────────────────────────────────

step "Phase 4: Installing sync scripts"

for f in "$SCRIPT_DIR"/sync-scripts/*.py; do
    fname=$(basename "$f")
    cp "$f" "$WORKSPACE/scripts/$fname"
done
log "Copied $(ls "$SCRIPT_DIR"/sync-scripts/*.py | wc -l | tr -d ' ') scripts to workspace"

# Install Python dependencies for scripts
pip3 install --break-system-packages slack-sdk honcho-ai 2>/dev/null || \
    pip3 install slack-sdk honcho-ai
log "Installed Python dependencies"

# ─── Phase 5: Honcho ───────────────────────────────────────────────

step "Phase 5: Setting up Honcho (memory system)"
source "$SCRIPT_DIR/scripts/setup_honcho.sh"

# ─── Phase 6: Slack ────────────────────────────────────────────────

if [ "$SKIP_SLACK" = false ]; then
    step "Phase 6: Setting up Slack"
    source "$SCRIPT_DIR/scripts/setup_slack.sh"
else
    warn "Skipping Slack setup"
fi

# ─── Phase 7: Google Workspace ──────────────────────────────────────

if [ "$SKIP_GOOGLE" = false ]; then
    step "Phase 7: Setting up Google Workspace"
    source "$SCRIPT_DIR/scripts/setup_google.sh"
else
    warn "Skipping Google Workspace setup"
fi

# ─── Phase 8: Obsidian ──────────────────────────────────────────────

step "Phase 8: Setting up Obsidian vault"
source "$SCRIPT_DIR/scripts/setup_obsidian.sh"

# ─── Phase 9: Configuration ─────────────────────────────────────────

step "Phase 9: Configuring OpenClaw"

if [ ! -f "$OPENCLAW_DIR/openclaw.json" ]; then
    err "No openclaw.json found. Run 'openclaw init' or copy a config."
    err "A template is at $SCRIPT_DIR/templates/openclaw.json"
    warn "You'll need to add your Anthropic API key and Slack tokens manually."
else
    log "openclaw.json exists"
fi

# Prompt for customization
echo ""
ask "What's your name? (for USER.md)"
USER_NAME="$REPLY"

ask "What's your timezone? (e.g., America/Denver)"
USER_TZ="$REPLY"

ask "What's your email? (for Google Workspace)"
USER_EMAIL="$REPLY"

if [ -n "$USER_NAME" ]; then
    sed -i '' "s/\[YOUR NAME\]/$USER_NAME/g" "$WORKSPACE/USER.md" 2>/dev/null || true
    sed -i '' "s/\[YOUR NAME\]/$USER_NAME/g" "$WORKSPACE/SOUL.md" 2>/dev/null || true
    log "Set name: $USER_NAME"
fi

if [ -n "$USER_TZ" ]; then
    sed -i '' "s|America/Denver|$USER_TZ|g" "$WORKSPACE/USER.md" 2>/dev/null || true
    log "Set timezone: $USER_TZ"
fi

if [ -n "$USER_EMAIL" ]; then
    sed -i '' "s/\[YOUR EMAIL\]/$USER_EMAIL/g" "$WORKSPACE/TOOLS.md" 2>/dev/null || true
    log "Set email: $USER_EMAIL"
fi

# ─── Phase 10: Start ────────────────────────────────────────────────

step "Phase 10: Starting OpenClaw"

if [ "$DRY_RUN" = true ]; then
    warn "Dry run, not starting gateway"
else
    echo ""
    ask "Start the OpenClaw gateway now? (y/n)"
    if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
        openclaw gateway start
        sleep 3
        openclaw status
        log "Gateway is running"

        echo ""
        ask "Create cron jobs now? (y/n)"
        if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
            source "$SCRIPT_DIR/scripts/setup_crons.sh"
        fi
    fi
fi

# ─── Done ────────────────────────────────────────────────────────────

step "Setup Complete"

echo "Next steps:"
echo "  1. Edit ~/.openclaw/workspace/SOUL.md with your agent's personality"
echo "  2. Edit ~/.openclaw/workspace/USER.md with your info"
echo "  3. Add API keys to ~/.openclaw/workspace/TOOLS.md"
echo "  4. Start the TUI: openclaw tui"
echo "  5. Say hello!"
echo ""
log "Done."
