#!/bin/bash
# Install OpenClaw

if ! command -v openclaw &>/dev/null; then
    log "Installing OpenClaw..."
    npm install -g openclaw
else
    log "OpenClaw already installed ($(openclaw --version 2>/dev/null || echo 'unknown version'))"
fi

# Initialize if needed
if [ ! -d "$HOME/.openclaw" ]; then
    log "Initializing OpenClaw..."
    openclaw init
else
    log "OpenClaw directory exists"
fi
