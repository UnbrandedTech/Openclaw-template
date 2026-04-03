# TOOLS.md — Local Notes

## API Keys & Credentials

### Google Workspace (gogcli)
- Binary: `~/.local/bin/gog`
- Account: [YOUR EMAIL]
- Set env: `export PATH="$HOME/.local/bin:$PATH" && export GOG_ACCOUNT=[YOUR EMAIL]`

### Slack
- Bot token + app token in openclaw.json
- User token (read-only): in openclaw.json or .slack_env

### GitHub
- Auth'd via `gh` CLI
- Run `gh auth login` to set up

### Google Calendar
- CalDAV via vdirsyncer
- Calendars: `~/.local/share/vdirsyncer/calendars/`
- Query: `khal list today 7d`
- Sync: `vdirsyncer sync`

## Key Paths

- Obsidian vault: `~/Documents/Obsidian Vault/`
- Workspace scripts: `~/.openclaw/workspace/scripts/`
- Transcriptions: `~/.openclaw/workspace/transcriptions/`
- Memory files: `~/.openclaw/workspace/memory/`

---

*Add API keys, credentials, and tool-specific notes here as you set things up.*
