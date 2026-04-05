#!/bin/bash
# Set up Honcho memory system

echo "Honcho setup options:"
echo "  1. Cloud Honcho (honcho.dev, easiest)"
echo "  2. Self-hosted (PostgreSQL + Ollama, more control)"
echo ""
if [ "${HONCHO_SELF_HOSTED:-}" = "true" ]; then
    HONCHO_OPTION="2"
else
    HONCHO_OPTION="1"
    log "Using Cloud Honcho (default). Set HONCHO_SELF_HOSTED=true to self-host."
fi

if [ "$HONCHO_OPTION" = "2" ]; then
    # PostgreSQL
    if ! command -v psql &>/dev/null; then
        log "Installing PostgreSQL..."
        if [ "$PLATFORM" = "macos" ]; then
            brew install postgresql@16
            brew services start postgresql@16
        elif [ "$DISTRO" = "debian" ]; then
            sudo apt-get install -y postgresql postgresql-client
            sudo systemctl enable --now postgresql
        elif [ "$DISTRO" = "fedora" ]; then
            sudo dnf install -y postgresql-server postgresql
            sudo postgresql-setup --initdb 2>/dev/null || true
            sudo systemctl enable --now postgresql
        fi
        sleep 2
    fi

    # Create database
    if ! psql -lqt | cut -d \| -f 1 | grep -qw honcho; then
        createdb honcho
        psql honcho -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null || true
        log "Created honcho database with pgvector"
    else
        log "honcho database already exists"
    fi

    # Ollama for embeddings
    if ! command -v ollama &>/dev/null; then
        log "Installing Ollama..."
        if [ "$PLATFORM" = "macos" ]; then
            brew install ollama
        else
            curl -fsSL https://ollama.com/install.sh | sh
        fi
    fi

    # Start Ollama and pull embedding model
    if ! pgrep -x ollama &>/dev/null; then
        ollama serve &>/dev/null &
        sleep 3
    fi
    ollama pull nomic-embed-text 2>/dev/null
    log "Ollama ready with nomic-embed-text"

    # Set keepalive
    if ! grep -q "OLLAMA_KEEP_ALIVE" "$SHELL_RC" 2>/dev/null; then
        echo 'export OLLAMA_KEEP_ALIVE=24h' >> "$SHELL_RC"
    fi

    log "Self-hosted Honcho ready (localhost:18790)"
else
    warn "Cloud Honcho selected. Sign up at honcho.dev and add your API key to the config."
fi

# Install Python client
"$HOME/.openclaw/venv/bin/pip" install honcho-ai
log "Honcho Python client installed"
