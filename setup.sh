#!/bin/bash
set -e

# OpenClaw Setup
# Usage: ./setup.sh [--skip-deps] [--skip-google] [--skip-slack] [--dry-run] [--no-wizard] [--from N]
# --skip-google skips email & calendar tool setup (Phase 8)
# --no-wizard   disables gum TUI and uses plain text prompts
# --from N      restart from phase N (e.g., --from 7 to redo AI provider setup)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCLAW_DIR="$HOME/.openclaw"
WORKSPACE="$OPENCLAW_DIR/workspace"
VENV_PYTHON="$HOME/.openclaw/venv/bin/python3"

# ── Load previous config (from uninstall backup) ─────────────────
BACKUP_DIR="$HOME/.openclaw-backup"
PREV_USER_JSON="$BACKUP_DIR/user.json"
if [ -f "$PREV_USER_JSON" ]; then
    PREV_NAME=$(python3 -c "import json; print(json.load(open('$PREV_USER_JSON')).get('name',''))" 2>/dev/null)
    PREV_EMAIL=$(python3 -c "import json; print(json.load(open('$PREV_USER_JSON')).get('email',''))" 2>/dev/null)
    PREV_TZ=$(python3 -c "import json; print(json.load(open('$PREV_USER_JSON')).get('timezone',''))" 2>/dev/null)
    PREV_SLACK_ID=$(python3 -c "import json; print(json.load(open('$PREV_USER_JSON')).get('slack_user_id',''))" 2>/dev/null)
    PREV_SLACK_USER=$(python3 -c "import json; print(json.load(open('$PREV_USER_JSON')).get('slack_username',''))" 2>/dev/null)
    PREV_TITLE=$(python3 -c "import json; print(json.load(open('$PREV_USER_JSON')).get('title',''))" 2>/dev/null)
    PREV_COMPANY=$(python3 -c "import json; print(json.load(open('$PREV_USER_JSON')).get('company',''))" 2>/dev/null)
    PREV_GITHUB=$(python3 -c "import json; print(json.load(open('$PREV_USER_JSON')).get('github_username',''))" 2>/dev/null)
    PREV_SERVICES=$(python3 -c "import json; d=json.load(open('$PREV_USER_JSON')); print('true' if d.get('services_business') else 'false')" 2>/dev/null)
    PREV_EMAIL_PROVIDER=$(python3 -c "import json; print(json.load(open('$PREV_USER_JSON')).get('email_provider',''))" 2>/dev/null)
    PREV_CALENDAR_PROVIDER=$(python3 -c "import json; print(json.load(open('$PREV_USER_JSON')).get('calendar_provider',''))" 2>/dev/null)
    PREV_KEYCHAIN=$(python3 -c "import json; d=json.load(open('$PREV_USER_JSON')); print('true' if d.get('keychain') else 'false')" 2>/dev/null)
fi

# Restore .env and .slack_env from backup if they exist
if [ -f "$BACKUP_DIR/.env" ] && [ ! -f "$WORKSPACE/.env" ]; then
    mkdir -p "$WORKSPACE"
    cp "$BACKUP_DIR/.env" "$WORKSPACE/.env"
    chmod 600 "$WORKSPACE/.env"
fi
if [ -f "$BACKUP_DIR/.slack_env" ] && [ ! -f "$WORKSPACE/.slack_env" ]; then
    mkdir -p "$WORKSPACE"
    cp "$BACKUP_DIR/.slack_env" "$WORKSPACE/.slack_env"
    chmod 600 "$WORKSPACE/.slack_env"
fi
if [ -f "$BACKUP_DIR/.google_env" ] && [ ! -f "$WORKSPACE/.google_env" ]; then
    mkdir -p "$WORKSPACE"
    cp "$BACKUP_DIR/.google_env" "$WORKSPACE/.google_env"
fi

# ── Setup log file ────────────────────────────────────────────────
mkdir -p "$OPENCLAW_DIR"
SETUP_LOG="$OPENCLAW_DIR/setup.log"
echo "=== OpenClaw setup started $(date -Iseconds) ===" >> "$SETUP_LOG"

# ── OS detection ────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
    Darwin) PLATFORM="macos" ;;
    Linux)  PLATFORM="linux" ;;
    *)      echo "Unsupported OS: $OS"; exit 1 ;;
esac

# Detect Linux distro family
DISTRO=""
if [ "$PLATFORM" = "linux" ]; then
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        case "$ID" in
            ubuntu|debian|pop|mint|elementary) DISTRO="debian" ;;
            fedora|rhel|centos|rocky|alma)     DISTRO="fedora" ;;
            arch|manjaro)                       DISTRO="arch" ;;
            *)                                 DISTRO="$ID" ;;
        esac
    fi
fi

# Detect user shell config file
if [ -n "$ZSH_VERSION" ] || [ "$(basename "$SHELL")" = "zsh" ]; then
    SHELL_RC="$HOME/.zshrc"
    SHELL_PROFILE="$HOME/.zprofile"
else
    SHELL_RC="$HOME/.bashrc"
    SHELL_PROFILE="$HOME/.profile"
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ── Wizard / TUI detection ─────────────────────────────────────────
HAS_GUM=false
if command -v gum &>/dev/null; then
    HAS_GUM=true
fi

TOTAL_PHASES=12
CURRENT_PHASE=0

# Plain-mode UI (fallback)
_log_plain()  { echo -e "${GREEN}[✓]${NC} $1"; }
_warn_plain() { echo -e "${YELLOW}[!]${NC} $1"; echo "[$(date +%H:%M:%S)] WARN: $1" >> "$SETUP_LOG" 2>/dev/null; }
_err_plain()  { echo -e "${RED}[✗]${NC} $1"; echo "[$(date +%H:%M:%S)] ERR:  $1" >> "$SETUP_LOG" 2>/dev/null; }
_step_plain() { echo -e "\n${BLUE}━━━ $1 ━━━${NC}\n"; }
_ask_plain()  { echo -e "${YELLOW}$1${NC}"; read -r REPLY; }

# Gum-enhanced UI
_log_gum()  { gum style --foreground 2 "✓ $1"; }
_warn_gum() { gum style --foreground 3 "! $1"; echo "[$(date +%H:%M:%S)] WARN: $1" >> "$SETUP_LOG" 2>/dev/null; }
_err_gum()  { gum style --foreground 1 --bold "✗ $1"; echo "[$(date +%H:%M:%S)] ERR:  $1" >> "$SETUP_LOG" 2>/dev/null; }
_step_gum() {
    echo ""
    gum style --border rounded --border-foreground 4 --padding "0 2" --bold \
        "[$CURRENT_PHASE/$TOTAL_PHASES] $1"
    echo ""
}
_ask_gum() {
    # gum input with the prompt as header
    gum style --foreground 3 "$1"
    REPLY=$(gum input --placeholder "Type your answer..." --width 60) || REPLY=""
}

# wizard_choose: present a selection menu
# Usage: wizard_choose "Header text" "option1" "option2" ...
# Sets REPLY to selected option
wizard_choose() {
    local header="$1"; shift
    if [ "$HAS_GUM" = true ] && [ "$WIZARD" = true ]; then
        gum style --foreground 4 "$header"
        REPLY=$(gum choose "$@") || REPLY=""
    else
        echo -e "${BLUE}${header}${NC}"
        local i=1
        for opt in "$@"; do
            echo "  $i) $opt"
            i=$((i + 1))
        done
        echo ""
        echo -e "${YELLOW}Choose (1-$#):${NC}"
        read -r REPLY
    fi
}

# wizard_confirm: yes/no confirmation
# Usage: wizard_confirm "Question?" && echo "yes" || echo "no"
wizard_confirm() {
    if [ "$HAS_GUM" = true ] && [ "$WIZARD" = true ]; then
        gum confirm "$1"
    else
        echo -e "${YELLOW}$1 (y/n)${NC}"
        read -r REPLY
        [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]
    fi
}

