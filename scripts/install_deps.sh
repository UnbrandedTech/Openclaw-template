#!/bin/bash
# Install prerequisites on macOS

# Xcode command line tools (needed for compiling some dependencies)
if ! xcode-select -p &>/dev/null; then
    log "Installing Xcode command line tools..."
    xcode-select --install
    echo "Waiting for Xcode CLI tools to finish installing..."
    echo "Press Enter when the installation is complete."
    read -r
else
    log "Xcode CLI tools already installed"
fi

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

# Google Cloud SDK (for Vertex AI auth)
if ! command -v gcloud &>/dev/null; then
    log "Installing Google Cloud SDK..."
    brew install --cask google-cloud-sdk
    log "Google Cloud SDK installed"
    warn "Run 'gcloud auth application-default login' after setup"
else
    log "Google Cloud SDK already installed"
fi

# Python venv for sync scripts
VENV="$HOME/.openclaw/venv"
if [ ! -d "$VENV" ]; then
    log "Creating Python venv at $VENV..."
    python3 -m venv "$VENV"
    log "Python venv created"
else
    log "Python venv already exists"
fi

log "All dependencies ready"
