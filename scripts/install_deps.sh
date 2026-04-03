#!/bin/bash
# Install prerequisites on macOS

# Homebrew
if ! command -v brew &>/dev/null; then
    log "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    eval "$(/opt/homebrew/bin/brew shellenv)"
    echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
else
    log "Homebrew already installed"
fi

# Node.js via nvm
if ! command -v node &>/dev/null || [ "$(node -v | cut -d. -f1 | tr -d v)" -lt 22 ]; then
    log "Installing Node.js 22 via nvm..."
    if ! command -v nvm &>/dev/null; then
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
        export NVM_DIR="$HOME/.nvm"
        [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
    fi
    nvm install 22
    nvm use 22
    nvm alias default 22
else
    log "Node.js $(node -v) already installed"
fi

# Python 3.12+
if ! command -v python3 &>/dev/null || [ "$(python3 -c 'import sys; print(sys.version_info.minor)')" -lt 12 ]; then
    log "Installing Python 3.12..."
    brew install python@3.12
else
    log "Python $(python3 --version) already installed"
fi

# GitHub CLI
if ! command -v gh &>/dev/null; then
    log "Installing GitHub CLI..."
    brew install gh
    warn "Run 'gh auth login' after setup to authenticate"
else
    log "GitHub CLI already installed"
fi

# jq (useful for JSON processing)
if ! command -v jq &>/dev/null; then
    brew install jq
    log "Installed jq"
fi

log "All dependencies ready"