# wizard_input: text input with placeholder
# Usage: wizard_input "Label" "placeholder" [--password]
wizard_input() {
    local label="$1" placeholder="${2:-}" password="${3:-}"
    if [ "$HAS_GUM" = true ] && [ "$WIZARD" = true ]; then
        gum style --foreground 3 "$label"
        if [ "$password" = "--password" ]; then
            REPLY=$(gum input --placeholder "$placeholder" --password --width 60) || REPLY=""
        else
            REPLY=$(gum input --placeholder "$placeholder" --width 60) || REPLY=""
        fi
    else
        echo -e "${YELLOW}${label}${NC}"
        read -r REPLY
    fi
}

# wizard_spin: run a command with a spinner
# Usage: wizard_spin "message" command arg1 arg2 ...
wizard_spin() {
    local msg="$1"; shift
    echo "[$(date +%H:%M:%S)] RUN: $msg — $*" >> "$SETUP_LOG"
    if [ "$HAS_GUM" = true ] && [ "$WIZARD" = true ]; then
        if "$@" >> "$SETUP_LOG" 2>&1; then
            gum style --foreground 2 "  ✓ $msg"
            echo "[$(date +%H:%M:%S)] OK:  $msg" >> "$SETUP_LOG"
            return 0
        else
            local code=$?
            gum style --foreground 1 "  ✗ $msg (see ~/.openclaw/setup.log)"
            echo "[$(date +%H:%M:%S)] FAIL($code): $msg" >> "$SETUP_LOG"
            return $code
        fi
    else
        echo -n "  $msg... "
        if "$@" >> "$SETUP_LOG" 2>&1; then
            echo "done"
            return 0
        else
            local code=$?
            echo "FAILED (see ~/.openclaw/setup.log)"
            return $code
        fi
    fi
}

# run_phase: execute a phase with error recovery
# Usage: run_phase "Phase name" "explanation" command_or_function
run_phase() {
    local name="$1" explanation="$2"
    shift 2
    CURRENT_PHASE=$((CURRENT_PHASE + 1))
    step "$name"

    if [ "$HAS_GUM" = true ] && [ "$WIZARD" = true ] && [ -n "$explanation" ]; then
        gum style --faint --italic "$explanation"
        echo ""
    fi

    # Run the remaining args as the phase body
    # (phases are inline, so this is used for sourced scripts)
    if [ $# -gt 0 ]; then
        local attempt=1
        while true; do
            if "$@" 2>&1; then
                return 0
            else
                local exit_code=$?
                err "Step failed (exit code $exit_code)"
                if [ "$HAS_GUM" = true ] && [ "$WIZARD" = true ]; then
                    local action
                    action=$(gum choose "Retry" "Skip this step" "Abort setup") || action="Abort setup"
                    case "$action" in
                        "Retry")
                            attempt=$((attempt + 1))
                            warn "Retrying (attempt $attempt)..."
                            continue
                            ;;
                        "Skip this step")
                            warn "Skipped: $name"
                            return 0
                            ;;
                        "Abort setup")
                            err "Setup aborted by user."
                            exit 1
                            ;;
                    esac
                else
                    ask "Retry (r), skip (s), or abort (a)?"
                    case "$REPLY" in
                        r|R) attempt=$((attempt + 1)); warn "Retrying..."; continue ;;
                        s|S) warn "Skipped: $name"; return 0 ;;
                        *)   err "Setup aborted."; exit 1 ;;
                    esac
                fi
            fi
        done
    fi
}

# Set active UI functions based on wizard mode
_set_ui_mode() {
    if [ "$HAS_GUM" = true ] && [ "$WIZARD" = true ]; then
        log()  { _log_gum "$@"; }
        warn() { _warn_gum "$@"; }
        err()  { _err_gum "$@"; }
        step() { _step_gum "$@"; }
        ask()  { _ask_gum "$@"; }
    else
        log()  { _log_plain "$@"; }
        warn() { _warn_plain "$@"; }
        err()  { _err_plain "$@"; }
        step() { _step_plain "$@"; }
        ask()  { _ask_plain "$@"; }
    fi
}

# Default to plain mode (wizard mode set after arg parsing)
log()  { _log_plain "$@"; }
warn() { _warn_plain "$@"; }
err()  { _err_plain "$@"; }
step() { _step_plain "$@"; }
ask()  { _ask_plain "$@"; }

# Cross-platform sed -i (macOS needs '' arg, Linux doesn't)
sedi() {
    if [ "$PLATFORM" = "macos" ]; then
        sed -i '' "$@"
    else
        sed -i "$@"
    fi
}

# Cross-platform package install
pkg_install() {
    if [ "$PLATFORM" = "macos" ]; then
        brew install "$@"
    elif [ "$DISTRO" = "debian" ]; then
        sudo apt-get install -y "$@"
    elif [ "$DISTRO" = "fedora" ]; then
        sudo dnf install -y "$@"
    elif [ "$DISTRO" = "arch" ]; then
        sudo pacman -S --noconfirm "$@"
    else
        err "Cannot install $* — unknown distro. Install manually."
        return 1
    fi
}

# Store a secret in system keychain or .env file
# Usage: store_secret KEY VALUE
store_secret() {
    local key="$1" value="$2"
    if [ "$USE_KEYCHAIN" = true ]; then
        if [ "$PLATFORM" = "macos" ]; then
            security add-generic-password -s openclaw -a "$key" -w "$value" -U 2>/dev/null && return 0
        else
            echo -n "$value" | secret-tool store --label "OpenClaw: $key" service openclaw key "$key" 2>/dev/null && return 0
        fi
        warn "Keychain store failed for $key, falling back to .env file"
    fi
    # Fallback: write to .env
    mkdir -p "$WORKSPACE"
    if grep -q "^${key}=" "$WORKSPACE/.env" 2>/dev/null; then
        sedi "s|^${key}=.*|${key}=${value}|" "$WORKSPACE/.env"
    else
        echo "${key}=${value}" >> "$WORKSPACE/.env"
    fi
    chmod 600 "$WORKSPACE/.env"
}

# Cross-platform cask/GUI app install
pkg_install_cask() {
    if [ "$PLATFORM" = "macos" ]; then
        brew install --cask "$@"
    elif [ "$DISTRO" = "debian" ]; then
        # Most GUI apps need snap or flatpak on Linux
        if command -v snap &>/dev/null; then
            sudo snap install "$@"
        elif command -v flatpak &>/dev/null; then
            warn "Install $* via Flatpak or download from the project website."
        else
            warn "Install $* manually — no snap or flatpak available."
        fi
    else
        warn "Install $* manually for your platform."
    fi
}

log "Detected platform: $PLATFORM${DISTRO:+ ($DISTRO)}"

SKIP_DEPS=false
SKIP_GOOGLE=false
SKIP_SLACK=false
DRY_RUN=false
WIZARD=true
START_FROM=1

for arg in "$@"; do
    case $arg in
        --skip-deps)   SKIP_DEPS=true ;;
        --skip-google) SKIP_GOOGLE=true ;;
        --skip-slack)  SKIP_SLACK=true ;;
        --dry-run)     DRY_RUN=true ;;
        --no-wizard)   WIZARD=false ;;
        --from)        :;;  # value handled below
        [0-9]|[0-9][0-9]) START_FROM="$arg" ;;
    esac
