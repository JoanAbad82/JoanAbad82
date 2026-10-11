#!/usr/bin/env python3
"""Evidence-only GitHub profile curator. No AI text generation, no external posting.

Reads public GitHub metadata, rewrites a bounded README section, and keeps
a deterministic JSON snapshot. No repository other than the profile is writable.
"""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

OWNER = "JoanAbad82"
API_BASE = "https://api.github.com"
START = "<!-- portfolio-pulse:start -->"
END = "<!-- portfolio-pulse:end -->"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9-]{1,39}$")
ISSUE_URL = re.compile(
    r"^https://github[.]com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(issues|pull)/([1-9][0-9]*)$"
)
MAX_READ = 1024 * 1024
SEARCH = {
    "accepted_upstream": "is:pr is:merged author:JoanAbad82 -user:JoanAbad82",
    "accepted_inbound": "is:pr is:merged user:JoanAbad82 -author:JoanAbad82",
    "external_inbound": "is:pr is:open user:JoanAbad82 -author:JoanAbad82",
    "contribution_tasks": 'is:issue is:open user:JoanAbad82 label:"good first issue" -linked:pr',
}
LIMITS = {"accepted_upstream": 3, "accepted_inbound": 3, "external_inbound": 3, "contribution_tasks": 4}


class CuratorError(RuntimeError):
    pass


