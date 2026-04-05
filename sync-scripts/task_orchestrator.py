#!/usr/bin/env python3
"""
task_orchestrator.py — Jeff's Claude Code task orchestrator.

NOTE: This script handles state tracking only.
Actual Claude Code processes are launched via the OpenClaw exec tool
with background:true so they get proper process management.

Commands:
  start <MD-xxxx> [MD-yyyy ...]   Print the launch command for tickets
  status                           Show active/recent tasks
  register <ticket> <session_id>  Register a running session
  check-reviews                    Check open PRs for new review comments
  cleanup                          Remove stale completed tasks
"""

import json
import subprocess
import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

from shared import WORKSPACE, save_json, load_json, get_secret

# Repo map loaded from team.json if available, empty otherwise
_team = load_json(WORKSPACE / "team.json")
REPO_MAP = _team.get("repo_map", {})

STATE_FILE = WORKSPACE / "memory" / "active-tasks.json"
LINEAR_TOKEN = get_secret("LINEAR_API_KEY")
OPENCLAW_BIN = shutil.which("openclaw") or "openclaw"
CLAUDE_BIN = "claude"

def load_state():
    state = load_json(STATE_FILE)
    if "tasks" not in state:
        state["tasks"] = {}
    if "completed" not in state:
        state["completed"] = []
    return state


def save_state(state):
    save_json(STATE_FILE, state)


def get_repo(ticket_id):
    prefix = ticket_id.split("-")[0].upper()
    return REPO_MAP.get(prefix, "")


def get_linear_ticket(ticket_id):
    import urllib.request
    query = json.dumps({
        "query": """query($filter: IssueFilter) {
            issues(filter: $filter) {
                nodes { identifier title state { name } priority url }
            }
        }""",
        "variables": {"filter": {"identifier": {"eq": ticket_id}}}
    }).encode()
    try:
        req = urllib.request.Request(
            "https://api.linear.app/graphql",
            data=query,
            headers={"Authorization": LINEAR_TOKEN, "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            nodes = result.get("data", {}).get("issues", {}).get("nodes", [])
            if nodes:
                return nodes[0]
    except Exception as e:
        print(f"  Warning: Could not fetch Linear ticket: {e}")
    return {"identifier": ticket_id, "title": ticket_id, "url": ""}


def build_prompt(ticket_id, title):
    return (
        f"/start-task {ticket_id}\n\n"
        f"## Non-interactive defaults (apply without asking):\n"
        f"- Worktree already exists → Recreate (fresh from latest main)\n"
        f"- Ticket is Done/Cancelled → proceed anyway\n"
        f"- Any yes/no question → choose the most sensible default and proceed\n\n"
        f"## On completion:\n"
        f"When the PR is created, run:\n"
        f"{OPENCLAW_BIN} system event --text \"PR ready: {ticket_id} — {title}\" --mode now\n\n"
        f"If genuinely blocked and need human input, run:\n"
        f"{OPENCLAW_BIN} system event --text \"Needs input: {ticket_id} — [describe question]\" --mode now\n"
    )


def cmd_launch(args, state):
    """Print the exec command to launch a ticket — to be run by the agent via exec tool."""
    for ticket_id in [t.upper() for t in args.tickets]:
        ticket = get_linear_ticket(ticket_id)
        title = ticket.get("title", ticket_id)
        repo = get_repo(ticket_id)
        prompt = build_prompt(ticket_id, title)

        # Write prompt file
        prompt_path = WORKSPACE / "memory" / f"task-{ticket_id.lower()}.prompt"
        prompt_path.write_text(prompt)

        print(f"\n# {ticket_id}: {title}")
        print(f"# Repo: {repo}")
        print(f"# Prompt: {prompt_path}")
        home = str(Path.home())
        nvm_init = f"export NVM_DIR={home}/.nvm && source {home}/.nvm/nvm.sh && nvm use 20 --silent 2>/dev/null"
        print(f"\ncd {repo} && {nvm_init} && {CLAUDE_BIN} --permission-mode acceptEdits --print \"$(cat {prompt_path})\"")


def cmd_status(args, state):
    tasks = state.get("tasks", {})
    if not tasks:
        print("No active tasks.")
        return
    print(f"{'Ticket':<12} {'Status':<18} {'Session':<20} {'Title'}")
    print("-" * 80)
    for tid, task in sorted(tasks.items()):
        status = task.get("status", "unknown")
        session = task.get("session_id", "-")[:18]
        title = task.get("title", "")[:40]
        pr = f" PR#{task['pr_number']}" if task.get("pr_number") else ""
        print(f"{tid:<12} {status:<18} {session:<20} {title}{pr}")


def cmd_register(args, state):
    """Register a background session ID for a ticket."""
    ticket_id = args.ticket.upper()
    ticket = get_linear_ticket(ticket_id)
    state["tasks"][ticket_id] = {
        "ticket_id": ticket_id,
        "title": ticket.get("title", ticket_id),
        "url": ticket.get("url", ""),
        "repo": get_repo(ticket_id),
        "session_id": args.session_id,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pr_number": None,
        "pr_url": None,
        "last_review_check": None,
    }
    save_state(state)
    print(f"Registered {ticket_id} → session {args.session_id}")


def find_pr(ticket_id, repo):
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--search", ticket_id.lower(),
             "--json", "number,url,state"],
            cwd=repo, capture_output=True, text=True, timeout=15
        )
        for pr in json.loads(result.stdout or "[]"):
            if pr.get("state") == "OPEN":
                return str(pr["number"]), pr["url"]
    except Exception:
        pass
    return None, None