done
# Handle --from N (two-arg form)
while [ $# -gt 0 ]; do
    if [ "$1" = "--from" ] && [ -n "${2:-}" ]; then
        START_FROM="$2"
        shift 2
    else
        shift
    fi
done

# Helper: should we run this phase?
should_run_phase() {
    [ "$CURRENT_PHASE" -ge "$START_FROM" ]
}

# Activate wizard mode (gum-enhanced UI) if gum is available and not disabled
if [ "$HAS_GUM" = false ]; then
    WIZARD=false
fi
_set_ui_mode

# ── Welcome banner ─────────────────────────────────────────────────
if [ "$WIZARD" = true ]; then
    echo ""
    STARTING_MSG="This wizard will guide you through 12 steps."
    if [ "$START_FROM" -gt 1 ]; then
        STARTING_MSG="Resuming from Phase $START_FROM (skipping 1-$((START_FROM - 1)))."
    fi
    gum style --border double --border-foreground 4 --padding "1 4" --bold --align center \
        "OpenClaw Setup Wizard" \
        "" \
        "Your AI assistant will be running in under 10 minutes." \
        "$STARTING_MSG"
    echo ""
else
    echo ""
    echo "=== OpenClaw Setup ==="
    echo ""
fi

# ─── Pre-flight checks ─────────────────────────────────────────────

if [ "$WIZARD" = true ]; then
    CURRENT_PHASE=0
    step "Pre-flight Checks"
    gum style --faint --italic "Making sure you have everything needed before we begin."
    echo ""
    PREFLIGHT_OK=true

    echo -n "  Internet connection... "
    if curl -s --connect-timeout 5 https://api.github.com >/dev/null 2>&1; then
        log "Connected"
    else
        err "No internet connection"
        PREFLIGHT_OK=false
    fi

    echo -n "  Disk space... "
    FREE_MB=$(df -m "$HOME" 2>/dev/null | awk 'NR==2{print $4}')
    if [ -n "$FREE_MB" ] && [ "$FREE_MB" -gt 500 ]; then
        log "${FREE_MB}MB free"
    else
        warn "Low disk space (${FREE_MB:-unknown}MB). At least 500MB recommended."
    fi

    echo ""
    gum style --bold "Before continuing, make sure you have:"
    echo ""
    gum style "  • Slack workspace admin access (for bot/user tokens)"
    gum style "  • Google account or IMAP email credentials"
    gum style "  • An AI provider API key (or use Ollama for free local)"
    echo ""

    if ! wizard_confirm "Ready to start?"; then
        echo ""
        gum style --faint "No worries! Run ./setup.sh again when you're ready."
        exit 0
    fi
    echo ""
    CURRENT_PHASE=0
else
    echo "Pre-flight: checking internet..."
    if ! curl -s --connect-timeout 5 https://api.github.com >/dev/null 2>&1; then
        warn "No internet connection detected. Some steps may fail."
    fi
fi

# ─── Phase 1: Dependencies ─────────────────────────────────────────

CURRENT_PHASE=1
if [ "$SKIP_DEPS" = false ] && should_run_phase; then
    step "Phase 1: Installing dependencies"
    if [ "$WIZARD" = true ]; then
        gum style --faint --italic "Installing build tools, Node.js, Python, and other prerequisites."
        echo ""
    fi
    source "$SCRIPT_DIR/scripts/install_deps.sh"
elif [ "$SKIP_DEPS" = true ]; then
    warn "Skipping dependency installation"
else
    log "Skipping Phase 1 (--from $START_FROM)"
fi

# ─── Phase 2: OpenClaw ─────────────────────────────────────────────

CURRENT_PHASE=2
if should_run_phase; then
    step "Phase 2: Installing OpenClaw"
    if [ "$WIZARD" = true ]; then
        gum style --faint --italic "Installing the OpenClaw agent platform and initializing your workspace."
        echo ""
    fi
    source "$SCRIPT_DIR/scripts/install_openclaw.sh"
else
    log "Skipping Phase 2 (--from $START_FROM)"
fi

# ─── Phase 3: Workspace Files ──────────────────────────────────────

CURRENT_PHASE=3
if should_run_phase; then
    step "Phase 3: Setting up workspace"
    if [ "$WIZARD" = true ]; then
        gum style --faint --italic "Creating config files and templates your agent needs to operate."
        echo ""
    fi

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

    # Copy team config (don't overwrite — user may have customized it)
    if [ ! -f "$WORKSPACE/team.json" ]; then
        cp "$SCRIPT_DIR/templates/team.json" "$WORKSPACE/team.json"
        log "Created team.json (edit to match your team)"
    else
        warn "team.json already exists, skipping"
    fi
else
    log "Skipping Phase 3 (--from $START_FROM)"
fi

# ─── Phase 4: Sync Scripts ─────────────────────────────────────────

CURRENT_PHASE=4
if should_run_phase; then
    step "Phase 4: Installing sync scripts"
    if [ "$WIZARD" = true ]; then
        gum style --faint --italic "Installing the Python scripts that sync your Slack, email, and calendar."
        echo ""
    fi

    for f in "$SCRIPT_DIR"/sync-scripts/*.py; do
        fname=$(basename "$f")
        cp "$f" "$WORKSPACE/scripts/$fname"
    done
    log "Copied $(ls "$SCRIPT_DIR"/sync-scripts/*.py | wc -l | tr -d ' ') scripts to workspace"

    # Install Python dependencies for scripts
    "$HOME/.openclaw/venv/bin/pip" install slack-sdk honcho-ai
    log "Installed Python dependencies"
else
    log "Skipping Phase 4 (--from $START_FROM)"
fi

# ─── Phase 5: Honcho ───────────────────────────────────────────────

CURRENT_PHASE=5
if should_run_phase; then
    step "Phase 5: Setting up Honcho (memory system)"
    if [ "$WIZARD" = true ]; then
        gum style --faint --italic "Honcho is the AI's long-term memory. It remembers conversations and context."
        echo ""
    fi
    source "$SCRIPT_DIR/scripts/setup_honcho.sh"
else
    log "Skipping Phase 5 (--from $START_FROM)"
fi

# ─── Phase 6: Slack ────────────────────────────────────────────────

CURRENT_PHASE=6
if [ "$SKIP_SLACK" = false ] && should_run_phase; then
    step "Phase 6: Setting up Slack"
    if [ "$WIZARD" = true ]; then
        gum style --faint --italic "Connecting to Slack so the agent can read messages and send you briefings."
        echo ""
    fi
    source "$SCRIPT_DIR/scripts/setup_slack.sh"
elif [ "$SKIP_SLACK" = true ]; then
    warn "Skipping Slack setup"
else
    log "Skipping Phase 6 (--from $START_FROM)"
fi

# ─── Keychain prompt ──────────────────────────────────────────────

USE_KEYCHAIN=false
KEYCHAIN_AVAILABLE=false

if [ "$PLATFORM" = "macos" ] && command -v security &>/dev/null; then
    KEYCHAIN_AVAILABLE=true
elif [ "$PLATFORM" = "linux" ] && command -v secret-tool &>/dev/null; then
    KEYCHAIN_AVAILABLE=true
fi

if [ "$KEYCHAIN_AVAILABLE" = true ]; then
    echo ""
    if [ "$WIZARD" = true ]; then
        gum style --bold "Credential Storage"
        echo ""
        gum style "Your API keys and tokens can be stored in the system keychain"
        gum style "instead of plaintext files on disk."
        if [ "$PLATFORM" = "macos" ]; then
            gum style --faint "Backend: macOS Keychain (via 'security' CLI)"
        else
            gum style --faint "Backend: GNOME Keyring / KDE Wallet (via 'secret-tool')"
        fi
    else
        echo "Your API keys and tokens can be stored in the system keychain"
        echo "instead of plaintext files on disk."
        if [ "$PLATFORM" = "macos" ]; then
            echo "  Backend: macOS Keychain (via 'security' CLI)"
        else
            echo "  Backend: GNOME Keyring / KDE Wallet (via 'secret-tool')"
        fi
    fi
    echo ""
    if wizard_confirm "Store credentials in system keychain?"; then
        USE_KEYCHAIN=true
        log "Keychain storage enabled"
    else
        warn "Credentials will be stored in plaintext .env files (chmod 600)"
    fi
else
    if [ "$PLATFORM" = "linux" ]; then
        warn "secret-tool not found. Install libsecret-tools (Debian/Ubuntu) or libsecret (Fedora/Arch) for keychain support."
    fi
    warn "Credentials will be stored in plaintext .env files (chmod 600)"
fi

# ─── Phase 7: AI Provider + Google Cloud ─────────────────────────

CURRENT_PHASE=7
if should_run_phase; then
step "Phase 7: AI provider setup"
if [ "$WIZARD" = true ]; then
    gum style --faint --italic "Choose which AI service powers your agent. Vertex AI is recommended."
    gum style --faint --italic "You can always change this later in ~/.openclaw/openclaw.json."
    echo ""
fi

if [ "$WIZARD" = true ]; then
    wizard_choose "Which AI provider will you use?" \
        "Vertex AI — Gemini + Claude via Google Cloud (recommended)" \
        "OpenAI — GPT-4o-mini (fast) + GPT-4o (reasoning)" \
        "Anthropic — Claude Haiku (fast) + Claude Sonnet (reasoning)" \
        "Ollama — Local models, no API costs" \
        "AWS Bedrock — Claude via AWS"
    case "$REPLY" in
        OpenAI*)    AI_PROVIDER="openai"    ; FAST_MODEL="openai/gpt-4o-mini"                       ; REASONING_MODEL="openai/gpt-4o" ;;
        Anthropic*) AI_PROVIDER="anthropic" ; FAST_MODEL="anthropic/claude-haiku-4-5-20251001"       ; REASONING_MODEL="anthropic/claude-sonnet-4-6" ;;
        Ollama*)    AI_PROVIDER="ollama"    ; FAST_MODEL="ollama/llama3.1"                           ; REASONING_MODEL="ollama/llama3.1" ;;
        *Bedrock*)  AI_PROVIDER="bedrock"   ; FAST_MODEL="bedrock/anthropic.claude-haiku-4-5-20251001" ; REASONING_MODEL="bedrock/anthropic.claude-sonnet-4-6" ;;
        *)          AI_PROVIDER="vertex"    ; FAST_MODEL="vertex/gemini-2.5-flash"                   ; REASONING_MODEL="vertex/claude-sonnet-4-6" ;;
    esac
else
    echo "Which AI provider will you use?"
    echo ""
    echo "  1) Vertex AI   — Gemini + Claude via Google Cloud (recommended)"
    echo "  2) OpenAI      — GPT-4o-mini (fast) + GPT-4o (reasoning)"
    echo "  3) Anthropic   — Claude Haiku (fast) + Claude Sonnet (reasoning)"
    echo "  4) Ollama      — Local models, no API costs"
    echo "  5) AWS Bedrock — Claude via AWS"
    echo ""
    ask "Choose (1-5, default 1):"

    case "${REPLY:-1}" in
        2) AI_PROVIDER="openai"    ; FAST_MODEL="openai/gpt-4o-mini"                       ; REASONING_MODEL="openai/gpt-4o" ;;
        3) AI_PROVIDER="anthropic" ; FAST_MODEL="anthropic/claude-haiku-4-5-20251001"       ; REASONING_MODEL="anthropic/claude-sonnet-4-6" ;;
        4) AI_PROVIDER="ollama"    ; FAST_MODEL="ollama/llama3.1"                           ; REASONING_MODEL="ollama/llama3.1" ;;
        5) AI_PROVIDER="bedrock"   ; FAST_MODEL="bedrock/anthropic.claude-haiku-4-5-20251001" ; REASONING_MODEL="bedrock/anthropic.claude-sonnet-4-6" ;;
        *) AI_PROVIDER="vertex"    ; FAST_MODEL="vertex/gemini-2.5-flash"                   ; REASONING_MODEL="vertex/claude-sonnet-4-6" ;;
    esac
fi

log "Selected provider: $AI_PROVIDER"

# ── Provider-specific auth ──────────────────────────────────────

CLIENT_SECRET="$SCRIPT_DIR/templates/client_secret.json"
GCP_OK=true
AI_OK=true

if [ "$AI_PROVIDER" = "vertex" ]; then
    # ── Vertex AI: full GCP auth flow ───────────────────────────
    GCP_PROJECT=$(jq -r '.auth.profiles["vertex:default"].project_id' "$SCRIPT_DIR/templates/openclaw-sync.json")
    GCP_REGION=$(jq -r '.auth.profiles["vertex:default"].region' "$SCRIPT_DIR/templates/openclaw-sync.json")

    # Prompt if template still has placeholders
    if [ -z "$GCP_PROJECT" ] || [ "$GCP_PROJECT" = "YOUR_PROJECT_ID" ] || [ "$GCP_PROJECT" = "null" ]; then
        wizard_input "Google Cloud project ID (e.g., my-company-prod):" "my-project-id"
        GCP_PROJECT="$REPLY"
    fi
    if [ -z "$GCP_REGION" ] || [ "$GCP_REGION" = "null" ]; then
        wizard_input "GCP region for Vertex AI (default: us-east5):" "us-east5"
        GCP_REGION="${REPLY:-us-east5}"
    fi

    ALL_SCOPES="https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/calendar.readonly,https://www.googleapis.com/auth/drive.readonly"

    # 1. Check gcloud is installed
    if ! command -v gcloud &>/dev/null; then
        err "gcloud CLI not found. Install it:"
        if [ "$PLATFORM" = "macos" ]; then
            err "  brew install --cask google-cloud-sdk"
        else
            err "  See https://cloud.google.com/sdk/docs/install#linux"
        fi
        GCP_OK=false
    fi

    if [ ! -f "$CLIENT_SECRET" ]; then
        err "OAuth client_secret.json not found at $CLIENT_SECRET"
        err "Ask your admin for the client_secret JSON from the GCP project."
        GCP_OK=false
    fi

    if [ "$GCP_OK" = true ]; then
        if gcloud auth application-default print-access-token &>/dev/null 2>&1; then
            log "GCP credentials already exist"
            ask "Re-authenticate to ensure all scopes (Vertex + Gmail + Calendar + Drive)? (y/n)"
            if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
                gcloud auth application-default login \
                    --client-id-file="$CLIENT_SECRET" \
                    --scopes="$ALL_SCOPES" \
                    --project="$GCP_PROJECT"
            fi
        else
            log "Opening browser for Google authentication..."
            echo "  This will grant access to: Vertex AI, Gmail, Calendar, and Drive"
            echo ""
            gcloud auth application-default login \
                --client-id-file="$CLIENT_SECRET" \
                --scopes="$ALL_SCOPES" \
                --project="$GCP_PROJECT"
        fi

        if gcloud auth application-default print-access-token &>/dev/null 2>&1; then
            log "Authenticated successfully"
        else
            err "Authentication failed."
            GCP_OK=false
        fi
    fi

    if [ "$GCP_OK" = true ]; then
        if gcloud projects describe "$GCP_PROJECT" &>/dev/null 2>&1; then
            log "GCP project '$GCP_PROJECT' accessible"
        else
            err "Cannot access GCP project '$GCP_PROJECT'."
            warn "Make sure you have been granted access to the project."
            GCP_OK=false
        fi
    fi

    if [ "$GCP_OK" = true ]; then
        ENABLED_APIS=$(gcloud services list --enabled --project="$GCP_PROJECT" 2>/dev/null)
        for api in aiplatform.googleapis.com gmail.googleapis.com calendar-json.googleapis.com drive.googleapis.com; do
            if echo "$ENABLED_APIS" | grep -q "$api"; then
                log "$api enabled"
            else
                warn "$api not enabled — attempting to enable..."
                gcloud services enable "$api" --project="$GCP_PROJECT" 2>/dev/null && \
                    log "Enabled $api" || \
                    warn "Could not enable $api — ask your admin"
            fi
        done
    fi

    # Test Vertex AI models
    if [ "$GCP_OK" = true ]; then
        ACCESS_TOKEN=$(gcloud auth application-default print-access-token 2>/dev/null)

        echo -n "  Testing Gemini Flash... "
        FLASH_URL="https://${GCP_REGION}-aiplatform.googleapis.com/v1/projects/${GCP_PROJECT}/locations/${GCP_REGION}/publishers/google/models/gemini-2.5-flash:generateContent"
        FLASH_RESULT=$(curl -s -o /dev/null -w "%{http_code}" \
            -X POST "$FLASH_URL" \
            -H "Authorization: Bearer $ACCESS_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"contents":[{"role":"user","parts":[{"text":"hi"}]}],"generationConfig":{"maxOutputTokens":1}}' \
            --connect-timeout 10 2>/dev/null)
        if [ "$FLASH_RESULT" = "200" ]; then
            log "Gemini Flash OK"
        else
            err "Gemini Flash failed (HTTP $FLASH_RESULT)"
            AI_OK=false
        fi

        echo -n "  Testing Claude Sonnet... "
        SONNET_URL="https://${GCP_REGION}-aiplatform.googleapis.com/v1/projects/${GCP_PROJECT}/locations/${GCP_REGION}/publishers/anthropic/models/claude-sonnet-4-6:rawPredict"
        SONNET_RESULT=$(curl -s -o /dev/null -w "%{http_code}" \
            -X POST "$SONNET_URL" \
            -H "Authorization: Bearer $ACCESS_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"anthropic_version":"vertex-2023-10-16","max_tokens":1,"messages":[{"role":"user","content":"hi"}]}' \
            --connect-timeout 10 2>/dev/null)
        if [ "$SONNET_RESULT" = "200" ]; then
            log "Claude Sonnet OK"
        else
            err "Claude Sonnet failed (HTTP $SONNET_RESULT)"
            if [ "$SONNET_RESULT" = "404" ]; then
                warn "Claude may not be available in region '$GCP_REGION'."
                warn "Try: us-east5, us-central1, or europe-west1."
            elif [ "$SONNET_RESULT" = "403" ]; then
                warn "Permission denied. Check IAM roles (Vertex AI User) on project '$GCP_PROJECT'."
            fi
            AI_OK=false
        fi
    fi

elif [ "$AI_PROVIDER" = "openai" ]; then
    # ── OpenAI ──────────────────────────────────────────────────
    ask "OpenAI API key (or Enter to set OPENAI_API_KEY env var later):"
    if [ -n "$REPLY" ]; then
        store_secret "OPENAI_API_KEY" "$REPLY"
        export OPENAI_API_KEY="$REPLY"

        echo -n "  Testing OpenAI API... "
        TEST_RESULT=$(curl -s -o /dev/null -w "%{http_code}" \
            -X POST "https://api.openai.com/v1/chat/completions" \
            -H "Authorization: Bearer $REPLY" \
            -H "Content-Type: application/json" \
            -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}],"max_tokens":1}' \
            --connect-timeout 10 2>/dev/null)
        if [ "$TEST_RESULT" = "200" ]; then
            log "OpenAI API OK"
        else
            err "OpenAI API test failed (HTTP $TEST_RESULT)"
            AI_OK=false
        fi
    else
        warn "Set OPENAI_API_KEY before running sync scripts."
        AI_OK=false
    fi

elif [ "$AI_PROVIDER" = "anthropic" ]; then
    # ── Anthropic ───────────────────────────────────────────────
    ask "Anthropic API key (or Enter to set ANTHROPIC_API_KEY env var later):"
    if [ -n "$REPLY" ]; then
        store_secret "ANTHROPIC_API_KEY" "$REPLY"
        export ANTHROPIC_API_KEY="$REPLY"

        echo -n "  Testing Anthropic API... "
        TEST_RESULT=$(curl -s -o /dev/null -w "%{http_code}" \
            -X POST "https://api.anthropic.com/v1/messages" \
            -H "x-api-key: $REPLY" \
            -H "anthropic-version: 2023-06-01" \
            -H "Content-Type: application/json" \
            -d '{"model":"claude-haiku-4-5-20251001","max_tokens":1,"messages":[{"role":"user","content":"hi"}]}' \
            --connect-timeout 10 2>/dev/null)
        if [ "$TEST_RESULT" = "200" ]; then
            log "Anthropic API OK"
        else
            err "Anthropic API test failed (HTTP $TEST_RESULT)"
            AI_OK=false
        fi
    else
        warn "Set ANTHROPIC_API_KEY before running sync scripts."
        AI_OK=false
    fi

elif [ "$AI_PROVIDER" = "ollama" ]; then
    # ── Ollama ──────────────────────────────────────────────────
    if ! command -v ollama &>/dev/null; then
        warn "Ollama not found. Install: https://ollama.ai"
        AI_OK=false
    else
        log "Ollama found"
    fi

    ask "Model to use? (default: llama3.1)"
    if [ -n "$REPLY" ]; then
        FAST_MODEL="ollama/$REPLY"
        REASONING_MODEL="ollama/$REPLY"
    fi

    if command -v ollama &>/dev/null; then
        OLLAMA_MODEL="${FAST_MODEL#ollama/}"
        echo -n "  Checking model $OLLAMA_MODEL... "
        if ollama list 2>/dev/null | grep -q "$OLLAMA_MODEL"; then
            log "$OLLAMA_MODEL available"
        else
            warn "$OLLAMA_MODEL not pulled yet. Pulling..."
            ollama pull "$OLLAMA_MODEL" || AI_OK=false
        fi
    fi

elif [ "$AI_PROVIDER" = "bedrock" ]; then
    # ── AWS Bedrock ─────────────────────────────────────────────
    if ! command -v aws &>/dev/null; then
        err "AWS CLI not found. Install: https://aws.amazon.com/cli/"
        AI_OK=false
    else
        log "AWS CLI found"
    fi

    ask "AWS region for Bedrock (default: us-east-1):"
    BEDROCK_REGION="${REPLY:-us-east-1}"

    if command -v aws &>/dev/null; then
        echo -n "  Testing Bedrock access... "
        aws bedrock list-foundation-models --region "$BEDROCK_REGION" --max-results 1 &>/dev/null 2>&1 && \
            log "Bedrock accessible" || { err "Bedrock access failed. Check AWS credentials."; AI_OK=false; }
    fi
fi

# ── Generate config files ─────────────────────────────────────
# openclaw.json       — gateway config (strict schema, validated by OpenClaw)
# openclaw-sync.json  — sync script config (models, project_id, auth details)

mkdir -p "$OPENCLAW_DIR"

# -- Gateway config (openclaw.json) --
if [ ! -f "$OPENCLAW_DIR/openclaw.json" ]; then
    GATEWAY_AUTH_MODE="oauth"
    GATEWAY_AUTH_PROFILE=""
    GATEWAY_MODEL="$FAST_MODEL"

    case "$AI_PROVIDER" in
        vertex)    GATEWAY_AUTH_PROFILE=""  ; GATEWAY_AUTH_PROFILE_KEY=""  ; GATEWAY_AUTH_PROVIDER="google-vertex"  ; GATEWAY_AUTH_MODE="oauth"  ; GATEWAY_MODEL="google-vertex/gemini-2.5-flash" ;;
        openai)    GATEWAY_AUTH_PROFILE="\"openai:default\": {\"provider\": \"openai\", \"mode\": \"api_key\"}"    ; GATEWAY_AUTH_PROFILE_KEY="openai:default"    ; GATEWAY_AUTH_PROVIDER="openai"    ; GATEWAY_AUTH_MODE="api_key" ;;
        anthropic) GATEWAY_AUTH_PROFILE="\"anthropic:default\": {\"provider\": \"anthropic\", \"mode\": \"api_key\"}" ; GATEWAY_AUTH_PROFILE_KEY="anthropic:default" ; GATEWAY_AUTH_PROVIDER="anthropic" ; GATEWAY_AUTH_MODE="api_key" ;;
        ollama)    GATEWAY_AUTH_PROFILE="\"ollama:default\": {\"provider\": \"ollama\", \"mode\": \"api_key\"}"    ; GATEWAY_AUTH_PROFILE_KEY="ollama:default"    ; GATEWAY_AUTH_PROVIDER="ollama"    ; GATEWAY_AUTH_MODE="api_key" ;;
        bedrock)   GATEWAY_AUTH_PROFILE="\"bedrock:default\": {\"provider\": \"bedrock\", \"mode\": \"oauth\"}"    ; GATEWAY_AUTH_PROFILE_KEY="bedrock:default"   ; GATEWAY_AUTH_PROVIDER="bedrock"   ; GATEWAY_AUTH_MODE="oauth" ;;
    esac

    VAULT_PATH_ESCAPED="${OBSIDIAN_VAULT:-$HOME/Documents/Obsidian Vault}"

    # Enable QMD if installed
    QMD_CONFIG=""
    if command -v qmd &>/dev/null; then
        QMD_CONFIG="$(cat << QMDBLOCK
  "memory": {
    "backend": "qmd",
    "qmd": {
      "paths": [
        { "name": "vault-people", "path": "$VAULT_PATH_ESCAPED/People", "pattern": "**/*.md" },
        { "name": "vault-clients", "path": "$VAULT_PATH_ESCAPED/Clients", "pattern": "**/*.md" },
        { "name": "transcripts", "path": "$WORKSPACE/transcriptions", "pattern": "**/*.txt" }
      ],
      "sessions": { "enabled": true }
    }
  },