def github_get(endpoint: str, token: str | None) -> dict[str, Any]:
    if not endpoint.startswith("/search/issues?") and not endpoint.startswith("/repos/"):
        raise CuratorError("GitHub endpoint is not allowlisted")
    req = urllib.request.Request(API_BASE + endpoint, headers={
        "User-Agent": "JoanAbad82-PortfolioPulse/1.0",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        **({"Authorization": "Bearer " + token} if token else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=22) as resp:
            if resp.status != 200:
                raise CuratorError("Unexpected GitHub response status")
            data = resp.read(MAX_READ + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        # Do not log URL query, response content or any auth information.
        raise CuratorError("GitHub API unavailable") from exc
    if len(data) > MAX_READ:
        raise CuratorError("GitHub API response too large")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise CuratorError("Invalid GitHub JSON") from exc
    if type(value) not in (dict, list):
        raise CuratorError("Unexpected GitHub response")
    return value


def search_items(query: str, token: str | None) -> list[dict[str, Any]]:
    url = "/search/issues?" + urllib.parse.urlencode({
        "q": query, "per_page": "100", "sort": "updated", "order": "desc"
    })
    obj = github_get(url, token)
    if obj.get("incomplete_results") is True:
        raise CuratorError("Search results are incomplete")
    items = obj.get("items")
    if type(items) is not list:
        raise CuratorError("Invalid search response")
    return [x for x in items if type(x) is dict]


def parse_issue(item: dict[str, Any], kind: str) -> dict[str, Any] | None:
    url = item.get("html_url")
    if type(url) is not str:
        return None
    match = ISSUE_URL.fullmatch(url)
    if not match:
        return None
    owner, repo, target, number = match.groups()
    if not REPO_RE.fullmatch(repo) or not LOGIN_RE.fullmatch(owner):
        return None
    if target != ("issues" if kind == "contribution_tasks" else "pull"):
        return None
    if not (isinstance(item.get("number"), int) and item["number"] == int(number)):
        return None
    if item.get("repository_url") != f"{API_BASE}/repos/{owner}/{repo}":
        return None
    author = item.get("user") or {}
    if type(author) is not dict:
        return None
    login = author.get("login")
    if type(login) is not str or not LOGIN_RE.fullmatch(login):
        return None
    if login.endswith("[bot]") or author.get("type") == "Bot":
        return None
    if kind == "accepted_upstream" and (owner.lower() == OWNER.lower() or login.lower() != OWNER.lower()):
        return None
    if kind in ("accepted_inbound", "external_inbound", "contribution_tasks") and owner.lower() != OWNER.lower():
        return None
    if kind in ("accepted_inbound", "external_inbound") and login.lower() == OWNER.lower():
        return None
    if kind in ("accepted_upstream", "accepted_inbound"):
        pull = item.get("pull_request")
        if item.get("state") != "closed" or type(pull) is not dict or not pull.get("merged_at"):
            return None
    if kind == "contribution_tasks" and item.get("state") != "open":
        return None
    if kind == "external_inbound" and item.get("state") != "open":
        return None
    title = item.get("title")
    if type(title) is not str or not title.strip():
        return None
    return {
        "repo": f"{owner}/{repo}",
        "number": int(number),
        "title": title.strip()[:110],
        "url": url,
        "author": login,
    }


def public_repo(full_name: str, token: str | None, cache: dict[str, bool]) -> bool:
    if full_name not in cache:
        owner, repo = full_name.split("/", 1)
        if not LOGIN_RE.fullmatch(owner) or not REPO_RE.fullmatch(repo):
            return False
        value = github_get(f"/repos/{owner}/{repo}", token)
        # Never publish private repository names, even when the local PAT
        # sees private organizations that a workflow token cannot see.
        cache[full_name] = value.get("private") is False and value.get("full_name", "").lower() == full_name.lower()
    return cache[full_name]


def collect(fetch=github_get, token: str | None = None) -> dict[str, Any]:
    # Inject fetch in tests. Runtime requests only use the allowlisted GET client.
    def search(query: str) -> list[dict[str, Any]]:
        url = "/search/issues?" + urllib.parse.urlencode({
            "q": query, "per_page": "100", "sort": "updated", "order": "desc"
        })
        obj = fetch(url, token)
        if type(obj) is not dict or obj.get("incomplete_results") is True or type(obj.get("items")) is not list:
            raise CuratorError("Incomplete or invalid GitHub search")
        return obj["items"]
    cache: dict[str, bool] = {}
    claimed: set[tuple[str, int]] = set()
    snapshot: dict[str, Any] = {"schema": "GITHUB_PORTFOLIO_PULSE_V1"}
    for kind, query in SEARCH.items():
        values: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in search(query):
            if type(item) is not dict:
                continue
            candidate = parse_issue(item, kind)
            if candidate is None or candidate["url"] in seen:
                continue
            fullname = candidate["repo"]
            if fullname not in cache:
                try:
                    owner, name = fullname.split("/", 1)
                    if not (LOGIN_RE.fullmatch(owner) and REPO_RE.fullmatch(name)):
                        continue
                    meta = fetch(f"/repos/{owner}/{name}", token)
                    if type(meta) is not dict:
                        raise CuratorError("Invalid repository metadata")
                    cache[fullname] = (meta.get("private") is False and str(meta.get("full_name", "")).lower() == fullname.lower())
                except (ValueError, AttributeError):
                    continue
            if not cache[fullname]:
                continue
            if kind == "contribution_tasks" and (fullname.lower(), candidate["number"]) in claimed:
                continue
            if kind == "external_inbound":
                # A submitted review is evidence of review activity, not merge approval.
                owner, name = fullname.split("/", 1)
                reviews = fetch(f"/repos/{owner}/{name}/pulls/{candidate['number']}/reviews?per_page=100", token)
                if type(reviews) is not list or len(reviews) >= 100:
                    raise CuratorError("Missing or potentially truncated PR reviews")
                reviewed = any(
                    type(review) is dict
                    and type(review.get("user")) is dict
                    and str(review["user"].get("login", "")).lower() == OWNER.lower()
                    and review.get("state") in ("COMMENTED", "APPROVED", "CHANGES_REQUESTED")
                    for review in reviews
                )
                candidate["review_status"] = "maintainer review recorded" if reviewed else "no maintainer review recorded"
            if kind == "external_inbound" and type(item.get("body")) is str:
                # PR bodies refer to the same repository. These are hints
                # for hiding claimed issues, not permission to change them.
                for ref in re.findall(
                    r"(?i)\b(?:addresses|closes|closed|fixes|fixed|resolves|resolved)\s+#([1-9][0-9]*)",
                    item["body"][:4096],
                ):
                    claimed.add((fullname.lower(), int(ref)))
            values.append(candidate)
            seen.add(candidate["url"])
            if len(values) == LIMITS[kind]:
                break
        snapshot[kind] = values
    return snapshot


def safe_md(value: str) -> str:
    # Escape HTML and Markdown metacharacters; no untrusted HTML or links.
    s = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", value)
    s = " ".join(s.split())[:110]
    s = html.escape(s, quote=False).replace("@", "＠")
    return re.sub(r"([\\\[\]\(\)\*\_\`|])", r"\\\1", s)


def render(snapshot: dict[str, Any]) -> str:
    rows = [
        START,
        "## Verified collaboration and contribution opportunities",
        "",
        "This section is updated automatically from **public GitHub records** when facts change.",
        "It does not represent visitor counts, paid promotion, or an endorsement of unreviewed pull requests.",
        "",
    ]
    groups = [
        ("accepted_upstream", "**Accepted upstream contributions**"),
        ("accepted_inbound", "**Merged community contributions**"),
        ("external_inbound", "**Open community pull requests**"),
        ("contribution_tasks", "**Starter tasks open to contributors**"),
    ]
    for key, title in groups:
        rows.append(title)
        rows.append("")
        items = snapshot.get(key, [])
        if not items:
            rows.append("- No verified public entries currently available.")
        for item in items:
            label = f"{safe_md(item['repo'])} #{item['number']}: {safe_md(item['title'])}"
            if key == "accepted_inbound":
                label += f" (by {safe_md(item['author'])}; **merged**)"
            if key == "external_inbound":
                label += f" (by {safe_md(item['author'])}; **{safe_md(item['review_status'])}**)"
            rows.append(f"- [{label}]({item['url']})")
        rows.append("")
    rows.extend([
        "Tasks may already have an active contributor. Check the issue discussion before starting.",
        END,
    ])
    return "\n".join(rows)


def insert_bounded_section(original: str, generated: str) -> str:
    if original.count(START) != original.count(END) or original.count(START) > 1:
        raise CuratorError("Missing or duplicate README boundary marker")
    if START in original:
        a = original.index(START)
        b = original.index(END) + len(END)
        if b < a:
            raise CuratorError("Invalid README marker order")
        return original[:a] + generated + original[b:]
    anchor = "## Selected work"
    if original.count(anchor) != 1:
        raise CuratorError("Cannot safely locate profile section insertion point")
    return original.replace(anchor, generated + "\n\n" + anchor, 1)


def run(readme: Path, snapshot_file: Path, token: str | None) -> tuple[bool, dict[str, Any]]:
    before = readme.read_text(encoding="utf-8")
    snapshot = collect(token=token)
    replacement = insert_bounded_section(before, render(snapshot))
    serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    existing = snapshot_file.read_text(encoding="utf-8") if snapshot_file.exists() else ""
    updated = before != replacement or existing != serialized
    if updated:
        readme.write_text(replacement, encoding="utf-8", newline="\n")
        snapshot_file.write_text(serialized, encoding="utf-8", newline="\n")
    return updated, snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only GitHub discovery -> bounded local README profile update")
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--snapshot", type=Path, default=Path("profile-pulse.json"))
    parser.add_argument("--dry-run", action="store_true", help="Never write files")
    args = parser.parse_args()
    try:
        if args.dry_run:
            content = args.readme.read_text(encoding="utf-8")
            snapshot = collect(token=os.environ.get("GH_TOKEN"))
            replacement = insert_bounded_section(content, render(snapshot))
            print(json.dumps({"changed": content != replacement, "counts": {
                k: len(snapshot[k]) for k in SEARCH}}, sort_keys=True))
            return 0
        updated, snapshot = run(args.readme, args.snapshot, os.environ.get("GH_TOKEN"))
        print(json.dumps({"changed": updated, "counts": {
            k: len(snapshot[k]) for k in SEARCH}}, sort_keys=True))
        return 0
    except (CuratorError, OSError, ValueError) as exc:
        print("Portfolio Pulse aborted safely: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
