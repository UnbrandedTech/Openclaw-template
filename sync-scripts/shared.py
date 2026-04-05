"""
shared.py — Shared utilities and constants for sync scripts.

All sync scripts should import from here instead of defining their own
copies of path constants, JSON helpers, sanitization, and Honcho setup.
"""

import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
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


# ── .env loading ──────────────────────────────────────────────────────────────

_env_file = WORKSPACE / ".env"
if _env_file.exists():
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _val = _line.split("=", 1)
                os.environ.setdefault(_key.strip(), _val.strip())


# ── LLM calling ──────────────────────────────────────────────────────────────

OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"


def call_llm(prompt: str, role: str = "fast", max_tokens: int = 4096) -> str:
    """Call an LLM using the configured provider for the given role.

    Roles map to models in openclaw.json:
      "models": {"fast": "vertex/gemini-2.5-flash", "reasoning": "anthropic/claude-sonnet-4-6"}

    Model spec format: "provider/model-name"
    Supported providers: vertex, openai, anthropic, ollama, bedrock
    """
    config = load_json(OPENCLAW_CONFIG)
    models = config.get("models", {})
    model_spec = models.get(role)

    if not model_spec:
        print(f"ERROR: No model configured for role '{role}' in openclaw.json", file=sys.stderr)
        print(f"  Expected: models.{role} = 'provider/model-name'", file=sys.stderr)
        sys.exit(1)

    if "/" not in model_spec:
        print(f"ERROR: Invalid model spec '{model_spec}' — expected 'provider/model-name'", file=sys.stderr)
        sys.exit(1)

    provider, model_name = model_spec.split("/", 1)
    auth_profiles = config.get("auth", {}).get("profiles", {})

    dispatch = {
        "vertex": _call_vertex,
        "openai": _call_openai,
        "anthropic": _call_anthropic,
        "ollama": _call_ollama,
        "bedrock": _call_bedrock,
    }

    handler = dispatch.get(provider)
    if not handler:
        print(f"ERROR: Unknown provider '{provider}' in model spec '{model_spec}'", file=sys.stderr)
        print(f"  Supported: {', '.join(dispatch)}", file=sys.stderr)
        sys.exit(1)

    return handler(auth_profiles, model_name, prompt, max_tokens)


def _get_vertex_token() -> str:
    """Get a GCP access token via gcloud."""
    result = subprocess.run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        capture_output=True, text=True,
    )
    token = result.stdout.strip()
    if not token:
        print("ERROR: Could not get gcloud access token.", file=sys.stderr)
        print("  Run: gcloud auth application-default login", file=sys.stderr)
        sys.exit(1)
    return token


def _call_vertex(profiles: dict, model_name: str, prompt: str, max_tokens: int) -> str:
    """Call a model via Vertex AI (handles both Google and Anthropic models)."""
    profile = profiles.get("vertex:default", {})
    project = profile.get("project_id", "")
    region = profile.get("region", "")

    if not project or not region:
        print("ERROR: Vertex AI project_id or region not configured in openclaw.json", file=sys.stderr)
        print("  Expected: auth.profiles['vertex:default'].{project_id, region}", file=sys.stderr)
        sys.exit(1)

    token = _get_vertex_token()

    if model_name.startswith("claude"):
        url = (
            f"https://{region}-aiplatform.googleapis.com/v1/projects/{project}"
            f"/locations/{region}/publishers/anthropic/models/{model_name}:rawPredict"
        )
        body = json.dumps({
            "anthropic_version": "vertex-2023-10-16",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
    else:
        url = (
            f"https://{region}-aiplatform.googleapis.com/v1/projects/{project}"
            f"/locations/{region}/publishers/google/models/{model_name}:generateContent"
        )
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }).encode()

    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
        if model_name.startswith("claude"):
            return result["content"][0]["text"]
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        print(f"ERROR: Vertex AI returned HTTP {e.code}: {body_text[:500]}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Vertex AI call failed: {e}", file=sys.stderr)
        sys.exit(1)


def _call_openai(profiles: dict, model_name: str, prompt: str, max_tokens: int) -> str:
    """Call a model via OpenAI API (also works with compatible APIs)."""
    profile = profiles.get("openai:default", {})
    api_key_env = profile.get("api_key_env", "OPENAI_API_KEY")
    api_key = os.environ.get(api_key_env, "")
    base_url = profile.get("base_url", "https://api.openai.com/v1")

    if not api_key:
        print(f"ERROR: {api_key_env} not set.", file=sys.stderr)
        sys.exit(1)

    url = f"{base_url}/chat/completions"
    body = json.dumps({
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }).encode()

    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
        return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        print(f"ERROR: OpenAI API returned HTTP {e.code}: {body_text[:500]}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: OpenAI API call failed: {e}", file=sys.stderr)
        sys.exit(1)


def _call_anthropic(profiles: dict, model_name: str, prompt: str, max_tokens: int) -> str:
    """Call a model via Anthropic API."""
    profile = profiles.get("anthropic:default", {})
    api_key_env = profile.get("api_key_env", "ANTHROPIC_API_KEY")
    api_key = os.environ.get(api_key_env, "")

    if not api_key:
        print(f"ERROR: {api_key_env} not set.", file=sys.stderr)
        sys.exit(1)

    url = "https://api.anthropic.com/v1/messages"
    body = json.dumps({
        "model": model_name,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(url, data=body, headers={
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    })

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
        return result["content"][0]["text"]
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        print(f"ERROR: Anthropic API returned HTTP {e.code}: {body_text[:500]}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Anthropic API call failed: {e}", file=sys.stderr)
        sys.exit(1)


def _call_ollama(profiles: dict, model_name: str, prompt: str, max_tokens: int) -> str:
    """Call a model via Ollama local API."""
    profile = profiles.get("ollama:default", {})
    base_url = profile.get("base_url", "http://localhost:11434")

    url = f"{base_url}/api/chat"
    body = json.dumps({
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"num_predict": max_tokens},
    }).encode()

    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
    })

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read())
        return result["message"]["content"]
    except Exception as e:
        print(f"ERROR: Ollama call failed: {e}", file=sys.stderr)
        print("  Is Ollama running? Start with: ollama serve", file=sys.stderr)
        sys.exit(1)


def _call_bedrock(profiles: dict, model_name: str, prompt: str, max_tokens: int) -> str:
    """Call a model via AWS Bedrock using the AWS CLI."""
    profile = profiles.get("bedrock:default", {})
    region = profile.get("region", "us-east-1")

    if "anthropic" in model_name or "claude" in model_name:
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        })
    else:
        body = json.dumps({
            "inputText": prompt,
            "textGenerationConfig": {"maxTokenCount": max_tokens},
        })

    fd, tmp_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        result = subprocess.run(
            [
                "aws", "bedrock-runtime", "invoke-model",
                "--model-id", model_name,
                "--body", body,
                "--region", region,
                "--content-type", "application/json",
                "--accept", "application/json",
                tmp_path,
            ],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"ERROR: Bedrock call failed: {result.stderr[:500]}", file=sys.stderr)
            sys.exit(1)

        with open(tmp_path) as f:
            response = json.load(f)

        if "anthropic" in model_name or "claude" in model_name:
            return response["content"][0]["text"]
        return response.get("results", [{}])[0].get("outputText", "")
    except subprocess.TimeoutExpired:
        print("ERROR: Bedrock call timed out", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Bedrock call failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


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