QMDBLOCK
)"
    fi

    cat > "$OPENCLAW_DIR/openclaw.json" << GWEOF
{
  "gateway": {
    "mode": "local"
  },
  "auth": {
    "profiles": {
      $GATEWAY_AUTH_PROFILE
    }
  },
  $QMD_CONFIG
  "agents": {
    "defaults": {
      "model": {
        "primary": "$GATEWAY_MODEL"
      }
    },
    "list": [
      {
        "id": "main",
        "model": "$GATEWAY_MODEL"
      }
    ]
  }
}
GWEOF
    log "Created openclaw.json (gateway, $AI_PROVIDER)"
else
    log "openclaw.json exists"
fi

# Always ensure our config fields survive plugin installs that rewrite openclaw.json
VAULT_PATH_ESCAPED="${OBSIDIAN_VAULT:-$HOME/Documents/Obsidian Vault}"
python3 -c "
import json
config_path = '$OPENCLAW_DIR/openclaw.json'
with open(config_path) as f:
    config = json.load(f)

# Gateway mode
config.setdefault('gateway', {})['mode'] = 'local'

# Auth profile (skip for vertex — uses ADC auto-detection)
if '$GATEWAY_AUTH_PROFILE_KEY':
    auth = config.setdefault('auth', {})
    profiles = auth.setdefault('profiles', {})
    if '$GATEWAY_AUTH_PROFILE_KEY' not in profiles:
        profiles['$GATEWAY_AUTH_PROFILE_KEY'] = {'provider': '$GATEWAY_AUTH_PROVIDER', 'mode': '$GATEWAY_AUTH_MODE'}