def cmd_check_reviews(args, state):
    tasks = state.get("tasks", {})
    triggered = 0
    for ticket_id, task in list(tasks.items()):
        repo = task.get("repo", "")

        # Find PR if not tracked yet
        if not task.get("pr_number"):
            pr_num, pr_url = find_pr(ticket_id, repo)
            if pr_num:
                task["pr_number"] = pr_num
                task["pr_url"] = pr_url
                task["status"] = "pr_open"
                print(f"  {ticket_id}: found PR #{pr_num}")

        if not task.get("pr_number"):
            continue

        pr_num = task["pr_number"]
        last_check = task.get("last_review_check", "")

        try:
            result = subprocess.run(
                ["gh", "repo", "view", "--json", "owner,name"],
                cwd=repo, capture_output=True, text=True, timeout=10
            )
            info = json.loads(result.stdout)
            slug = f"{info['owner']['login']}/{info['name']}"

            result = subprocess.run(
                ["gh", "api", f"repos/{slug}/pulls/{pr_num}/comments",
                 "--jq", "[.[] | select(.in_reply_to_id == null) | {body, user: .user.login, created_at}]"],
                capture_output=True, text=True, timeout=15
            )
            comments = json.loads(result.stdout or "[]")
            new = [c for c in comments
                   if c.get("user") not in ("github-actions[bot]", "dependabot[bot]", "linear[bot]")
                   and (not last_check or c.get("created_at", "") > last_check)]

            task["last_review_check"] = datetime.now(timezone.utc).isoformat()

            if new:
                print(f"  {ticket_id}: {len(new)} new review comment(s) — print address-review command")
                print(f"  cd {repo} && {CLAUDE_BIN} --permission-mode acceptEdits --print \"/address-review {pr_num}\\n\\nWhen done: {OPENCLAW_BIN} system event --text 'Review addressed: {ticket_id} PR#{pr_num}' --mode now\"")
                triggered += 1

        except Exception as e:
            print(f"  {ticket_id}: error checking PR — {e}")

    save_state(state)
    print(f"\nChecked {len(tasks)} task(s), {triggered} need address-review.")


def cmd_cleanup(args, state):
    tasks = state.get("tasks", {})
    to_remove = [tid for tid, t in tasks.items()
                 if t.get("status") not in ("pr_open", "addressing_review", "running")]
    for tid in to_remove:
        state.setdefault("completed", []).append({
            **state["tasks"].pop(tid),
            "completed_at": datetime.now(timezone.utc).isoformat()
        })
        print(f"  Cleaned up {tid}")
    save_state(state)
    print(f"Removed {len(to_remove)} completed task(s).")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("launch", help="Print launch command for tickets")
    p.add_argument("tickets", nargs="+")

    sub.add_parser("status")

    p = sub.add_parser("register", help="Register a running session for a ticket")
    p.add_argument("ticket")
    p.add_argument("session_id")

    sub.add_parser("check-reviews")
    sub.add_parser("cleanup")

    args = parser.parse_args()
    state = load_state()

    if args.command == "launch":
        cmd_launch(args, state)
    elif args.command == "status":
        cmd_status(args, state)
    elif args.command == "register":
        cmd_register(args, state)
    elif args.command == "check-reviews":
        cmd_check_reviews(args, state)
    elif args.command == "cleanup":
        cmd_cleanup(args, state)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
