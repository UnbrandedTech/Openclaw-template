#!/bin/bash
set -e

# OpenClaw Setup
# Usage: ./setup.sh [--skip-deps] [--skip-google] [--skip-slack] [--dry-run]
# --skip-google skips email & calendar tool setup (Phase 8)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCLAW_DIR="$HOME/.openclaw"
WORKSPACE="$OPENCLAW_DIR/workspace"

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

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; }
step() { echo -e "\n${BLUE}━━━ $1 ━━━${NC}\n"; }
ask()  { echo -e "${YELLOW}$1${NC}"; read -r REPLY; }

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

# Copy team config (don't overwrite — user may have customized it)
if [ ! -f "$WORKSPACE/team.json" ]; then
    cp "$SCRIPT_DIR/templates/team.json" "$WORKSPACE/team.json"
    log "Created team.json (edit to match your team)"
else
    warn "team.json already exists, skipping"
fi

# ─── Phase 4: Sync Scripts ─────────────────────────────────────────

step "Phase 4: Installing sync scripts"

for f in "$SCRIPT_DIR"/sync-scripts/*.py; do
    fname=$(basename "$f")
    cp "$f" "$WORKSPACE/scripts/$fname"
done
log "Copied $(ls "$SCRIPT_DIR"/sync-scripts/*.py | wc -l | tr -d ' ') scripts to workspace"

# Install Python dependencies for scripts
"$HOME/.openclaw/venv/bin/pip" install slack-sdk honcho-ai
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

# ─── Phase 7: Google Cloud (Vertex AI + Workspace) ────────────────

step "Phase 7: Google Cloud authentication"

CLIENT_SECRET="$SCRIPT_DIR/templates/client_secret.json"
GCP_PROJECT=$(jq -r '.auth.profiles["vertex:default"].project_id' "$SCRIPT_DIR/templates/openclaw.json")
GCP_REGION=$(jq -r '.auth.profiles["vertex:default"].region' "$SCRIPT_DIR/templates/openclaw.json")
GCP_OK=true

# All scopes needed: Vertex AI (cloud-platform) + Gmail + Calendar + Drive
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

# 2. Check client_secret.json exists
if [ ! -f "$CLIENT_SECRET" ]; then
    err "OAuth client_secret.json not found at $CLIENT_SECRET"
    err "Ask your admin for the client_secret JSON from the GCP project."
    GCP_OK=false
fi

# 3. Single login — Vertex AI + Gmail + Calendar + Drive in one consent screen
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

# 4. Check project access
if [ "$GCP_OK" = true ]; then
    if gcloud projects describe "$GCP_PROJECT" &>/dev/null 2>&1; then
        log "GCP project '$GCP_PROJECT' accessible"
    else
        err "Cannot access GCP project '$GCP_PROJECT'."
        warn "Make sure you have been granted access to the project."
        GCP_OK=false
    fi
fi

# 5. Check APIs are enabled
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

# 6. Test Vertex AI — one Gemini Flash call + one Claude Sonnet call
if [ "$GCP_OK" = true ]; then
    ACCESS_TOKEN=$(gcloud auth application-default print-access-token 2>/dev/null)

    # Test Gemini Flash (used for most crons + heartbeat)
    echo -n "  Testing Gemini Flash... "
    FLASH_URL="https://${GCP_REGION}-aiplatform.googleapis.com/v1/projects/${GCP_PROJECT}/locations/${GCP_REGION}/publishers/google/models/gemini-2.5-flash:generateContent"
    FLASH_RESULT=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$FLASH_URL" \
        -H "Authorization: Bearer $ACCESS_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"contents":[{"parts":[{"text":"hi"}]}],"generationConfig":{"maxOutputTokens":1}}' \
        --connect-timeout 10 2>/dev/null)
    if [ "$FLASH_RESULT" = "200" ]; then
        log "Gemini Flash OK"
    else
        err "Gemini Flash failed (HTTP $FLASH_RESULT)"
        GCP_OK=false
    fi

    # Test Claude Sonnet (used for EOD dossier merging)
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
        GCP_OK=false
    fi
fi

# Copy OpenClaw config
if [ ! -f "$OPENCLAW_DIR/openclaw.json" ]; then
    cp "$SCRIPT_DIR/templates/openclaw.json" "$OPENCLAW_DIR/openclaw.json"
    log "Created openclaw.json (Vertex AI, project: $GCP_PROJECT, region: $GCP_REGION)"
else
    log "openclaw.json exists"
fi

# Save client_secret for gogcli/vdirsyncer to use
cp "$CLIENT_SECRET" "$WORKSPACE/.client_secret.json" 2>/dev/null
chmod 600 "$WORKSPACE/.client_secret.json" 2>/dev/null

if [ "$GCP_OK" = false ]; then
    warn "Some GCP checks failed. OpenClaw may not work until these are resolved."
fi

# ─── Phase 8: Email & Calendar tools ─────────────────────────────

if [ "$SKIP_GOOGLE" = false ]; then
    step "Phase 8: Setting up email & calendar tools"
    source "$SCRIPT_DIR/scripts/setup_email.sh"
else
    warn "Skipping email & calendar setup"
fi

# ─── Phase 9: Obsidian ──────────────────────────────────────────────

step "Phase 9: Setting up Obsidian vault"
source "$SCRIPT_DIR/scripts/setup_obsidian.sh"

# ─── Phase 10: Personalization ────────────────────────────────────────
# (Runs BEFORE data sync so user.json exists when scripts need USER_NAME)

step "Phase 10: Personalization"

VENV_PYTHON="$HOME/.openclaw/venv/bin/python3"

echo ""
ask "What's your name? (e.g., Jane Doe)"
USER_NAME="$REPLY"

ask "What's your first name?"
USER_FIRST="$REPLY"

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

ask "What's your email? (for Google Workspace)"
USER_EMAIL="$REPLY"

ask "Your Slack user ID? (e.g., UXXXXXXXXXX, press Enter to skip)"
USER_SLACK_ID="$REPLY"

ask "Your title/role? (e.g., CTO, press Enter to skip)"
USER_TITLE="$REPLY"

ask "Company name? (press Enter to skip)"
USER_COMPANY="$REPLY"

ask "Do you use GitHub for work? (y/n)"
SYNC_GITHUB=false
if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
    SYNC_GITHUB=true
fi

ask "Is your company services-based (agency, consulting, etc.)? (y/n)"
SERVICES_BIZ=false
if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
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
FIRST_LOWER=$(echo "$USER_FIRST" | tr '[:upper:]' '[:lower:]')
LAST_LOWER=$(echo "$USER_NAME" | awk '{print $NF}' | tr '[:upper:]' '[:lower:]')
NAME_KEYWORDS="[\"$FIRST_LOWER\", \"$LAST_LOWER\"]"

# Write user.json for sync scripts
cat > "$WORKSPACE/user.json" << USERJSON
{
  "name": "${USER_NAME:-[YOUR NAME]}",
  "first_name": "${USER_FIRST:-[FIRST]}",
  "email": "${USER_EMAIL:-}",
  "timezone": "${USER_TZ:-America/Denver}",
  "slack_user_id": "${USER_SLACK_ID:-}",
  "name_keywords": ${NAME_KEYWORDS},
  "title": "${USER_TITLE:-}",
  "company": "${USER_COMPANY:-}",
  "services_business": $SERVICES_BIZ,
  "email_provider": "${EMAIL_PROVIDER:-google}",
  "imap_server": "${IMAP_SERVER:-}",
  "imap_port": ${IMAP_PORT:-993},
  "imap_username": "${IMAP_USERNAME:-}",
  "calendar_provider": "${CALENDAR_PROVIDER:-google}"
}
USERJSON
log "Created user.json"

# Copy company template
cp "$SCRIPT_DIR/templates/company-template.md" "$WORKSPACE/references/"
log "Copied company profile template"

# ─── Phase 11: Full Workspace Sync + Discovery ───────────────────────

step "Phase 11: Full workspace sync + discovery"

echo "This will download your Slack, email, and calendar history,"
echo "load it into Honcho, and use AI to identify your key people,"
echo "clients, and priority channels."
echo ""
ask "Run full workspace sync now? This takes 5-10 minutes. (y/n)"
if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then

    # ── Step 1: Download all data ────────────────────────────────────
    step "Step 1/5: Downloading data"

    log "Refreshing calendar..."
    vdirsyncer sync 2>/dev/null || warn "Calendar sync had errors (vdirsyncer may need 'discover' first)"

    log "Syncing Slack messages (3 months)..."
    "$VENV_PYTHON" "$WORKSPACE/scripts/slack_sync.py" --hours 2160 --skip-threads || warn "Slack sync had errors"

    log "Downloading meeting transcripts..."
    "$VENV_PYTHON" "$WORKSPACE/scripts/sync_meeting_transcripts.py" --full --skip-actions || warn "Transcript sync had errors"

    log "Parsing calendar events (30 days)..."
    "$VENV_PYTHON" "$WORKSPACE/scripts/sync_calendar.py" --days 30 || warn "Calendar parse had errors"

    if [ "$SYNC_GITHUB" = true ]; then
        log "Syncing GitHub activity..."
        "$VENV_PYTHON" "$WORKSPACE/scripts/sync_github.py" --days 90 || warn "GitHub sync had errors"
    fi

    # ── Step 2: Filter and classify ──────────────────────────────────
    step "Step 2/5: Identifying bots and noise"

    "$VENV_PYTHON" "$WORKSPACE/scripts/discover_workspace.py" --force || warn "Discovery had errors"

    # ── Step 3: Load into Honcho ─────────────────────────────────────
    step "Step 3/5: Loading data into memory"

    log "Loading Slack messages..."
    "$VENV_PYTHON" "$WORKSPACE/scripts/honcho_slack_sync.py" || warn "Honcho Slack sync had errors"

    log "Loading transcripts, calendar, and GitHub data..."
    "$VENV_PYTHON" "$WORKSPACE/scripts/load_to_honcho.py" --all || warn "Honcho data load had errors"

    # ── Step 4: LLM priority analysis ────────────────────────────────
    step "Step 4/5: Analyzing priorities (Sonnet)"

    ANALYZE_FLAGS=""
    if [ "$SERVICES_BIZ" = true ]; then
        ANALYZE_FLAGS="--services-business"
    fi
    "$VENV_PYTHON" "$WORKSPACE/scripts/analyze_priorities.py" $ANALYZE_FLAGS || warn "Priority analysis had errors"

    # ── Step 5: Generate dossiers + client profiles ──────────────────
    step "Step 5/5: Generating dossiers"

    "$VENV_PYTHON" "$WORKSPACE/scripts/generate_initial_dossiers.py" --type all --priority all || warn "Dossier generation had errors"

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
    warn "  $VENV_PYTHON ~/.openclaw/workspace/scripts/load_to_honcho.py --all"
    warn "  $VENV_PYTHON ~/.openclaw/workspace/scripts/analyze_priorities.py"
    warn "  $VENV_PYTHON ~/.openclaw/workspace/scripts/generate_initial_dossiers.py --type all"
fi

# ─── Phase 12: Start ────────────────────────────────────────────────

step "Phase 12: Starting OpenClaw"

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