# Agents
agents = config.setdefault('agents', {})
defaults = agents.setdefault('defaults', {})
model_config = {'primary': '$GATEWAY_MODEL'}
if '$AI_PROVIDER' == 'vertex':
    model_config['fallbacks'] = [
        'google-vertex/gemini-2.5-pro',
        'anthropic-vertex/claude-sonnet-4-6',
    ]
defaults['model'] = model_config
agent_entry = {'id': 'main', 'model': model_config}
if not agents.get('list'):
    agents['list'] = [agent_entry]
else:
    agents['list'][0]['model'] = model_config

# GCP env vars for Vertex AI (both Google and Anthropic providers need these)
if '$AI_PROVIDER' == 'vertex':
    import os
    env = config.setdefault('env', {})
    env_vars = env.setdefault('vars', {})
    env_vars['GOOGLE_CLOUD_PROJECT'] = '$GCP_PROJECT'
    env_vars['GOOGLE_CLOUD_LOCATION'] = '${GCP_REGION:-us-east5}'
    env_vars['ANTHROPIC_VERTEX_PROJECT_ID'] = '$GCP_PROJECT'
    env_vars['ANTHROPIC_VERTEX_REGION'] = '${GCP_REGION:-us-east5}'
    adc_path = os.path.expanduser('~/.config/gcloud/application_default_credentials.json')
    if os.path.exists(adc_path):
        env_vars['GOOGLE_APPLICATION_CREDENTIALS'] = adc_path

