#!/bin/bash
# Set up Honcho memory system

if [ "${HONCHO_SELF_HOSTED:-}" = "true" ]; then
    HONCHO_OPTION="2"
else
    wizard_choose "How would you like to run Honcho (the AI's memory system)?" \
        "Cloud Honcho (honcho.dev, easiest)" \
        "Self-hosted (PostgreSQL + Ollama, more control)"

    case "$REPLY" in
        *Self-hosted*|2) HONCHO_OPTION="2" ;;
        *)               HONCHO_OPTION="1" ;;
    esac
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

    # Run Honcho migrations if tables are missing (e.g., after uninstall)
    HONCHO_DIR="${HONCHO_PROJECT_DIR:-$HOME/Projects/Personal/honcho}"
    if [ -f "$HONCHO_DIR/alembic.ini" ]; then
        TABLES=$(psql -d honcho -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null | tr -d ' ')
        if [ "${TABLES:-0}" -lt 5 ]; then
            log "Running Honcho migrations..."
            (cd "$HONCHO_DIR" && "$HONCHO_DIR/.venv/bin/alembic" upgrade heads 2>&1) || warn "Alembic migrations had errors"
        fi
    fi

    # Start/restart Honcho server
    if ! curl -s http://localhost:18790/ &>/dev/null; then
        if [ -f "$HONCHO_DIR/src/main.py" ]; then
            log "Starting Honcho server..."
            (cd "$HONCHO_DIR" && "$HONCHO_DIR/.venv/bin/fastapi" run src/main.py --port 18790 --host 127.0.0.1 &>/dev/null &)
            sleep 3
            if curl -s http://localhost:18790/ &>/dev/null; then
                log "Honcho server started"
            else
                warn "Honcho server may not have started — check manually"
            fi
        fi
    else
        log "Honcho server already running"
    fi

    log "Self-hosted Honcho ready (localhost:18790)"
else
    warn "Cloud Honcho selected. Sign up at honcho.dev and add your API key to the config."
fi

# Install Python client
"$HOME/.openclaw/venv/bin/pip" install honcho-ai
log "Honcho Python client installed"

# Install OpenClaw Honcho plugin
if command -v openclaw &>/dev/null; then
    if ! openclaw plugins list 2>/dev/null | grep -q "openclaw-honcho"; then
        log "Installing OpenClaw Honcho plugin..."
        openclaw plugins install @honcho-ai/openclaw-honcho 2>/dev/null || warn "Could not install Honcho plugin (install manually: openclaw plugins install @honcho-ai/openclaw-honcho)"
    else
        log "OpenClaw Honcho plugin already installed"
    fi

    # Configure the plugin in openclaw.json
    if [ -f "$OPENCLAW_DIR/openclaw.json" ]; then
        HONCHO_URL="http://localhost:18790"
        if [ "$HONCHO_OPTION" = "1" ]; then
            HONCHO_URL="https://api.honcho.dev"
        fi

        python3 -c "
import json, os
config_path = '$OPENCLAW_DIR/openclaw.json'
with open(config_path) as f:
    config = json.load(f)

# Configure Honcho plugin
plugins = config.setdefault('plugins', {})
entries = plugins.setdefault('entries', {})
honcho = entries.setdefault('openclaw-honcho', {})
honcho_cfg = honcho.setdefault('config', {})
honcho_cfg['workspaceId'] = 'openclaw'
honcho_cfg['baseUrl'] = '$HONCHO_URL'

# Restore fields that plugin install may have wiped
config.setdefault('gateway', {})['mode'] = 'local'

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
print('  Configured Honcho plugin + restored gateway.mode')
" 2>/dev/null
    fi
else
    warn "OpenClaw not installed yet — Honcho plugin will be configured after OpenClaw install"
fi
