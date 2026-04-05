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
if [ "$PLATFORM" = "macos" ]; then
    if ! ls /Applications/Obsidian.app &>/dev/null 2>&1; then
        ask "Install Obsidian app? (y/n)"
        if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
            brew install --cask obsidian
            log "Obsidian installed"
        fi
    else
        log "Obsidian already installed"
    fi
elif [ "$PLATFORM" = "linux" ]; then
    if ! command -v obsidian &>/dev/null && ! flatpak list 2>/dev/null | grep -q obsidian; then
        ask "Install Obsidian app? (y/n)"
        if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
            if command -v snap &>/dev/null; then
                sudo snap install obsidian --classic
                log "Obsidian installed via snap"
            elif command -v flatpak &>/dev/null; then
                flatpak install -y flathub md.obsidian.Obsidian
                log "Obsidian installed via flatpak"
            else
                warn "Install Obsidian manually: https://obsidian.md/download"
            fi
        fi
    else
        log "Obsidian already installed"
    fi
fi