# Plugin allowlist (only load what we need)
plugins = config.setdefault('plugins', {})
if 'allow' not in plugins:
    plugins['allow'] = [
        'google', 'anthropic-vertex', 'acpx', 'openclaw-honcho',
        'memory-core', 'diffs', 'browser', 'slack',
    ]

# QMD memory
if 'qmd' not in config.get('memory', {}):
    config['memory'] = {
        'backend': 'qmd',
        'qmd': {
            'paths': [
                {'name': 'vault-people', 'path': '$VAULT_PATH_ESCAPED/People', 'pattern': '**/*.md'},
                {'name': 'vault-clients', 'path': '$VAULT_PATH_ESCAPED/Clients', 'pattern': '**/*.md'},
                {'name': 'transcripts', 'path': '$WORKSPACE/transcriptions', 'pattern': '**/*.txt'},
            ],
            'sessions': {'enabled': True},
        },
    }

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
" 2>/dev/null

# -- Sync script config (openclaw-sync.json) --
if [ ! -f "$OPENCLAW_DIR/openclaw-sync.json" ]; then
    SYNC_AUTH_PROFILE=""
    case "$AI_PROVIDER" in
        vertex)    SYNC_AUTH_PROFILE="\"vertex:default\": {\"provider\": \"vertex\", \"project_id\": \"$GCP_PROJECT\", \"region\": \"${GCP_REGION:-us-east5}\"}" ;;
        openai)    SYNC_AUTH_PROFILE="\"openai:default\": {\"provider\": \"openai\", \"api_key_env\": \"OPENAI_API_KEY\"}" ;;
        anthropic) SYNC_AUTH_PROFILE="\"anthropic:default\": {\"provider\": \"anthropic\", \"api_key_env\": \"ANTHROPIC_API_KEY\"}" ;;
        ollama)    SYNC_AUTH_PROFILE="\"ollama:default\": {\"provider\": \"ollama\", \"base_url\": \"http://localhost:11434\"}" ;;
        bedrock)   SYNC_AUTH_PROFILE="\"bedrock:default\": {\"provider\": \"bedrock\", \"region\": \"${BEDROCK_REGION:-us-east-1}\"}" ;;
    esac

    cat > "$OPENCLAW_DIR/openclaw-sync.json" << SYNCEOF
{
  "models": {
    "fast": "$FAST_MODEL",
    "reasoning": "$REASONING_MODEL"
  },
  "auth": {
    "profiles": {
      $SYNC_AUTH_PROFILE
    }
  }
}
SYNCEOF
    log "Created openclaw-sync.json (models: $FAST_MODEL / $REASONING_MODEL)"
else
    log "openclaw-sync.json exists"
fi

# Save client_secret for gogcli/vdirsyncer (needed regardless of AI provider)
if [ -f "$CLIENT_SECRET" ]; then
    cp "$CLIENT_SECRET" "$WORKSPACE/.client_secret.json" 2>/dev/null
    chmod 600 "$WORKSPACE/.client_secret.json" 2>/dev/null
fi

if [ "$AI_OK" = false ]; then
    warn "AI provider tests failed. Sync scripts may not work until resolved."
fi

else
    log "Skipping Phase 7 (--from $START_FROM)"
fi

# ─── Phase 8: Email & Calendar tools ─────────────────────────────

CURRENT_PHASE=8
if [ "$SKIP_GOOGLE" = false ] && should_run_phase; then
    step "Phase 8: Setting up email & calendar tools"
    if [ "$WIZARD" = true ]; then
        gum style --faint --italic "Connecting to your email and calendar so the agent can read transcripts and events."
        echo ""
    fi
    source "$SCRIPT_DIR/scripts/setup_email.sh"
elif [ "$SKIP_GOOGLE" = true ]; then
    warn "Skipping email & calendar setup"
else
    log "Skipping Phase 8 (--from $START_FROM)"
fi

# ─── Phase 9: Obsidian ──────────────────────────────────────────────

CURRENT_PHASE=9
if should_run_phase; then
    step "Phase 9: Setting up Obsidian vault"
    if [ "$WIZARD" = true ]; then
        gum style --faint --italic "Creating folders for people dossiers and client profiles in your Obsidian vault."
        echo ""
    fi
    source "$SCRIPT_DIR/scripts/setup_obsidian.sh"
else
    log "Skipping Phase 9 (--from $START_FROM)"
fi

# ─── Phase 10: Personalization ────────────────────────────────────────
# (Runs BEFORE data sync so user.json exists when scripts need USER_NAME)

CURRENT_PHASE=10
if should_run_phase; then
step "Phase 10: Personalization"
if [ "$WIZARD" = true ]; then
    gum style --faint --italic "Tell us about yourself so the agent knows who it's working for."
    echo ""
