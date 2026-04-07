#!/bin/bash
# Set up Slack integration

echo ""
echo "OpenClaw needs a Slack App to read messages and send briefings."
echo ""
echo "There are two parts:"
echo "  1. The APP (shared) — one per workspace, created by an admin"
echo "  2. Your USER TOKEN (personal) — one per person using OpenClaw"
echo ""
echo "═══ If your team already has an OpenClaw Slack app ═══"
echo ""
echo "  Ask your admin for the Bot token (xoxb-) and App token (xapp-)."
echo "  Then generate your own User token:"
echo "    1. Go to https://api.slack.com/apps > select the app"
echo "    2. OAuth & Permissions > scroll to User Token Scopes"
echo "    3. Add scopes: channels:history, groups:history, im:history,"
echo "       mpim:history, channels:read, groups:read, im:read,"
echo "       mpim:read, users:read"
echo "    4. Reinstall the app to your workspace"
echo "    5. Copy the User OAuth Token (xoxp-...)"
echo ""
echo "═══ If you need to create a new Slack app ═══"
echo ""
echo "  1. Go to https://api.slack.com/apps > Create New App > From scratch"
echo "  2. Enable Socket Mode (Settings > Socket Mode) — copy the App token (xapp-)"
echo "  3. Add Bot Token Scopes under OAuth & Permissions:"
echo "     chat:write, channels:history, groups:history,"
echo "     im:history, mpim:history, users:read, channels:read"
echo "  4. Add User Token Scopes (same page, scroll down):"
echo "     channels:history, groups:history, im:history, mpim:history,"
echo "     channels:read, groups:read, im:read, mpim:read, users:read"
echo "  5. Install to your workspace"
echo "  6. Copy Bot token (xoxb-) and User token (xoxp-) from the OAuth page"
echo ""

ask "Do you have your Slack tokens ready? (y/n)"
if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
    ask "Bot token (xoxb-..., shared across your team):"
    SLACK_BOT_TOKEN="$REPLY"

    ask "App token (xapp-..., shared across your team):"
    SLACK_APP_TOKEN="$REPLY"

    ask "Your personal User token (xoxp-..., unique to you):"
    SLACK_USER_TOKEN="$REPLY"

    # Validate token prefixes
    if [ -n "$SLACK_BOT_TOKEN" ] && [[ ! "$SLACK_BOT_TOKEN" == xoxb-* ]]; then
        warn "Bot token should start with xoxb- (got: ${SLACK_BOT_TOKEN:0:5}...)"
    fi
    if [ -n "$SLACK_APP_TOKEN" ] && [[ ! "$SLACK_APP_TOKEN" == xapp-* ]]; then
        warn "App token should start with xapp- (got: ${SLACK_APP_TOKEN:0:5}...)"
    fi
    if [ -n "$SLACK_USER_TOKEN" ] && [[ ! "$SLACK_USER_TOKEN" == xoxp-* ]]; then
        warn "User token should start with xoxp- (you may have pasted the bot token)"
        warn "The sync scripts need YOUR user token (xoxp-) to read messages as you."
        warn "Bot tokens (xoxb-) can't read DMs or private channels you're in."
        ask "Continue anyway? (y/n)"
        if [ "$REPLY" != "y" ] && [ "$REPLY" != "Y" ]; then
            ask "User token (xoxp-...):"
            SLACK_USER_TOKEN="$REPLY"
        fi
    fi

    # Save tokens (keychain or .slack_env file)
    store_secret "SLACK_BOT_TOKEN" "$SLACK_BOT_TOKEN"
    store_secret "SLACK_APP_TOKEN" "$SLACK_APP_TOKEN"
    if [ -n "$SLACK_USER_TOKEN" ]; then
        store_secret "SLACK_USER_TOKEN" "$SLACK_USER_TOKEN"
    fi

    # Also write .slack_env for scripts that read it directly
    if [ "$USE_KEYCHAIN" != true ]; then
        cat > "$WORKSPACE/.slack_env" << EOF
SLACK_BOT_TOKEN=$SLACK_BOT_TOKEN
SLACK_APP_TOKEN=$SLACK_APP_TOKEN
SLACK_USER_TOKEN=$SLACK_USER_TOKEN
EOF
        chmod 600 "$WORKSPACE/.slack_env"
    fi
    log "Slack tokens saved${USE_KEYCHAIN:+ to keychain}"
else
    warn "Skipping Slack token setup. Add tokens later with: ./setup.sh --from 6"
fi

# Install SDK (+ certifi so urllib HTTPS calls work on macOS Python.org installs)
"$HOME/.openclaw/venv/bin/pip" install slack-sdk certifi
log "Slack SDK installed"
