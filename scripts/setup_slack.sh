#!/bin/bash
# Set up Slack integration

echo "To connect Slack, you need a Slack App with Socket Mode."
echo ""
echo "If you don't have one yet:"
echo "  1. Go to https://api.slack.com/apps"
echo "  2. Create New App > From scratch"
echo "  3. Enable Socket Mode (Settings > Socket Mode)"
echo "  4. Add Bot Token Scopes under OAuth & Permissions:"
echo "     chat:write, channels:history, groups:history,"
echo "     im:history, mpim:history, users:read, channels:read"
echo "  5. (Optional) Add User Token Scopes for stealth reads:"
echo "     channels:history, groups:history, im:history, mpim:history,"
echo "     channels:read, groups:read, im:read, mpim:read, users:read"
echo "  6. Install to your workspace"
echo ""

ask "Do you have your Slack tokens ready? (y/n)"
if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
    ask "Bot token (xoxb-...):"
    SLACK_BOT_TOKEN="$REPLY"

    ask "App token (xapp-...):"
    SLACK_APP_TOKEN="$REPLY"

    ask "User token for sync scripts (xoxp-..., press Enter to skip):"
    SLACK_USER_TOKEN="$REPLY"

    # Save to a local env file for the sync scripts
    cat > "$WORKSPACE/.slack_env" << EOF
SLACK_BOT_TOKEN=$SLACK_BOT_TOKEN
SLACK_APP_TOKEN=$SLACK_APP_TOKEN
SLACK_USER_TOKEN=$SLACK_USER_TOKEN
EOF
    chmod 600 "$WORKSPACE/.slack_env"
    log "Slack tokens saved to $WORKSPACE/.slack_env"
    warn "You still need to add these to openclaw.json under the Slack plugin config"
else
    warn "Skipping Slack token setup. Add tokens to openclaw.json manually later."
fi

# Install SDK
"$HOME/.openclaw/venv/bin/pip" install slack-sdk
log "Slack SDK installed"
