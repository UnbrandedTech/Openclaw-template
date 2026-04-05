"""
shared.py — Shared utilities and constants for sync scripts.

All sync scripts should import from here instead of defining their own
copies of path constants, JSON helpers, sanitization, and Honcho setup.
"""

import fcntl
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

# ── Path constants ──────────────────────────────────────────────────────────

WORKSPACE = Path.home() / ".openclaw" / "workspace"
MESSAGES_DIR = WORKSPACE / "slack_messages"
TRANSCRIPTS_DIR = WORKSPACE / "transcriptions"
CALENDAR_EVENTS_FILE = WORKSPACE / "calendar_events.json"
CALENDAR_ATTENDEES_FILE = WORKSPACE / "calendar_attendees.json"
GITHUB_ACTIVITY_FILE = WORKSPACE / "github_activity.json"
VAULT_PATH = Path(os.environ.get("OBSIDIAN_VAULT", str(Path.home() / "Documents" / "Obsidian Vault")))
PEOPLE_DIR = VAULT_PATH / "People"
CLIENTS_DIR = VAULT_PATH / "Clients"
VDIRSYNCER_CALENDARS = Path.home() / ".local" / "share" / "vdirsyncer" / "calendars"

# ── Honcho constants ────────────────────────────────────────────────────────

HONCHO_BASE_URL = os.environ.get("HONCHO_BASE_URL", "http://localhost:18790")
HONCHO_WORKSPACE = os.environ.get("HONCHO_WORKSPACE", "openclaw")


def get_honcho(base_url=None, workspace=None):
    """Create a Honcho client with sensible defaults."""
    try:
        from honcho import Honcho
    except ImportError:
        print("ERROR: honcho-ai not installed. Run: pip3 install honcho-ai", file=sys.stderr)
        sys.exit(1)
    return Honcho(
        base_url=base_url or HONCHO_BASE_URL,
        workspace_id=workspace or HONCHO_WORKSPACE,
    )


# ── JSON helpers (atomic writes) ────────────────────────────────────────────

def load_json(path: Path) -> dict:
    """Load a JSON file, returning {} if missing or corrupt."""
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_json(path: Path, data: dict):
    """Atomically write a JSON file via temp file + os.replace()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, content: str):
    """Atomically write a text file via temp file + os.replace()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── ID sanitization ────────────────────────────────────────────────────────

def sanitize_id(name: str) -> str:
    """Convert a name/ID to a valid Honcho-compatible ID.

    Lowercases, replaces non-alphanumeric chars with dashes, collapses runs,
    and strips leading/trailing dashes.
    """
    pid = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-").lower()
    pid = re.sub(r"-{2,}", "-", pid)
    return pid or "unknown"


# ── User identity (loaded from user.json, written by setup.sh) ──────────────

_user_config_path = WORKSPACE / "user.json"
_user_config = {}
if _user_config_path.exists():
    try:
        with open(_user_config_path) as _f:
            _user_config = json.load(_f)
    except (json.JSONDecodeError, OSError):
        pass

USER_NAME = _user_config.get("name", os.environ.get("OPENCLAW_USER_NAME", ""))
USER_FIRST_NAME = _user_config.get("first_name", "")
USER_EMAIL = _user_config.get("email", "")
USER_SLACK_ID = _user_config.get("slack_user_id", os.environ.get("SLACK_USER_ID", ""))
USER_NAME_KEYWORDS = _user_config.get("name_keywords", [])
USER_TITLE = _user_config.get("title", "")
USER_COMPANY = _user_config.get("company", "")
USER_PEER_ID = sanitize_id(USER_NAME) if USER_NAME else "owner"


# ── Script locking (prevents cron overlap) ──────────────────────────────────

LOCKS_DIR = WORKSPACE / ".locks"


@contextmanager
def script_lock(script_name: str):
    """Context manager that holds an exclusive flock for the script's duration.

    If another instance is already running, prints a message and exits.
    The OS releases the lock automatically if the process crashes.
    """
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = LOCKS_DIR / f"{script_name}.lock"
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"[{script_name}] Another instance is already running. Exiting.")
        lock_fd.close()
        sys.exit(0)
    try:
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
