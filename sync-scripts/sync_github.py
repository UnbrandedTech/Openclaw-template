#!/usr/bin/env python3
"""
sync_github.py — Optionally pull GitHub activity via the `gh` CLI.

Discovers repos the authenticated user contributed to recently, fetches
authored and review-requested PRs, builds a collaborator map, and writes
the result to:
  ~/.openclaw/workspace/github_activity.json

Usage:
  python3 sync_github.py [--days 90] [--dry-run]
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta

from shared import WORKSPACE, save_json, script_lock

OUTPUT_PATH = WORKSPACE / "github_activity.json"


# ── Helpers ──────────────────────────────────────────────────────────────────


def gh_authenticated() -> bool:
    """Return True if the gh CLI is installed and authenticated."""
    result = subprocess.run(
        ["gh", "auth", "status"], capture_output=True
    )
    return result.returncode == 0


def gh_json(args: list[str]) -> list | dict:
    """Run a gh command that returns JSON and parse the output.

    Returns an empty list on failure.
    """
    result = subprocess.run(
        ["gh"] + args,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return []


def parse_iso(date_str: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp string into a timezone-aware datetime."""
    if not date_str:
        return None
    try:
        # Handle trailing Z (GitHub style)
        cleaned = date_str.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None


# ── Repo discovery ───────────────────────────────────────────────────────────


def discover_repos(days: int) -> list[dict]:
    """List repos the user owns/contributed to that were pushed within *days*."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    repos = gh_json([
        "repo", "list",
        "--json", "name,owner,pushedAt",
        "--limit", "50",
    ])
    if not isinstance(repos, list):
        return []

    recent = []
    for repo in repos:
        pushed_at = parse_iso(repo.get("pushedAt"))
        if pushed_at and pushed_at >= cutoff:
            owner = repo.get("owner", {}).get("login", "")
            name = repo.get("name", "")
            if owner and name:
                recent.append({"owner": owner, "name": name, "full_name": f"{owner}/{name}"})
    return recent


# ── PR fetching ──────────────────────────────────────────────────────────────


def fetch_authored_prs(repo_full_name: str) -> list[dict]:
    """Fetch PRs authored by the authenticated user in *repo_full_name*."""
    return gh_json([
        "pr", "list",
        "--repo", repo_full_name,
        "--author", "@me",
        "--state", "all",
        "--json", "number,title,state,reviewDecision,url,createdAt,mergedAt",
        "--limit", "50",
    ])


def fetch_review_requested_prs(repo_full_name: str) -> list[dict]:
    """Fetch PRs where the user was requested as a reviewer."""
    return gh_json([
        "pr", "list",
        "--repo", repo_full_name,
        "--search", "review-requested:@me",
        "--state", "all",
        "--json", "number,title,state,url,createdAt",
        "--limit", "20",
    ])


# ── Collaborator map ────────────────────────────────────────────────────────


def build_collaborator_map(repos_data: dict) -> dict:
    """Build a collaborators dict from PR participants.

    For each authored PR, any reviewers mentioned in the PR metadata count
    toward review_count.  For review-requested PRs, the PR author counts
    toward pr_count.  This gives a rough picture of who you work with.
    """
    collabs: dict[str, dict[str, int]] = {}

    def _ensure(username: str) -> None:
        if username and username not in collabs:
            collabs[username] = {"pr_count": 0, "review_count": 0}

    for _repo_name, repo_info in repos_data.items():
        # Authored PRs — count reviewers
        for pr in repo_info.get("authored_prs", []):
            # reviewDecision is a string, not a list of reviewers.
            # gh pr list doesn't return individual reviewer usernames in the
            # default fields, but we still record the PR exists.  If the PR
            # JSON happens to contain reviewer info (via extended queries),
            # we would count it here.
            pass

        # Review-requested PRs — attribute authorship to collaborators
        for pr in repo_info.get("review_requested_prs", []):
            # The author of a PR you reviewed is a collaborator.
            author = ""
            if isinstance(pr.get("author"), dict):
                author = pr["author"].get("login", "")
            elif isinstance(pr.get("author"), str):
                author = pr["author"]
            if author:
                _ensure(author)
                collabs[author]["pr_count"] += 1

    return collabs


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync GitHub activity via gh CLI.")
    parser.add_argument("--days", type=int, default=90, help="Look-back window in days (default: 90)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be synced without writing")
    args = parser.parse_args()

    # Gate on authentication
    if not gh_authenticated():
        print("GitHub CLI not authenticated. Skipping.")
        sys.exit(0)

    with script_lock("sync_github"):
        print(f"Discovering repos pushed within the last {args.days} days...")
        repos = discover_repos(args.days)
        if not repos:
            print("No recently-pushed repos found.")

        repos_data: dict[str, dict] = {}
        total_prs = 0

        for repo in repos:
            full = repo["full_name"]
            print(f"  Fetching PRs for {full}...")

            authored = fetch_authored_prs(full)
            review_requested = fetch_review_requested_prs(full)

            if not isinstance(authored, list):
                authored = []
            if not isinstance(review_requested, list):
                review_requested = []

            repos_data[full] = {
                "authored_prs": authored,
                "review_requested_prs": review_requested,
            }
            total_prs += len(authored) + len(review_requested)

        collaborators = build_collaborator_map(repos_data)

        output = {
            "repos": repos_data,
            "collaborators": collaborators,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }

        if args.dry_run:
            print("\n[dry-run] Would write to:", OUTPUT_PATH)
            print(json.dumps(output, indent=2)[:2000])
        else:
            save_json(OUTPUT_PATH, output)
            print(f"\nWrote {OUTPUT_PATH}")

        # Summary
        print(
            f"\nSummary: {len(repos_data)} repos, "
            f"{total_prs} PRs, "
            f"{len(collaborators)} collaborators"
        )


if __name__ == "__main__":
    main()
