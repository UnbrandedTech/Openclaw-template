#!/bin/bash
# Install prerequisites (cross-platform: macOS + Linux)
# Uses $PLATFORM, $DISTRO, $SHELL_RC, $SHELL_PROFILE from setup.sh

# ── Build tools ─────────────────────────────────────────────────────
if [ "$PLATFORM" = "macos" ]; then
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
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> "$SHELL_PROFILE"
    else
        log "Homebrew already installed"
    fi
elif [ "$PLATFORM" = "linux" ]; then
    # Ensure basic build tools are available (needed for gogcli, nvm, etc.)
    if ! command -v make &>/dev/null || ! command -v git &>/dev/null || ! command -v curl &>/dev/null; then
        log "Installing build essentials..."
        if [ "$DISTRO" = "debian" ]; then
            sudo apt-get update -qq
            sudo apt-get install -y build-essential git curl
        elif [ "$DISTRO" = "fedora" ]; then
            sudo dnf install -y make gcc git curl
        elif [ "$DISTRO" = "arch" ]; then
            sudo pacman -S --noconfirm base-devel git curl
        fi
    else
        log "Build tools already installed"
    fi
fi

# ── Node.js via nvm ─────────────────────────────────────────────────
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

# ── Python 3.12+ ────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null || [ "$(python3 -c 'import sys; print(sys.version_info.minor)')" -lt 12 ]; then
    log "Installing Python 3.12..."
    if [ "$PLATFORM" = "macos" ]; then
        brew install python@3.12
    elif [ "$DISTRO" = "debian" ]; then
        sudo apt-get install -y python3 python3-venv python3-pip
    elif [ "$DISTRO" = "fedora" ]; then
        sudo dnf install -y python3 python3-pip
    elif [ "$DISTRO" = "arch" ]; then
        sudo pacman -S --noconfirm python python-pip
    fi
else
    log "Python $(python3 --version) already installed"
fi

# ── GitHub CLI ──────────────────────────────────────────────────────
if ! command -v gh &>/dev/null; then
    log "Installing GitHub CLI..."
    if [ "$PLATFORM" = "macos" ]; then
        brew install gh
    elif [ "$DISTRO" = "debian" ]; then
        (type -p wget >/dev/null || sudo apt-get install wget -y) \
            && sudo mkdir -p -m 755 /etc/apt/keyrings \
            && wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
            && sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
            && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
            && sudo apt-get update -qq \
            && sudo apt-get install -y gh
    elif [ "$DISTRO" = "fedora" ]; then
        sudo dnf install -y gh
    elif [ "$DISTRO" = "arch" ]; then
        sudo pacman -S --noconfirm github-cli
    fi
    warn "Run 'gh auth login' after setup to authenticate"
else
    log "GitHub CLI already installed"
fi

# ── jq ──────────────────────────────────────────────────────────────
if ! command -v jq &>/dev/null; then
    log "Installing jq..."
    pkg_install jq
fi

# ── Google Cloud SDK ────────────────────────────────────────────────
if ! command -v gcloud &>/dev/null; then
    log "Installing Google Cloud SDK..."
    if [ "$PLATFORM" = "macos" ]; then
        brew install --cask google-cloud-sdk
    elif [ "$DISTRO" = "debian" ]; then
        sudo apt-get install -y apt-transport-https ca-certificates gnupg curl
        curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg 2>/dev/null
        echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list > /dev/null
        sudo apt-get update -qq && sudo apt-get install -y google-cloud-cli
    elif [ "$DISTRO" = "fedora" ]; then
        sudo tee /etc/yum.repos.d/google-cloud-sdk.repo << 'GCSDK'
[google-cloud-cli]
name=Google Cloud CLI
baseurl=https://packages.cloud.google.com/yum/repos/cloud-sdk-el9-x86_64
enabled=1
gpgcheck=1
repo_gpgcheck=0
gpgkey=https://packages.cloud.google.com/yum/doc/rpm-package-key.gpg
GCSDK
        sudo dnf install -y google-cloud-cli
    else
        warn "Install Google Cloud SDK manually: https://cloud.google.com/sdk/docs/install"
    fi
    warn "Run 'gcloud auth application-default login' after setup"
else
    log "Google Cloud SDK already installed"
fi

# ── gum (TUI wizard) ───────────────────────────────────────────────
if ! command -v gum &>/dev/null; then
    log "Installing gum (interactive TUI)..."
    if [ "$PLATFORM" = "macos" ]; then
        brew install gum
    elif [ "$DISTRO" = "debian" ]; then
        sudo mkdir -p /etc/apt/keyrings
        curl -fsSL https://repo.charm.sh/apt/gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/charm.gpg 2>/dev/null
        echo "deb [signed-by=/etc/apt/keyrings/charm.gpg] https://repo.charm.sh/apt/ * *" | sudo tee /etc/apt/sources.list.d/charm.list > /dev/null
        sudo apt-get update -qq && sudo apt-get install -y gum
    elif [ "$DISTRO" = "fedora" ]; then
        echo '[charm]
name=Charm
baseurl=https://repo.charm.sh/yum/
enabled=1
gpgcheck=1
gpgkey=https://repo.charm.sh/yum/gpg.key' | sudo tee /etc/yum.repos.d/charm.repo > /dev/null
        sudo dnf install -y gum
    elif [ "$DISTRO" = "arch" ]; then
        sudo pacman -S --noconfirm gum
    fi
    if command -v gum &>/dev/null; then
        log "gum installed"
    else
        warn "gum not installed — wizard will use plain text mode"
    fi
else
    log "gum already installed"
fi

# ── Python venv ─────────────────────────────────────────────────────
VENV="$HOME/.openclaw/venv"
if [ ! -d "$VENV" ]; then
    # Ensure python3-venv is available on Debian/Ubuntu
    if [ "$DISTRO" = "debian" ] && ! python3 -m venv --help &>/dev/null 2>&1; then
        sudo apt-get install -y python3-venv
    fi
    log "Creating Python venv at $VENV..."
    mkdir -p "$HOME/.openclaw"
    python3 -m venv "$VENV"
    log "Python venv created"
else
    log "Python venv already exists"
fi

log "All dependencies ready"
