#!/bin/bash
# Set up Google Workspace (Gmail, Calendar, Drive)

# gogcli
if ! command -v gog &>/dev/null; then
    echo "Installing gogcli..."
    TMPDIR=$(mktemp -d)
    cd "$TMPDIR"
    git clone https://github.com/steipete/gogcli.git
    cd gogcli
    make
    mkdir -p ~/.local/bin
    cp gog ~/.local/bin/
    cd ~
    rm -rf "$TMPDIR"

    # Add to PATH if needed
    if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
        export PATH="$HOME/.local/bin:$PATH"
    fi
    log "gogcli installed to ~/.local/bin/gog"
else
    log "gogcli already installed"
fi

echo ""
echo "Google OAuth setup:"
echo "  If your org already has an OAuth client (marathondataco.com does),"
echo "  just use the existing client_secret JSON file."
echo ""
echo "  Otherwise:"
echo "  1. Go to console.cloud.google.com"
echo "  2. Create a project, enable Gmail/Calendar/Drive APIs"
echo "  3. Create OAuth 2.0 Client ID (Desktop type)"
echo "  4. Download the client_secret JSON"
echo ""

ask "Path to client_secret JSON (or press Enter to skip):"
if [ -n "$REPLY" ] && [ -f "$REPLY" ]; then
    gog auth credentials "$REPLY"
    log "OAuth credentials loaded"

    ask "Google account email to authenticate:"
    if [ -n "$REPLY" ]; then
        gog auth add "$REPLY"
        log "Authenticated as $REPLY"

        # Save for TOOLS.md
        echo "GOG_ACCOUNT=$REPLY" >> "$WORKSPACE/.google_env"
    fi
else
    warn "Skipping Google auth. Run 'gog auth credentials <json>' later."
fi

# vdirsyncer + khal for calendar
pip3 install --break-system-packages vdirsyncer khal 2>/dev/null || pip3 install vdirsyncer khal

if [ ! -f ~/.config/vdirsyncer/config ]; then
    mkdir -p ~/.config/vdirsyncer
    mkdir -p ~/.local/share/vdirsyncer/{status,calendars}

    cat > ~/.config/vdirsyncer/config << 'VDIREOF'
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
client_id = "YOUR_CLIENT_ID"
client_secret = "YOUR_CLIENT_SECRET"
VDIREOF
    warn "vdirsyncer config created at ~/.config/vdirsyncer/config"
    warn "You need to fill in client_id and client_secret, then run:"
    warn "  vdirsyncer discover calendars"
    warn "  vdirsyncer sync"
else
    log "vdirsyncer already configured"
fi

log "Google Workspace setup complete"
