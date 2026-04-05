#!/bin/bash
# Set up email + calendar sync tools
# Supports Google Workspace (gogcli) and generic IMAP
# Uses $WORKSPACE from setup.sh

CLIENT_SECRET="$WORKSPACE/.client_secret.json"
CLIENT_ID=$(jq -r '.installed.client_id' "$CLIENT_SECRET" 2>/dev/null)
CLIENT_SECRET_VAL=$(jq -r '.installed.client_secret' "$CLIENT_SECRET" 2>/dev/null)

# ── Email provider selection ────────────────────────────────────────

echo ""
echo "Email provider for transcript sync:"
echo "  1) Google Workspace (Gmail via gogcli + OAuth)"
echo "  2) IMAP (Outlook, iCloud, Fastmail, ProtonMail, etc.)"
echo "  3) Skip email sync"
echo ""
ask "Choose (1-3, default 1):"

case "${REPLY:-1}" in
    2) EMAIL_PROVIDER="imap" ;;
    3) EMAIL_PROVIDER="none" ;;
    *) EMAIL_PROVIDER="google" ;;
esac

# ── 1a. Google: gogcli ──────────────────────────────────────────────

if [ "$EMAIL_PROVIDER" = "google" ]; then
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

# ── 1b. IMAP configuration ─────────────────────────────────────────

elif [ "$EMAIL_PROVIDER" = "imap" ]; then
    echo ""
    echo "Common IMAP servers:"
    echo "  Outlook/Microsoft 365:  outlook.office365.com"
    echo "  iCloud:                 imap.mail.me.com"
    echo "  Fastmail:               imap.fastmail.com"
    echo "  ProtonMail Bridge:      127.0.0.1 (port 1143)"
    echo "  Yahoo:                  imap.mail.yahoo.com"
    echo ""
    ask "IMAP server hostname:"
    IMAP_SERVER="$REPLY"

    ask "IMAP port (default: 993):"
    IMAP_PORT="${REPLY:-993}"

    ask "IMAP username (usually your email address):"
    IMAP_USERNAME="$REPLY"

    ask "IMAP password or app password (stored in .env, not in config files):"
    if [ -n "$REPLY" ]; then
        mkdir -p "$WORKSPACE"
        # Append to .env, avoiding duplicates
        if grep -q "^IMAP_PASSWORD=" "$WORKSPACE/.env" 2>/dev/null; then
            sed -i "s|^IMAP_PASSWORD=.*|IMAP_PASSWORD=$REPLY|" "$WORKSPACE/.env" 2>/dev/null || \
                sed -i '' "s|^IMAP_PASSWORD=.*|IMAP_PASSWORD=$REPLY|" "$WORKSPACE/.env"
        else
            echo "IMAP_PASSWORD=$REPLY" >> "$WORKSPACE/.env"
        fi
        chmod 600 "$WORKSPACE/.env"
        export IMAP_PASSWORD="$REPLY"
        log "IMAP password saved to .env"
    else
        warn "Set IMAP_PASSWORD in ~/.openclaw/workspace/.env before running transcript sync."
    fi

    log "IMAP configured: $IMAP_USERNAME @ $IMAP_SERVER:$IMAP_PORT"

elif [ "$EMAIL_PROVIDER" = "none" ]; then
    warn "Email sync disabled. Transcript downloads will be skipped."
fi

# ── Calendar provider selection ─────────────────────────────────────

echo ""
echo "Calendar provider:"
echo "  1) Google Calendar"
echo "  2) CalDAV (iCloud, Fastmail, Nextcloud, etc.)"
echo "  3) Skip calendar sync"
echo ""
ask "Choose (1-3, default 1):"

case "${REPLY:-1}" in
    2) CALENDAR_PROVIDER="caldav" ;;
    3) CALENDAR_PROVIDER="none" ;;
    *) CALENDAR_PROVIDER="google" ;;
esac

# ── 2. vdirsyncer + khal for calendar ──────────────────────────────

if [ "$CALENDAR_PROVIDER" != "none" ]; then
    "$HOME/.openclaw/venv/bin/pip" install vdirsyncer khal icalendar 2>/dev/null

    if [ ! -f ~/.config/vdirsyncer/config ]; then
        mkdir -p ~/.config/vdirsyncer
        mkdir -p ~/.local/share/vdirsyncer/{status,calendars}

        if [ "$CALENDAR_PROVIDER" = "google" ]; then
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
                log "vdirsyncer config created with Google Calendar OAuth"
            else
                warn "vdirsyncer config created but missing OAuth credentials"
            fi

        elif [ "$CALENDAR_PROVIDER" = "caldav" ]; then
            echo ""
            echo "Common CalDAV URLs:"
            echo "  iCloud:     https://caldav.icloud.com/"
            echo "  Fastmail:   https://caldav.fastmail.com/"
            echo "  Nextcloud:  https://YOUR_SERVER/remote.php/dav/"
            echo ""
            ask "CalDAV server URL:"
            CALDAV_URL="$REPLY"

            ask "CalDAV username:"
            CALDAV_USER="$REPLY"

            ask "CalDAV password (stored in .env, not in config files):"
            if [ -n "$REPLY" ]; then
                if grep -q "^CALDAV_PASSWORD=" "$WORKSPACE/.env" 2>/dev/null; then
                    sed -i "s|^CALDAV_PASSWORD=.*|CALDAV_PASSWORD=$REPLY|" "$WORKSPACE/.env" 2>/dev/null || \
                        sed -i '' "s|^CALDAV_PASSWORD=.*|CALDAV_PASSWORD=$REPLY|" "$WORKSPACE/.env"
                else
                    echo "CALDAV_PASSWORD=$REPLY" >> "$WORKSPACE/.env"
                fi
                chmod 600 "$WORKSPACE/.env"
            fi

            # Write password file for vdirsyncer to read
            CALDAV_PASS_FILE=~/.config/vdirsyncer/caldav_password
            echo "$REPLY" > "$CALDAV_PASS_FILE"
            chmod 600 "$CALDAV_PASS_FILE"

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
type = "caldav"
url = "$CALDAV_URL"
username = "$CALDAV_USER"
password.fetch = ["command", "cat", "$CALDAV_PASS_FILE"]
VDIREOF

            log "vdirsyncer config created for CalDAV ($CALDAV_URL)"
        fi

        warn "Run these to finish calendar setup:"
        warn "  vdirsyncer discover calendars"
        warn "  vdirsyncer sync"
    else
        log "vdirsyncer already configured"
    fi
else
    warn "Calendar sync disabled."
fi

log "Email & calendar tools ready"