fi

echo ""
if [ -n "${PREV_NAME:-}" ]; then
    log "Previous config found. Press Enter to keep defaults."
    echo ""
fi
wizard_input "What's your name? (e.g., Jane Doe)${PREV_NAME:+ [${PREV_NAME}]}" "${PREV_NAME:-Jane Doe}"
USER_NAME="${REPLY:-$PREV_NAME}"

# Derive first name from full name
USER_FIRST="${USER_NAME%% *}"
log "First name: $USER_FIRST"

if [ -n "${PREV_TZ:-}" ]; then
    USER_TZ="$PREV_TZ"
    log "Timezone: $USER_TZ (from previous config)"
else
    DETECTED_TZ=""
    if [ "$PLATFORM" = "macos" ]; then
        DETECTED_TZ=$(readlink /etc/localtime 2>/dev/null | sed 's|.*/zoneinfo/||')
    elif command -v timedatectl &>/dev/null; then
        DETECTED_TZ=$(timedatectl show -p Timezone --value 2>/dev/null || true)
    fi
    if [ -z "$DETECTED_TZ" ]; then
        DETECTED_TZ=$(readlink /etc/localtime 2>/dev/null | sed 's|.*/zoneinfo/||' || true)
    fi
    if [ -n "$DETECTED_TZ" ]; then
        log "Detected timezone: $DETECTED_TZ"
        ask "Use $DETECTED_TZ as your timezone? (y/n, or type a different one)"
        if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ] || [ -z "$REPLY" ]; then
            USER_TZ="$DETECTED_TZ"
        else
            USER_TZ="$REPLY"
        fi
    else
        ask "What's your timezone? (e.g., America/Denver)"
        USER_TZ="$REPLY"
    fi
fi

wizard_input "What's your email?${PREV_EMAIL:+ [${PREV_EMAIL}]}" "${PREV_EMAIL:-jane@company.com}"
USER_EMAIL="${REPLY:-$PREV_EMAIL}"

wizard_input "Your Slack user ID?${PREV_SLACK_ID:+ [${PREV_SLACK_ID}]}" "${PREV_SLACK_ID:-UXXXXXXXXXX}"
USER_SLACK_ID="${REPLY:-$PREV_SLACK_ID}"

wizard_input "Your Slack username?${PREV_SLACK_USER:+ [${PREV_SLACK_USER}]}" "${PREV_SLACK_USER:-jdoe}"
SLACK_USERNAME="${REPLY:-$PREV_SLACK_USER}"

wizard_input "Your title/role?${PREV_TITLE:+ [${PREV_TITLE}]}" "${PREV_TITLE:-CTO}"
USER_TITLE="${REPLY:-$PREV_TITLE}"

wizard_input "Company name?${PREV_COMPANY:+ [${PREV_COMPANY}]}" "${PREV_COMPANY:-Acme Corp}"
USER_COMPANY="${REPLY:-$PREV_COMPANY}"

SYNC_GITHUB=false
GITHUB_USERNAME="${PREV_GITHUB:-}"
if [ -n "$PREV_GITHUB" ]; then
    SYNC_GITHUB=true
    wizard_input "GitHub username? [${PREV_GITHUB}]" "$PREV_GITHUB"
    GITHUB_USERNAME="${REPLY:-$PREV_GITHUB}"
elif wizard_confirm "Do you use GitHub for work?"; then
    SYNC_GITHUB=true
    wizard_input "GitHub username?" "jdoe"
    GITHUB_USERNAME="$REPLY"
fi

SERVICES_BIZ=false
if [ "${PREV_SERVICES:-}" = "true" ]; then
    SERVICES_BIZ=true
    log "Services business: yes (from previous config)"
elif wizard_confirm "Is your company services-based (agency, consulting)?"; then
    SERVICES_BIZ=true
fi

if [ -n "$USER_NAME" ]; then
    sedi "s/\[YOUR NAME\]/$USER_NAME/g" "$WORKSPACE/USER.md" 2>/dev/null || true
    sedi "s/\[YOUR NAME\]/$USER_NAME/g" "$WORKSPACE/SOUL.md" 2>/dev/null || true
    log "Set name: $USER_NAME"
fi

if [ -n "$USER_TZ" ]; then
    sedi "s|America/Denver|$USER_TZ|g" "$WORKSPACE/USER.md" 2>/dev/null || true
    log "Set timezone: $USER_TZ"
fi

if [ -n "$USER_EMAIL" ]; then
    sedi "s/\[YOUR EMAIL\]/$USER_EMAIL/g" "$WORKSPACE/TOOLS.md" 2>/dev/null || true
    log "Set email: $USER_EMAIL"
fi

# Build name keywords for Slack mention detection (lowercase variants)
# Build name keywords for Slack mention detection
FIRST_LOWER=$(echo "$USER_FIRST" | tr '[:upper:]' '[:lower:]')
LAST_LOWER=$(echo "$USER_NAME" | awk '{print $NF}' | tr '[:upper:]' '[:lower:]')
NAME_KEYWORDS_ITEMS="\"$FIRST_LOWER\", \"$LAST_LOWER\""
if [ -n "$SLACK_USERNAME" ]; then
    SLACK_USER_LOWER=$(echo "$SLACK_USERNAME" | tr '[:upper:]' '[:lower:]')
    NAME_KEYWORDS_ITEMS="$NAME_KEYWORDS_ITEMS, \"$SLACK_USER_LOWER\""
fi
if [ -n "$GITHUB_USERNAME" ]; then
    GH_USER_LOWER=$(echo "$GITHUB_USERNAME" | tr '[:upper:]' '[:lower:]')
    NAME_KEYWORDS_ITEMS="$NAME_KEYWORDS_ITEMS, \"$GH_USER_LOWER\""
fi
NAME_KEYWORDS="[$NAME_KEYWORDS_ITEMS]"

# Write user.json for sync scripts
cat > "$WORKSPACE/user.json" << USERJSON
{
  "name": "${USER_NAME:-[YOUR NAME]}",
  "first_name": "${USER_FIRST:-[FIRST]}",
  "email": "${USER_EMAIL:-}",
  "timezone": "${USER_TZ:-America/Denver}",
  "slack_user_id": "${USER_SLACK_ID:-}",
  "slack_username": "${SLACK_USERNAME:-}",
  "github_username": "${GITHUB_USERNAME:-}",
  "name_keywords": ${NAME_KEYWORDS},
  "title": "${USER_TITLE:-}",
  "company": "${USER_COMPANY:-}",
  "services_business": $SERVICES_BIZ,
  "email_provider": "${EMAIL_PROVIDER:-google}",
  "imap_server": "${IMAP_SERVER:-}",
  "imap_port": ${IMAP_PORT:-993},
  "imap_username": "${IMAP_USERNAME:-}",
  "calendar_provider": "${CALENDAR_PROVIDER:-google}",
  "keychain": $USE_KEYCHAIN
}
USERJSON
log "Created user.json"

# Copy company template
cp "$SCRIPT_DIR/templates/company-template.md" "$WORKSPACE/references/"
log "Copied company profile template"

else
    log "Skipping Phase 10 (--from $START_FROM)"
fi

# ─── Phase 11: Full Workspace Sync + Discovery ───────────────────────

CURRENT_PHASE=11
if should_run_phase; then
step "Phase 11: Full workspace sync + discovery"

if [ "$WIZARD" = true ]; then
    gum style --faint --italic "This downloads your communication history, loads it into memory,"
    gum style --faint --italic "and uses AI to identify your key people, clients, and channels."
    echo ""
fi

SYNC_NOW=false
if wizard_confirm "Run full workspace sync now? (takes 5-10 minutes)"; then
    SYNC_NOW=true
fi

