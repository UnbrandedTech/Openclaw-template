# Contributing to OpenClaw Setup

Thanks for your interest in contributing! This guide will help you get started.

## How to Contribute

### Reporting Bugs

- Check [existing issues](https://github.com/UnbrandedTech/Openclaw-template/issues) first to avoid duplicates
- Use the **Bug Report** issue template
- Include your macOS version, Python version, and Node.js version
- Include relevant logs or error messages

### Suggesting Features

- Use the **Feature Request** issue template
- Explain the use case, not just the solution
- Be open to discussion — there may be alternative approaches

### Submitting Code

1. **Fork** the repository
2. **Create a branch** from `main` (`git checkout -b feature/your-feature`)
3. **Make your changes** — keep commits focused and atomic
4. **Test your changes** — run `setup.sh --dry-run` to verify nothing breaks
5. **Open a Pull Request** against `main`

### Pull Request Guidelines

- Fill out the PR template completely
- Keep PRs focused — one feature or fix per PR
- Update documentation if your change affects setup steps or configuration
- Add yourself to the contributors list if you'd like

## Development Setup

```bash
# Clone your fork
git clone git@github.com:YOUR_USERNAME/Openclaw-template.git
cd Openclaw-template

# Create a Python venv for testing sync scripts
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run setup in dry-run mode to validate
./setup.sh --dry-run
```

## Code Style

- **Shell scripts**: Use `set -e`, check before installing (idempotent), use functions for logical groupings
- **Python scripts**: Follow PEP 8, use type hints where practical, import from `shared.py` for common utilities
- **Config changes**: Update `config.py` — don't scatter constants across scripts

## Security

- **Never** commit credentials, tokens, or secrets
- Use `.env` files for sensitive values (these are gitignored)
- See [SECURITY.md](SECURITY.md) for reporting vulnerabilities

## Questions?

Open a [Discussion](https://github.com/UnbrandedTech/Openclaw-template/discussions) or file an issue.
