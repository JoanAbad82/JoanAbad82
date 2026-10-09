#!/usr/bin/env python3
"""Fail-closed drift guard for JoanAbad82 machine-readable agent surfaces."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

USER_AGENT = "JoanAbad82-AgentSurfaceDriftGuard/1.0"
SITE_REPO_RAW = "https://raw.githubusercontent.com/JoanAbad82/openutilitylab-site/main"
LIVE_BASE = "https://openutilitylab.com"


class DriftError(RuntimeError):
    pass


def fetch_text(url: str, timeout: int = 20) -> str:
    request_url = url
    if "raw.githubusercontent.com" in url or "openutilitylab.com" in url:
        separator = "&" if "?" in url else "?"
        request_url = f"{url}{separator}drift_guard={int(datetime.now(timezone.utc).timestamp())}"
    req = urllib.request.Request(
        request_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,text/markdown,*/*;q=0.5",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                raise DriftError(f"HTTP {getattr(resp, 'status', 'unknown')} for {url}")
            return resp.read().decode("utf-8-sig")
    except urllib.error.HTTPError as exc:
        raise DriftError(f"HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise DriftError(f"URL error for {url}: {exc.reason}") from exc


def fetch_json(url: str) -> dict[str, Any]:
    try:
        data = json.loads(fetch_text(url))
    except json.JSONDecodeError as exc:
        raise DriftError(f"Invalid JSON at {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise DriftError(f"Expected JSON object at {url}")
    return data


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise DriftError(f"Missing local file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DriftError(f"Invalid local JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise DriftError(f"Expected JSON object in {path}")
    return data


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_task_contract(
    task: dict[str, Any],
    expected_repo: str,
    expected_schema: str,
    errors: list[str],
    source: str,
) -> None:
    require(task.get("repository") == expected_repo,
            f"{source}: repository mismatch ({task.get('repository')!r} != {expected_repo!r})",
            errors)
    require(task.get("$schema") == expected_schema,
            f"{source}: $schema drift ({task.get('$schema')!r} != {expected_schema!r})",
            errors)
    require(isinstance(task.get("schema_version"), str),
            f"{source}: missing schema_version", errors)
    require(isinstance(task.get("purpose"), str) and bool(task.get("purpose", "").strip()),
            f"{source}: missing purpose", errors)

    caps = task.get("capabilities") or task.get("accepted_task_types")
    require(isinstance(caps, list) and len(caps) > 0,
            f"{source}: no capabilities/accepted_task_types", errors)

    contract = task.get("task_contract")
    require(isinstance(contract, dict), f"{source}: missing task_contract object", errors)
    if not isinstance(contract, dict):
        return

    require(contract.get("external_input_trust") == "UNTRUSTED_EXTERNAL_INPUT",
            f"{source}: external_input_trust must be UNTRUSTED_EXTERNAL_INPUT", errors)
    require(contract.get("production_write_access") is False,
            f"{source}: production_write_access must be false", errors)
    require(contract.get("automatic_promotion") is False,
            f"{source}: automatic_promotion must be false", errors)
    require(isinstance(contract.get("expected_artifact"), list) and len(contract["expected_artifact"]) > 0,
            f"{source}: expected_artifact missing/empty", errors)
    require(isinstance(contract.get("completion_condition"), str)
            and bool(contract.get("completion_condition", "").strip()),
            f"{source}: completion_condition missing", errors)


def normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-root", default=".", help="Local checkout of JoanAbad82 profile repo")
    parser.add_argument("--json-report", default="", help="Optional report path")
    args = parser.parse_args()

    root = Path(args.profile_root).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[str] = []

    projects = load_json(root / "projects.json")
    schema = load_json(root / "schemas" / "agent-task-contract.schema.json")
    task_index_schema = load_json(root / "schemas" / "agent-task-index.schema.json")
    schema_url = projects.get("agent_contract_schema")
    interaction_meta = projects.get("agent_interaction", {})
    task_index_url = interaction_meta.get("task_index_url")
    task_index_schema_url = interaction_meta.get("task_index_schema")

    require(isinstance(schema_url, str) and bool(schema_url),
            "projects.json: agent_contract_schema missing", errors)
    require(schema.get("$id") == schema_url,
            f"schema $id drift ({schema.get('$id')!r} != {schema_url!r})", errors)
    require(task_index_url == "https://openutilitylab.com/tasks.json",
            "projects.json: task_index_url drift", errors)
    require(task_index_schema_url == task_index_schema.get("$id"),
            "projects.json: task_index_schema does not match schema $id", errors)

    project_list = projects.get("projects")
    require(isinstance(project_list, list), "projects.json: projects must be an array", errors)
    if not isinstance(project_list, list):
        project_list = []

    project_map: dict[str, dict[str, Any]] = {}
    for project in project_list:
        if not isinstance(project, dict):
            errors.append("projects.json: non-object project entry")
            continue
        repo = project.get("repository")
        if not isinstance(repo, str):
            errors.append("projects.json: project missing repository")
            continue
        if repo in project_map:
            errors.append(f"projects.json: duplicate repository {repo}")
        project_map[repo] = project

    capability_discovery = projects.get("capability_discovery")
    require(isinstance(capability_discovery, dict),
            "projects.json: capability_discovery missing", errors)
    if isinstance(capability_discovery, dict):
        for capability, repos in capability_discovery.items():
            require(isinstance(repos, list) and len(repos) > 0,
                    f"projects.json: capability {capability!r} has no repositories", errors)
            if isinstance(repos, list):
                for repo in repos:
                    require(repo in project_map,
                            f"projects.json: capability {capability!r} references unknown repo {repo!r}",
                            errors)

    repo_agents = fetch_json(f"{SITE_REPO_RAW}/agents.json")
    live_agents = fetch_json(f"{LIVE_BASE}/agents.json")
    repo_tasks = fetch_json(f"{SITE_REPO_RAW}/tasks.json")
    live_tasks = fetch_json(f"{LIVE_BASE}/tasks.json")
    repo_llms = fetch_text(f"{SITE_REPO_RAW}/llms.txt")
    live_llms = fetch_text(f"{LIVE_BASE}/llms.txt")
    checks.extend([
        "repo agents.json",
        "live agents.json",
        "repo tasks.json",
        "live tasks.json",
        "repo llms.txt",
        "live llms.txt",
    ])

    require(repo_agents == live_agents,
            "deployment drift: live /agents.json differs from openutilitylab-site main", errors)
    require(repo_tasks == live_tasks,
            "deployment drift: live /tasks.json differs from openutilitylab-site main", errors)
    require(normalize_text(repo_llms) == normalize_text(live_llms),
            "deployment drift: live /llms.txt differs from openutilitylab-site main", errors)

    profile_custom_agent_refs: set[tuple[str, str]] = set()
    for repo, project in project_map.items():
        native = project.get("native_agent_surface")
        if not isinstance(native, dict):
            continue
        custom = native.get("custom_agents")
        if custom is None:
            continue
        require(isinstance(custom, list),
                f"projects.json: {repo} custom_agents must be an array", errors)
        if not isinstance(custom, list):
            continue
        for path in custom:
            require(isinstance(path, str) and path.startswith(".github/agents/"),
                    f"projects.json: invalid custom agent path for {repo}: {path!r}", errors)
            if not isinstance(path, str):
                continue
            ref = (repo, path)
            require(ref not in profile_custom_agent_refs,
                    f"projects.json: duplicate custom agent ref {repo}:{path}", errors)
            profile_custom_agent_refs.add(ref)
            try:
                fetch_text(f"https://raw.githubusercontent.com/{repo}/main/{path}")
                checks.append(f"custom_agent {repo}:{path}")
            except DriftError as exc:
                errors.append(str(exc))

    router_custom_agents = repo_agents.get("custom_agents")
    require(isinstance(router_custom_agents, list) and len(router_custom_agents) > 0,
            "agents.json: custom_agents missing/empty", errors)
    if not isinstance(router_custom_agents, list):
        router_custom_agents = []

    router_custom_agent_refs: set[tuple[str, str]] = set()
    router_custom_agent_names: set[str] = set()
    for agent in router_custom_agents:
        if not isinstance(agent, dict):
            errors.append("agents.json: custom_agents contains non-object")
            continue
        name = agent.get("name")
        repository = agent.get("repository")
        path = agent.get("path")
        require(isinstance(name, str) and bool(name.strip()),
                "agents.json: custom agent missing name", errors)
        require(isinstance(repository, str) and bool(repository),
                f"agents.json: custom agent {name!r} missing repository", errors)
        require(isinstance(path, str) and path.startswith(".github/agents/"),
                f"agents.json: custom agent {name!r} has invalid path {path!r}", errors)
        if isinstance(name, str):
            require(name not in router_custom_agent_names,
                    f"agents.json: duplicate custom agent name {name!r}", errors)
            router_custom_agent_names.add(name)
        if isinstance(repository, str) and isinstance(path, str):
            router_custom_agent_refs.add((repository, path))

    require(router_custom_agent_refs == profile_custom_agent_refs,
            "custom-agent drift: agents.json registry differs from projects.json native_agent_surface.custom_agents",
            errors)

    evidence_auditor = next(
        (
            agent for agent in router_custom_agents
            if isinstance(agent, dict) and agent.get("name") == "Evidence Auditor"
        ),
        None,
    )
    require(isinstance(evidence_auditor, dict),
            "agents.json: Evidence Auditor missing", errors)
    if isinstance(evidence_auditor, dict):
        require(evidence_auditor.get("repository") == "JoanAbad82/github-hidden-gems",
                "agents.json: Evidence Auditor repository drift", errors)
        require(evidence_auditor.get("path") == ".github/agents/evidence-auditor.agent.md",
                "agents.json: Evidence Auditor path drift", errors)
        require(evidence_auditor.get("safety") == "read-only",
                "agents.json: Evidence Auditor safety must remain read-only", errors)
        require(evidence_auditor.get("user_invocable") is True,
                "agents.json: Evidence Auditor must remain user-invocable", errors)

    surface_review_agent_by_repo: dict[str, str] = {}
    raw_task_surfaces = repo_agents.get("task_surfaces", [])
    if isinstance(raw_task_surfaces, list):
        for surface in raw_task_surfaces:
            if not isinstance(surface, dict):
                continue
            repo = surface.get("repository")
            recommended = surface.get("recommended_review_agent")
            if isinstance(repo, str) and recommended is not None:
                require(isinstance(recommended, str) and recommended in router_custom_agent_names,
                        f"agents.json: task surface {repo} references unknown recommended_review_agent {recommended!r}",
                        errors)
                if isinstance(recommended, str):
                    surface_review_agent_by_repo[repo] = recommended

    concrete = repo_agents.get("concrete_tasks", {})
    require(isinstance(concrete, dict), "agents.json: concrete_tasks missing", errors)
    if isinstance(concrete, dict):
        require(concrete.get("url") == task_index_url,
                "agents.json: concrete_tasks.url does not match projects.json", errors)
        require(concrete.get("schema") == task_index_schema_url,
                "agents.json: concrete_tasks.schema does not match projects.json", errors)

    require(repo_tasks.get("$schema") == task_index_schema_url,
            "tasks.json: $schema drift", errors)
    require(repo_tasks.get("owner") == "JoanAbad82",
            "tasks.json: owner mismatch", errors)
    task_rows = repo_tasks.get("tasks")
    require(isinstance(task_rows, list), "tasks.json: tasks must be an array", errors)
    if not isinstance(task_rows, list):
        task_rows = []
    require(repo_tasks.get("task_count") == len(task_rows),
            "tasks.json: task_count mismatch", errors)

    task_ids: set[str] = set()
    for task in task_rows:
        if not isinstance(task, dict):
            errors.append("tasks.json: non-object task entry")
            continue
        task_id = task.get("task_id")
        repo = task.get("repository")
        labels = task.get("labels", [])
        require(isinstance(task_id, str) and bool(task_id),
                "tasks.json: task missing task_id", errors)
        if isinstance(task_id, str):
            require(task_id not in task_ids,
                    f"tasks.json: duplicate task_id {task_id}", errors)
            task_ids.add(task_id)
        require(task.get("state") == "open",
                f"tasks.json: task {task_id} is not open", errors)
        require(isinstance(labels, list) and "agent-ready" in labels,
                f"tasks.json: task {task_id} missing agent-ready label", errors)
        require(isinstance(repo, str) and repo in project_map,
                f"tasks.json: task {task_id} references unknown repository {repo!r}", errors)
        expected_contract = project_map.get(repo, {}).get("interaction", {}).get("task_contract_ref")
        require(task.get("task_contract") == expected_contract,
                f"tasks.json: task {task_id} task_contract drift", errors)
        require(task.get("human_review_required") == ("human-review-required" in labels),
                f"tasks.json: task {task_id} human_review_required drift", errors)
        expected_review_agent = surface_review_agent_by_repo.get(str(repo))
        if expected_review_agent is not None:
            require(task.get("recommended_review_agent") == expected_review_agent,
                    f"tasks.json: task {task_id} recommended_review_agent drift", errors)
        elif "recommended_review_agent" in task:
            require(False,
                    f"tasks.json: task {task_id} has unrouted recommended_review_agent", errors)

    require(repo_agents.get("project_index") ==
            "https://github.com/JoanAbad82/JoanAbad82/blob/main/projects.json",
            "agents.json: project_index is not canonical", errors)
    require(repo_agents.get("agent_task_schema") == schema_url,
            "agents.json: agent_task_schema does not match projects.json", errors)

    known_router_repos: set[str] = set()
    for item in repo_agents.get("repositories", []):
        if not isinstance(item, dict):
            errors.append("agents.json: repositories contains non-object")
            continue
        name = item.get("name")
        if not isinstance(name, str):
            errors.append("agents.json: repository entry missing name")
            continue
        full = f"JoanAbad82/{name}"
        known_router_repos.add(full)
        require(full in project_map,
                f"agents.json: repository {full} missing from projects.json", errors)
        status_url = item.get("status_url")
        if isinstance(status_url, str) and status_url:
            try:
                fetch_text(status_url)
                checks.append(f"status_url {full}")
            except DriftError as exc:
                errors.append(str(exc))

    task_surfaces = repo_agents.get("task_surfaces", [])
    require(isinstance(task_surfaces, list) and len(task_surfaces) > 0,
            "agents.json: task_surfaces missing/empty", errors)
    if not isinstance(task_surfaces, list):
        task_surfaces = []

    fetched_task_indexes: dict[str, dict[str, Any]] = {}
    surface_task_urls: dict[str, str] = {}
    for surface in task_surfaces:
        if not isinstance(surface, dict):
            errors.append("agents.json: task_surfaces contains non-object")
            continue
        repo = surface.get("repository")
        task_index = surface.get("task_index")
        status_url = surface.get("project_status")
        caps = surface.get("capabilities")
        if not isinstance(repo, str):
            errors.append("agents.json: task surface missing repository")
            continue
        known_router_repos.add(repo)
        require(repo in project_map,
                f"agents.json: task surface {repo} missing from projects.json", errors)
        require(isinstance(task_index, str) and bool(task_index),
                f"agents.json: task surface {repo} missing task_index", errors)
        require(isinstance(status_url, str) and bool(status_url),
                f"agents.json: task surface {repo} missing project_status", errors)
        require(isinstance(caps, list) and len(caps) > 0,
                f"agents.json: task surface {repo} missing capabilities", errors)

        if isinstance(task_index, str) and task_index:
            surface_task_urls[repo] = task_index
            try:
                task = fetch_json(task_index)
                fetched_task_indexes[repo] = task
                validate_task_contract(task, repo, str(schema_url), errors, task_index)
                checks.append(f"task_index {repo}")
                project_caps = set(project_map.get(repo, {}).get("capabilities", []))
                task_caps = set(task.get("capabilities") or task.get("accepted_task_types") or [])
                surface_caps = set(caps if isinstance(caps, list) else [])
                require(task_caps == surface_caps,
                        f"{repo}: capabilities drift between AGENT_TASKS.json and agents.json", errors)
                require(task_caps == project_caps,
                        f"{repo}: capabilities drift between AGENT_TASKS.json and projects.json", errors)
                project_ref = project_map.get(repo, {}).get("interaction", {}).get("task_contract_ref")
                require(project_ref == task_index,
                        f"{repo}: projects.json task_contract_ref != agents.json task_index", errors)
                live_query = task.get("live_query")
                project_query = project_map.get(repo, {}).get("interaction", {}).get("open_challenges_url")
                surface_query = surface.get("live_agent_ready_query")
                require(live_query == project_query == surface_query,
                        f"{repo}: live agent-ready query drift", errors)
            except DriftError as exc:
                errors.append(str(exc))

        if isinstance(status_url, str) and status_url:
            try:
                status = fetch_json(status_url)
                require(status.get("repository") == repo,
                        f"{repo}: PROJECT_STATUS repository mismatch", errors)
                require(status.get("task_contract") == "AGENT_TASKS.json",
                        f"{repo}: PROJECT_STATUS task_contract drift", errors)
                checks.append(f"project_status {repo}")
            except DriftError as exc:
                errors.append(str(exc))

    cap_index = repo_agents.get("capability_index")
    require(isinstance(cap_index, dict), "agents.json: capability_index missing", errors)
    if isinstance(cap_index, dict):
        for capability, repos in cap_index.items():
            require(isinstance(repos, list) and len(repos) > 0,
                    f"agents.json: capability {capability!r} has no repositories", errors)
            if isinstance(repos, list):
                for repo in repos:
                    require(repo in known_router_repos,
                            f"agents.json: capability {capability!r} references unroutable repo {repo!r}",
                            errors)
                    profile_repos = capability_discovery.get(capability, []) if isinstance(capability_discovery, dict) else []
                    require(repo in profile_repos,
                            f"capability drift: {capability!r}/{repo!r} absent from projects.json capability_discovery",
                            errors)

    for repo, project in project_map.items():
        task_ref = project.get("interaction", {}).get("task_contract_ref")
        if isinstance(task_ref, str) and task_ref:
            if task_ref in surface_task_urls.values():
                continue
            try:
                task = fetch_json(task_ref)
                expected_repo = task.get("repository")
                if isinstance(expected_repo, str):
                    validate_task_contract(task, expected_repo, str(schema_url), errors, task_ref)
                    checks.append(f"project task_contract_ref {repo}")
            except DriftError as exc:
                errors.append(str(exc))

    expected_links = {
        "interaction_protocol": "https://github.com/JoanAbad82/JoanAbad82/blob/main/AGENT_INTERACTION.md",
        "open_research_challenges": "https://github.com/JoanAbad82/JoanAbad82/blob/main/OPEN_RESEARCH_CHALLENGES.md",
        "project_index": "https://github.com/JoanAbad82/JoanAbad82/blob/main/projects.json",
    }
    for field, expected in expected_links.items():
        require(repo_agents.get(field) == expected,
                f"agents.json: {field} drift ({repo_agents.get(field)!r} != {expected!r})",
                errors)
        try:
            fetch_text(expected)
            checks.append(f"canonical link {field}")
        except DriftError as exc:
            errors.append(str(exc))

    for needle in [
        f"{LIVE_BASE}/agents.json",
        f"{LIVE_BASE}/tasks.json",
        str(schema_url),
        str(task_index_schema_url),
        "https://github.com/JoanAbad82/github-hidden-gems/blob/main/.github/agents/evidence-auditor.agent.md",
        "https://raw.githubusercontent.com/JoanAbad82/github-hidden-gems-research-intake/main/AGENT_TASKS.json",
        "https://raw.githubusercontent.com/JoanAbad82/repasactiu-research-intake/main/AGENT_TASKS.json",
    ]:
        require(needle in repo_llms, f"llms.txt missing canonical link: {needle}", errors)

    report = {
        "schema_version": "1.0",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "user_agent": USER_AGENT,
        "status": "PASS" if not errors else "FAIL",
        "checks_completed": len(checks),
        "errors": errors,
        "warnings": warnings,
    }

    if args.json_report:
        Path(args.json_report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(f"DRIFT_GUARD={report['status']}")
    print(f"CHECKS_COMPLETED={len(checks)}")
    if warnings:
        for warning in warnings:
            print(f"WARNING: {warning}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
