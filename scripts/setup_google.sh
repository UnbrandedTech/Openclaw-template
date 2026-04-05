#!/bin/bash
# Set up Google Workspace tools (gogcli + vdirsyncer)
# Auth is already done in Phase 7 — this just configures the tools.

CLIENT_SECRET="$WORKSPACE/.client_secret.json"
CLIENT_ID=$(jq -r '.installed.client_id' "$CLIENT_SECRET" 2>/dev/null)
CLIENT_SECRET_VAL=$(jq -r '.installed.client_secret' "$CLIENT_SECRET" 2>/dev/null)

# ── 1. gogcli (Google OAuth CLI for Gmail) ───────────────────────────

if ! command -v gog &>/dev/null; then
    echo "Installing gogcli..."
    TMPDIR=$(mktemp -d)
    if ! command -v make &>/dev/null; then
        err "Cannot build gogcli: 'make' not found."
        if [ "$PLATFORM" = "macos" ]; then
            err "  Install Xcode CLI tools: xcode-select --install"
        else
            err "  Install build tools: sudo apt-get install build-essential (Debian) or sudo dnf install make gcc (Fedora)"
        fi
        warn "Skipping gogcli installation."
    else
        (
            cd "$TMPDIR"
            git clone https://github.com/steipete/gogcli.git
            cd gogcli
            make
            mkdir -p ~/.local/bin
            cp gog ~/.local/bin/
        )
        rm -rf "$TMPDIR"

        if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
            export PATH="$HOME/.local/bin:$PATH"
        fi
        log "gogcli installed to ~/.local/bin/gog"
    fi
else
    log "gogcli already installed"
fi

# Configure gogcli with the shared OAuth client
if command -v gog &>/dev/null && [ -f "$CLIENT_SECRET" ]; then
    if ! gog auth list 2>/dev/null | grep -q "@"; then
        log "Setting up gogcli with shared OAuth credentials..."
        gog auth credentials "$CLIENT_SECRET"

        ask "Google account email to authenticate gogcli:"
        if [ -n "$REPLY" ]; then
            gog auth add "$REPLY"
            log "gogcli authenticated as $REPLY"
            echo "GOG_ACCOUNT=$REPLY" >> "$WORKSPACE/.google_env"
        fi
    else
        log "gogcli already authenticated"
    fi
else
    if ! command -v gog &>/dev/null; then
        warn "gogcli not installed — Gmail sync won't work"
    fi
fi

# ── 2. vdirsyncer + khal for calendar ───────────────────────────────

"$HOME/.openclaw/venv/bin/pip" install vdirsyncer khal

if [ ! -f ~/.config/vdirsyncer/config ]; then
    mkdir -p ~/.config/vdirsyncer
    mkdir -p ~/.local/share/vdirsyncer/{status,calendars}

    # Use the same OAuth client credentials
    VDIR_CLIENT_ID="${CLIENT_ID:-YOUR_CLIENT_ID}"
    VDIR_CLIENT_SECRET="${CLIENT_SECRET_VAL:-YOUR_CLIENT_SECRET}"

    cat > ~/.config/vdirsyncer/config << VDIREOF
[general]
status_path = "~/.local/share/vdirsyncer/status/"

[pair calendars]
a = "calendars_local"
b = "calendars_remote"
collections = ["from a", "from b"]

[storage calendars_local]
type = "filesystem"
path = "~/.local/share/vdirsyncer/calendars/"
fileext = ".ics"

[storage calendars_remote]
type = "google_calendar"
token_file = "~/.config/vdirsyncer/google_token"
client_id = "$VDIR_CLIENT_ID"
client_secret = "$VDIR_CLIENT_SECRET"
VDIREOF

    if [ "$VDIR_CLIENT_ID" != "YOUR_CLIENT_ID" ]; then
        log "vdirsyncer config created with OAuth credentials"
        warn "Run these to finish calendar setup:"
        warn "  vdirsyncer discover calendars"
        warn "  vdirsyncer sync"
    else
        warn "vdirsyncer config created but missing OAuth credentials"
    fi
else
    log "vdirsyncer already configured"
fi

log "Google Workspace tools ready"
