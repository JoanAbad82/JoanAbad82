#!/usr/bin/env python3
"""Fail-closed drift guard for JoanAbad82 machine-readable agent surfaces."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

USER_AGENT = "JoanAbad82-AgentSurfaceDriftGuard/1.0"
SITE_REPO_RAW = "https://raw.githubusercontent.com/JoanAbad82/openutilitylab-site/main"
LIVE_BASE = "https://openutilitylab.com"


MAX_RESPONSE_BYTES = 1024 * 1024
REPOSITORY = re.compile(r"JoanAbad82/[A-Za-z0-9_.-]+", re.IGNORECASE)
REVISION = re.compile(r"[0-9a-f]{40}")
_RUN: ContextVar[dict[str, Any] | None] = ContextVar("guard_run", default=None)


class DriftError(RuntimeError):
    def __init__(self, message: str, code: str = "UNAVAILABLE") -> None:
        super().__init__(message)
        self.code = code


class DiagnosticErrors(list[str]):
    """Keep legacy error strings alongside explicit machine-readable codes."""

    def __init__(self) -> None:
        super().__init__()
        self.diagnostics: list[dict[str, str]] = []

    def add(self, message: str, code: str) -> None:
        super().append(message)
        self.diagnostics.append({"code": code, "message": message})

    def append(self, message: str) -> None:
        self.add(message, "DRIFT")


def record_error(errors: list[str], exc: DriftError) -> None:
    if isinstance(errors, DiagnosticErrors):
        errors.add(str(exc), exc.code)
    else:
        errors.append(str(exc))


def validate_destination(url: str) -> None:
    try:
        parts = urlsplit(url)
        path = parts.path.split("/")[1:]
        valid = (url.isascii() and not any(ord(ch) <= 32 or ord(ch) == 127 for ch in url)
                 and parts.scheme == "https" and parts.netloc == parts.hostname
                 and not parts.fragment and not parts.query
                 and all(re.fullmatch(r"[A-Za-z0-9_.-]+", x)
                         and x not in {".", ".."} for x in path))
        if valid and parts.hostname == "raw.githubusercontent.com":
            valid = (len(path) >= 4 and REPOSITORY.fullmatch("/".join(path[:2]))
                     and (path[2] == "main" or REVISION.fullmatch(path[2])))
        elif valid and parts.hostname == "github.com":
            valid = (len(path) >= 5 and REPOSITORY.fullmatch("/".join(path[:2]))
                     and path[2:4] == ["blob", "main"])
        elif valid and parts.hostname == "api.github.com":
            valid = (len(path) == 7 and path[0] == "repos"
                     and REPOSITORY.fullmatch("/".join(path[1:3]))
                     and path[3:] == ["git", "ref", "heads", "main"])
        elif valid and parts.hostname == "openutilitylab.com":
            valid = parts.path in {"/agents.json", "/tasks.json", "/llms.txt",
                                   "/PROJECT_STATUS.json"}
        else:
            valid = False
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise DriftError(f"Disallowed destination: {url}", "NETWORK_POLICY")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DriftError(f"Redirect refused for {req.full_url}", "NETWORK_POLICY")


def _open_url(req, timeout):
    return urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout)


def _record_source(source: str, data: bytes, **metadata: Any) -> None:
    run = _RUN.get()
    if run is not None:
        run["sources"].append({"source": source, "sha256": hashlib.sha256(data).hexdigest(),
                               "bytes": len(data), **metadata})


def _pin_raw_url(url: str) -> str:
    run = _RUN.get()
    parts = urlsplit(url)
    path = parts.path.split("/")
    if (run is None or parts.hostname != "raw.githubusercontent.com"
            or len(path) < 5 or path[3] != "main"):
        return url
    repo = "/".join(path[1:3])
    revisions = run["remote_revisions"]
    if repo not in revisions:
        ref = fetch_json(f"https://api.github.com/repos/{repo}/git/ref/heads/main")
        obj = ref.get("object")
        sha = obj.get("sha") if isinstance(obj, dict) else None
        if (ref.get("ref") != "refs/heads/main" or not isinstance(obj, dict)
                or obj.get("type") != "commit" or not isinstance(sha, str)
                or not REVISION.fullmatch(sha)):
            raise DriftError(f"Invalid main ref for {repo}", "INVALID_DATA")
        revisions[repo] = sha
    path[3] = revisions[repo]
    return f"https://raw.githubusercontent.com{'/'.join(path)}"


def _validator(schema: dict[str, Any]) -> Any:
    try:
        from jsonschema import Draft202012Validator, FormatChecker
        from jsonschema.exceptions import SchemaError
        from referencing import Registry
    except ImportError as exc:
        raise DriftError("Required JSON Schema validator is unavailable", "DEPENDENCY_ERROR") from exc
    # No remote schema retrieval; references must resolve within the local schema.
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise DriftError("Unsupported local JSON Schema dialect", "INVALID_SCHEMA")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise DriftError(f"Invalid local JSON Schema: {exc.message}", "INVALID_SCHEMA") from exc
    formats = FormatChecker()
    if not {"uri", "date-time"} <= formats.checkers.keys():
        raise DriftError("Required URI/date-time format validators are unavailable",
                         "DEPENDENCY_ERROR")
    return Draft202012Validator(schema, format_checker=formats, registry=Registry())


def validate_schema(instance: dict[str, Any], validator: Any,
                    errors: list[str], source: str) -> None:
    for issue in sorted(validator.iter_errors(instance), key=lambda e: (str(e.json_path), e.message)):
        message = f"{source}: schema {issue.json_path}: {issue.message}"
        if isinstance(errors, DiagnosticErrors):
            errors.add(message, "SCHEMA_VIOLATION")
        else:
            errors.append(message)


def fetch_text(url: str, timeout: int = 20) -> str:
    validate_destination(url)
    url = _pin_raw_url(url)
    # A transient 503 is not evidence of drift. Retry, but never accept
    # an unavailable endpoint as verified.
    for attempt in range(3):
        try:
            return _fetch_text_once(url, timeout)
        except DriftError as exc:
            cause = exc.__cause__
            if (not isinstance(cause, urllib.error.HTTPError)
                    or cause.code != 503 or attempt == 2):
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def _fetch_text_once(url: str, timeout: int) -> str:
    request_url = url
    if urlsplit(url).hostname in {"raw.githubusercontent.com", "openutilitylab.com"}:
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
        with _open_url(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                raise DriftError(f"HTTP {getattr(resp, 'status', 'unknown')} for {url}")
            data = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise DriftError(f"Response too large for {url}", "NETWORK_POLICY")
            _record_source(url, data, etag=resp.headers.get("ETag"),
                           last_modified=resp.headers.get("Last-Modified"))
            try:
                return data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise DriftError(f"Invalid UTF-8 at {url}", "INVALID_DATA") from exc
    except urllib.error.HTTPError as exc:
        raise DriftError(f"HTTP {exc.code} for {url}",
                         "NOT_FOUND" if exc.code == 404 else "UNAVAILABLE") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise DriftError(f"Timeout for {url}: {exc.reason}") from exc
        raise DriftError(f"URL error for {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise DriftError(f"Timeout for {url}: {exc}") from exc
    except OSError as exc:
        raise DriftError(f"Network I/O error for {url}: {exc}") from exc


def fetch_json(url: str) -> dict[str, Any]:
    try:
        data = json.loads(fetch_text(url))
    except json.JSONDecodeError as exc:
        raise DriftError(f"Invalid JSON at {url}: {exc}", "INVALID_DATA") from exc
    if not isinstance(data, dict):
        raise DriftError(f"Expected JSON object at {url}", "INVALID_DATA")
    return data


def load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        _record_source(path.name, raw)
        data = json.loads(raw.decode("utf-8-sig"))
    except FileNotFoundError as exc:
        raise DriftError(f"Missing local file: {path}", "LOCAL_INPUT") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DriftError(f"Invalid local JSON {path}: {exc}", "INVALID_DATA") from exc
    if not isinstance(data, dict):
        raise DriftError(f"Expected JSON object in {path}", "INVALID_DATA")
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


def _run_checks(
    root: Path, errors: list[str], warnings: list[str], checks: list[str]
) -> None:
    projects = load_json(root / "projects.json")
    schema = load_json(root / "schemas" / "agent-task-contract.schema.json")
    task_index_schema = load_json(root / "schemas" / "agent-task-index.schema.json")
    contract_validator = _validator(schema)
    index_validator = _validator(task_index_schema)
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

    validate_schema(repo_tasks, index_validator, errors, "repo tasks.json")
    validate_schema(live_tasks, index_validator, errors, "live tasks.json")

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
                record_error(errors, exc)

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
        if isinstance(repo, str) and type(task.get("issue_number")) is int:
            number = task["issue_number"]
            require(task_id == f"{repo}#{number}",
                    f"tasks.json: task {task_id} issue identity drift", errors)
            require(task.get("url") == f"https://github.com/{repo}/issues/{number}",
                    f"tasks.json: task {task_id} issue URL drift", errors)
        expected_contract = project_map.get(repo, {}).get("interaction", {}).get("task_contract_ref")
        require(task.get("task_contract") == expected_contract,
                f"tasks.json: task {task_id} task_contract drift", errors)
        require(task.get("human_review_required") is ("human-review-required" in labels),
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
                record_error(errors, exc)

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
                validate_schema(task, contract_validator, errors, task_index)
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
                record_error(errors, exc)

        if isinstance(status_url, str) and status_url:
            try:
                status = fetch_json(status_url)
                require(status.get("repository") == repo,
                        f"{repo}: PROJECT_STATUS repository mismatch", errors)
                require(status.get("task_contract") == "AGENT_TASKS.json",
                        f"{repo}: PROJECT_STATUS task_contract drift", errors)
                checks.append(f"project_status {repo}")
            except DriftError as exc:
                record_error(errors, exc)

    for row in task_rows:
        if not isinstance(row, dict):
            continue
        repo = row.get("repository")
        surface = next((x for x in task_surfaces if isinstance(x, dict)
                        and x.get("repository") == repo), None)
        actual = row.get("surface_capabilities")
        expected = surface.get("capabilities") if surface else None
        require(isinstance(actual, list) and isinstance(expected, list)
                and all(isinstance(x, str) for x in actual + expected)
                and set(actual) == set(expected),
                f"tasks.json: task {row.get('task_id')} surface_capabilities drift", errors)

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
                require(surface_task_urls.get(repo) == task_ref,
                        f"{repo}: task_contract_ref points at another task surface", errors)
                continue
            try:
                task = fetch_json(task_ref)
                validate_schema(task, contract_validator, errors, task_ref)
                validate_task_contract(task, repo, str(schema_url), errors, task_ref)
                checks.append(f"project task_contract_ref {repo}")
            except DriftError as exc:
                record_error(errors, exc)

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
            record_error(errors, exc)

    for needle in [
        f"{LIVE_BASE}/agents.json",
        f"{LIVE_BASE}/tasks.json",
        str(schema_url),
        str(task_index_schema_url),
        "https://github.com/JoanAbad82/github-hidden-gems/blob/main/.github/agents/evidence-auditor.agent.md",
        "https://raw.githubusercontent.com/JoanAbad82/github-hidden-gems-research-intake/main/AGENT_TASKS.json",
    ]:
        require(needle in repo_llms, f"llms.txt missing canonical link: {needle}", errors)

def _write_report_atomically(path: Path, report: dict[str, Any]) -> None:
    """Persist complete JSON on success or failure; replace only after fsync."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n",
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-root", default=".", help="Local checkout of JoanAbad82 profile repo")
    parser.add_argument("--json-report", default="", help="Optional report path")
    parser.add_argument("--evidence-report", default="", help="Optional separate report with diagnostics and source evidence")
    args = parser.parse_args()
    if (args.json_report and args.evidence_report
            and Path(args.json_report).resolve() == Path(args.evidence_report).resolve()):
        parser.error("Legacy and evidence report paths must differ")

    root = Path(args.profile_root).resolve()
    errors = DiagnosticErrors()
    warnings: list[str] = []
    checks: list[str] = []
    run: dict[str, Any] = {"sources": [], "remote_revisions": {}}
    token = _RUN.set(run)
    try:
        _run_checks(root, errors, warnings, checks)
    except DriftError as exc:
        record_error(errors, exc)
    except Exception as exc:
        # Never emit PASS or lose the report after an unexpected validation error.
        errors.add(f"Validation interrupted ({type(exc).__name__}): {exc}", "INTERNAL_ERROR")
    finally:
        _RUN.reset(token)

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
        _write_report_atomically(Path(args.json_report), report)

    if args.evidence_report:
        head = None
        dirty = None
        try:
            head = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
            dirty = bool(subprocess.check_output(
                ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                text=True, stderr=subprocess.DEVNULL, timeout=5).strip())
        except (OSError, subprocess.SubprocessError):
            pass
        details = {"version": "1.1", "profile_git_head": head,
                   "profile_tracked_worktree_dirty": dirty,
                   "guard_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   "diagnostics": errors.diagnostics, **run}
        _write_report_atomically(Path(args.evidence_report), {**report, "details": details})

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