if [ "$SYNC_NOW" = true ]; then

    # ── Step 1: Download all data ────────────────────────────────────
    step "Step 1/5: Downloading data"
    if [ "$WIZARD" = true ]; then
        gum style --faint --italic "Pulling 3 months of Slack, email transcripts, and 30 days of calendar events."
        echo ""
    fi

    wizard_spin "Refreshing calendar" "$HOME/.openclaw/venv/bin/vdirsyncer" sync || warn "Calendar sync had errors"

    log "Syncing Slack messages (3 months)..."
    "$VENV_PYTHON" "$WORKSPACE/scripts/slack_sync.py" --hours 2160 --skip-threads 2>&1 | tee -a "$SETUP_LOG" || warn "Slack sync had errors"

    log "Downloading meeting transcripts..."
    "$VENV_PYTHON" "$WORKSPACE/scripts/sync_meeting_transcripts.py" --full --skip-actions 2>&1 | tee -a "$SETUP_LOG" || warn "Transcript sync had errors"

    wizard_spin "Parsing calendar events (30 days)" "$VENV_PYTHON" "$WORKSPACE/scripts/sync_calendar.py" --days 30 || warn "Calendar parse had errors"

    if [ "$SYNC_GITHUB" = true ]; then
        wizard_spin "Syncing GitHub activity" "$VENV_PYTHON" "$WORKSPACE/scripts/sync_github.py" --days 90 || warn "GitHub sync had errors"
    fi

    # ── Step 2: Filter and classify ──────────────────────────────────
    step "Step 2/5: Identifying bots and noise"
    if [ "$WIZARD" = true ]; then
        gum style --faint --italic "Auto-detecting bot accounts and noisy channels to filter out."
        echo ""
    fi

    wizard_spin "Discovering workspace" "$VENV_PYTHON" "$WORKSPACE/scripts/discover_workspace.py" --force || warn "Discovery had errors"

    # ── Step 3: Load into Honcho ─────────────────────────────────────
    step "Step 3/5: Loading data into memory"
    if [ "$WIZARD" = true ]; then
        gum style --faint --italic "Pushing all downloaded data into the AI's long-term memory."
        echo ""
    fi

    log "Loading Slack messages into Honcho..."
    "$VENV_PYTHON" "$WORKSPACE/scripts/honcho_slack_sync.py" 2>&1 | tee -a "$SETUP_LOG" || warn "Honcho Slack sync had errors"

    wizard_spin "Loading transcripts, calendar, and GitHub data" "$VENV_PYTHON" "$WORKSPACE/scripts/load_to_honcho.py" || warn "Honcho data load had errors"

    # ── Step 4: LLM priority analysis ────────────────────────────────
    step "Step 4/5: Analyzing priorities"
    if [ "$WIZARD" = true ]; then
        gum style --faint --italic "Using AI to determine who matters most, identify clients, and rank channels."
        echo ""
    fi

    ANALYZE_FLAGS=""
    if [ "$SERVICES_BIZ" = true ]; then
        ANALYZE_FLAGS="--services-business"
    fi
    log "Analyzing priorities..."
    "$VENV_PYTHON" "$WORKSPACE/scripts/analyze_priorities.py" $ANALYZE_FLAGS 2>&1 | tee -a "$SETUP_LOG" || warn "Priority analysis had errors"

    # ── Step 5: Generate dossiers + client profiles ──────────────────
    step "Step 5/5: Generating dossiers"
    if [ "$WIZARD" = true ]; then
        gum style --faint --italic "Creating Obsidian profiles for every tracked person and client company."
        echo ""
    fi

    log "Generating dossiers..."
    "$VENV_PYTHON" "$WORKSPACE/scripts/generate_initial_dossiers.py" --type all --priority all 2>&1 | tee -a "$SETUP_LOG" || warn "Dossier generation had errors"

    log "Workspace sync complete!"
    log "Review your team config:    ~/.openclaw/workspace/team.json"
    log "Review people dossiers:     ~/Documents/Obsidian Vault/People/"
    log "Review client profiles:     ~/Documents/Obsidian Vault/Clients/"
else
    warn "Skipping workspace sync. Run later with:"
    warn "  $VENV_PYTHON ~/.openclaw/workspace/scripts/slack_sync.py --hours 2160"
    warn "  $VENV_PYTHON ~/.openclaw/workspace/scripts/sync_meeting_transcripts.py --full"
    warn "  $VENV_PYTHON ~/.openclaw/workspace/scripts/sync_calendar.py --days 30"
    warn "  $VENV_PYTHON ~/.openclaw/workspace/scripts/discover_workspace.py --force"
    warn "  $VENV_PYTHON ~/.openclaw/workspace/scripts/honcho_slack_sync.py"
    warn "  $VENV_PYTHON ~/.openclaw/workspace/scripts/load_to_honcho.py"
    warn "  $VENV_PYTHON ~/.openclaw/workspace/scripts/analyze_priorities.py"
    warn "  $VENV_PYTHON ~/.openclaw/workspace/scripts/generate_initial_dossiers.py --type all"
fi

else
    log "Skipping Phase 11 (--from $START_FROM)"
fi

# ─── Phase 12: Start ────────────────────────────────────────────────

CURRENT_PHASE=12
if should_run_phase; then
step "Phase 12: Starting OpenClaw"
if [ "$WIZARD" = true ]; then
    gum style --faint --italic "Starting the agent gateway and scheduling recurring jobs."
    echo ""
fi

if [ "$DRY_RUN" = true ]; then
    warn "Dry run, not starting gateway"
else
    echo ""
    if wizard_confirm "Start the OpenClaw gateway now?"; then
        wizard_spin "Starting gateway" openclaw gateway start
        sleep 3
        openclaw status
        log "Gateway is running"

        echo ""
        if wizard_confirm "Create cron jobs now?"; then
            source "$SCRIPT_DIR/scripts/setup_crons.sh"
        fi
    fi
fi

else
    log "Skipping Phase 12 (--from $START_FROM)"
fi

# ─── Done ────────────────────────────────────────────────────────────

CURRENT_PHASE=$TOTAL_PHASES
step "Setup Complete"

if [ "$WIZARD" = true ]; then
    echo ""
    gum style --border rounded --border-foreground 2 --padding "1 3" --bold --align center \
        "Your OpenClaw agent is ready!"
    echo ""
    gum style --bold "What your agent can do now:"
    echo ""
    gum style "  📅  Morning briefings at 8am with calendar, Slack highlights, and TODOs"
    gum style "  👥  Auto-maintained dossiers on everyone you work with"
    gum style "  📝  Action items extracted from every meeting transcript"
    gum style "  💬  Slack scanning for things directed at you"
    gum style "  🌙  End-of-day wrap with dossier updates and learnings"
    echo ""
    gum style --bold "Try these first:"
    echo ""
    gum style "  1. Open the TUI:  $(gum style --foreground 4 'openclaw tui')"
    gum style "  2. Say:           $(gum style --foreground 4 'What is on my calendar today?')"
    gum style "  3. Or ask:        $(gum style --foreground 4 'Give me a briefing on [person name]')"
    echo ""
    gum style --bold "Customize your agent:"
    echo ""
    gum style "  Personality:  ~/.openclaw/workspace/SOUL.md"
    gum style "  Your context: ~/.openclaw/workspace/USER.md"
    gum style "  Team config:  ~/.openclaw/workspace/team.json"
    echo ""
    gum style --bold "Need to redo a step?"
    echo ""
    gum style "  ./setup.sh --from 7    # Restart from Phase 7 (AI provider)"
    gum style "  ./setup.sh --from 10   # Redo personalization + sync"
    gum style "  ./setup.sh --from 11   # Re-run workspace sync only"
    echo ""
    gum style --bold "Logs:"
    echo ""
    gum style "  Full setup log: ~/.openclaw/setup.log"
    echo ""
else
    echo "Next steps:"
    echo "  1. Edit ~/.openclaw/workspace/SOUL.md with your agent's personality"
    echo "  2. Edit ~/.openclaw/workspace/USER.md with your info"
    echo "  3. Add API keys to ~/.openclaw/workspace/TOOLS.md"
    echo "  4. Start the TUI: openclaw tui"
    echo "  5. Say hello!"
    echo ""
    echo "To redo a step: ./setup.sh --from N (e.g., --from 7 for AI provider)"
    echo "Full log: ~/.openclaw/setup.log"
    echo ""
fi
log "Done."
