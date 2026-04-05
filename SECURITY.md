# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| latest  | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability, **please do not open a public issue.**

Instead, report it privately:

1. Go to the [Security Advisories](https://github.com/UnbrandedTech/Openclaw-template/security/advisories) page
2. Click **"Report a vulnerability"**
3. Provide a description, steps to reproduce, and potential impact

You can expect:
- **Acknowledgment** within 48 hours
- **Status update** within 7 days
- **Fix or mitigation** as soon as practical, depending on severity

## Scope

This project handles sensitive integrations (Slack, Gmail, Google Calendar, Linear). Security issues of particular concern include:

- Credential exposure (API keys, tokens, OAuth secrets)
- Unauthorized access to synced data (messages, emails, calendar events)
- Command injection in setup scripts or sync scripts
- Insecure file permissions on credential or state files
- Data leakage through log files or cached data

## Best Practices for Users

- Store all credentials in `.env` files (gitignored by default)
- Use `~/.openclaw/workspace/TOOLS.md` for API keys — never hardcode them
- Review cron job permissions and sync script access
- Keep dependencies updated (`pip install --upgrade -r requirements.txt`)
- Use application-default credentials for GCP (`gcloud auth application-default login`)
