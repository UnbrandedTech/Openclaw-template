#!/bin/bash
# Set up Obsidian vault structure

VAULT="${OBSIDIAN_VAULT:-$HOME/Documents/Obsidian Vault}"

mkdir -p "$VAULT/Daily Notes"
mkdir -p "$VAULT/People"
mkdir -p "$VAULT/Clients"
mkdir -p "$VAULT/Active Projects"
mkdir -p "$VAULT/Reference"
mkdir -p "$VAULT/Ideas"

log "Obsidian vault created at $VAULT"

# Install Obsidian (optional)
if ! ls /Applications/Obsidian.app &>/dev/null 2>&1; then
    ask "Install Obsidian app? (y/n)"
    if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
        brew install --cask obsidian
        log "Obsidian installed"
    fi
else
    log "Obsidian already installed"
fi
