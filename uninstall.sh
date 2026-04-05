#!/bin/bash
set -e

# OpenClaw Uninstall
# Removes: ~/.openclaw, keychain entries, cron jobs, vdirsyncer config
# Does NOT remove: Obsidian vault (unless --include-vault), system packages

OPENCLAW_DIR="$HOME/.openclaw"
WORKSPACE="$OPENCLAW_DIR/workspace"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; }

# ── OS detection ───────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
    Darwin) PLATFORM="macos" ;;
    Linux)  PLATFORM="linux" ;;
    *)      PLATFORM="unknown" ;;
esac

# ── Flags ──────────────────────────────────────────────────────────
INCLUDE_VAULT=false
FORCE=false

for arg in "$@"; do
    case $arg in
        --include-vault) INCLUDE_VAULT=true ;;
        --force|-f)      FORCE=true ;;
    esac
done

# ── Confirmation ───────────────────────────────────────────────────
echo ""
echo "This will remove:"
echo "  - $OPENCLAW_DIR (workspace, venv, config, scripts)"
echo "  - OpenClaw cron jobs"
echo "  - Keychain entries (if stored)"
echo "  - vdirsyncer config (~/.config/vdirsyncer)"
if [ "$INCLUDE_VAULT" = true ]; then
    VAULT_PATH="${OBSIDIAN_VAULT:-$HOME/Documents/Obsidian Vault}"
    echo "  - Obsidian vault People/Clients dirs ($VAULT_PATH/People, $VAULT_PATH/Clients)"
fi
echo ""
echo "This will NOT remove: system packages (Node, Python, gum, gcloud, etc.)"
echo ""

if [ "$FORCE" = false ]; then
    echo -e "${RED}Are you sure? This cannot be undone. (type 'yes' to confirm)${NC}"
    read -r REPLY
    if [ "$REPLY" != "yes" ]; then
        echo "Aborted."
        exit 0
    fi
fi

# ── Stop gateway ──────────────────────────────────────────────────
if command -v openclaw &>/dev/null; then
    echo -n "  Stopping OpenClaw gateway... "
    openclaw gateway stop 2>/dev/null || true
    log "stopped"
fi

# ── Remove cron jobs ──────────────────────────────────────────────
if command -v openclaw &>/dev/null; then
    echo -n "  Removing cron jobs... "
    for job in slack-cycle background-sync linear-pr-cycle morning-setup eod; do
        openclaw cron delete "$job" 2>/dev/null || true
    done
    log "removed"
fi

# ── Remove keychain entries ───────────────────────────────────────
KEYCHAIN_KEYS=(
    OPENAI_API_KEY
    ANTHROPIC_API_KEY
    SLACK_BOT_TOKEN
    SLACK_APP_TOKEN
    SLACK_USER_TOKEN
    IMAP_PASSWORD
    CALDAV_PASSWORD
    LINEAR_API_KEY
)

echo -n "  Removing keychain entries... "
for key in "${KEYCHAIN_KEYS[@]}"; do
    if [ "$PLATFORM" = "macos" ]; then
        security delete-generic-password -s openclaw -a "$key" 2>/dev/null || true
    elif command -v secret-tool &>/dev/null; then
        secret-tool clear service openclaw key "$key" 2>/dev/null || true
    fi
done
log "removed"

# ── Remove vdirsyncer config ─────────────────────────────────────
if [ -f ~/.config/vdirsyncer/config ]; then
    echo -n "  Removing vdirsyncer config... "
    rm -f ~/.config/vdirsyncer/config
    rm -f ~/.config/vdirsyncer/caldav_password
    rm -f ~/.config/vdirsyncer/google_token
    rm -rf ~/.local/share/vdirsyncer
    log "removed"
fi

# ── Remove Obsidian vault data ────────────────────────────────────
if [ "$INCLUDE_VAULT" = true ]; then
    VAULT_PATH="${OBSIDIAN_VAULT:-$HOME/Documents/Obsidian Vault}"
    if [ -d "$VAULT_PATH/People" ]; then
        echo -n "  Removing $VAULT_PATH/People... "
        rm -rf "$VAULT_PATH/People"
        log "removed"
    fi
    if [ -d "$VAULT_PATH/Clients" ]; then
        echo -n "  Removing $VAULT_PATH/Clients... "
        rm -rf "$VAULT_PATH/Clients"
        log "removed"
    fi
fi

# ── Remove ~/.openclaw ────────────────────────────────────────────
if [ -d "$OPENCLAW_DIR" ]; then
    echo -n "  Removing $OPENCLAW_DIR... "
    rm -rf "$OPENCLAW_DIR"
    log "removed"
fi

# ── Remove npm global package ─────────────────────────────────────
if command -v npm &>/dev/null && npm list -g openclaw &>/dev/null 2>&1; then
    echo -n "  Removing openclaw npm package... "
    npm uninstall -g openclaw 2>/dev/null || true
    log "removed"
fi

echo ""
log "OpenClaw uninstalled."
echo ""
echo "To reinstall: ./setup.sh"
echo ""
