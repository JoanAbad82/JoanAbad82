#!/usr/bin/env python3
"""Validate public Portfolio Pulse JSON Schema, evidence links and cross-field identities.

Runs offline with jsonschema pinned in CI. This checks the snapshot contract,
not the freshness or authenticity of live GitHub metadata.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys

from jsonschema import Draft202012Validator

OWNER = "JoanAbad82"
SCHEMA_URI = (
    "https://raw.githubusercontent.com/JoanAbad82/JoanAbad82/main/"
    "schemas/profile-pulse.schema.json"
)
SNAPSHOT_URI = "https://raw.githubusercontent.com/JoanAbad82/JoanAbad82/main/profile-pulse.json"
CATEGORIES = ("accepted_upstream", "accepted_inbound", "external_inbound", "contribution_tasks")


class ContractError(ValueError):
    pass


def load_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ContractError(f"Expected JSON object: {path.name}")
    return value


def validate_contract(schema: dict, snapshot: dict) -> None:
    """Fail closed for JSON Schema shape plus invariants across fields and groups."""
    Draft202012Validator.check_schema(schema)
    if schema.get("$id") != SCHEMA_URI:
        raise ContractError("Unexpected JSON Schema identifier")
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    errors = sorted(validator.iter_errors(snapshot), key=lambda x: (str(list(x.absolute_path)), x.message))
    if errors:
        issue = errors[0]
        raise ContractError(f"Schema violation at {list(issue.absolute_path)!r}: {issue.message}")

    seen: set[tuple[str, int]] = set()
    for group in CATEGORIES:
        for entry in snapshot[group]:
            repository = entry["repo"]
            number = entry["number"]
            author = entry["author"]
            kind = "issues" if group == "contribution_tasks" else "pull"
            expected_url = f"https://github.com/{repository}/{kind}/{number}"
            if entry["url"] != expected_url:
                raise ContractError(f"{group}: URL does not match repository, kind and issue number")
            key = (repository.casefold(), number)
            if key in seen:
                raise ContractError(f"Duplicate GitHub issue/PR across categories: {repository}#{number}")
            seen.add(key)
            owner = repository.split("/", 1)[0]
            if group == "accepted_upstream":
                if owner.casefold() == OWNER.casefold() or author.casefold() != OWNER.casefold():
                    raise ContractError("accepted_upstream must be authored by profile owner in external repo")
            elif owner.casefold() != OWNER.casefold():
                raise ContractError(f"{group}: repository is not owned by profile owner")
            if group in ("accepted_inbound", "external_inbound") and author.casefold() == OWNER.casefold():
                raise ContractError(f"{group}: community PR must be authored externally")


def check_published_timestamp(snapshot: dict) -> None:
    """Backward-compatible schema, strict published snapshot; not an authenticity signature."""
    value = snapshot.get("generated_at")
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise ContractError("Missing or malformed UTC generated_at")
    try:
        generated = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ContractError("Invalid UTC generated_at calendar date") from exc
    if generated > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ContractError("Future UTC generated_at")


def check_discovery(root: Path) -> None:
    """Public landing points must link directly to data and its actual schema."""
    if "[AI_CONTEXT.md](AI_CONTEXT.md)" not in (root / "README.md").read_text(encoding="utf-8"):
        raise ContractError("README.md: missing direct AI_CONTEXT.md link")
    for relative in ("README.md", "AI_CONTEXT.md", "AGENT_INTERACTION.md"):
        data = (root / relative).read_text(encoding="utf-8")
        for uri in (SNAPSHOT_URI, SCHEMA_URI):
            if uri not in data:
                raise ContractError(f"{relative}: missing public evidence or schema URL")


def run(root: Path) -> dict:
    schema = load_object(root / "schemas/profile-pulse.schema.json")
    snapshot = load_object(root / "profile-pulse.json")
    validate_contract(schema, snapshot)
    check_published_timestamp(snapshot)
    check_discovery(root)
    return {name: len(snapshot[name]) for name in CATEGORIES}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Portfolio Pulse published schema and agent discovery")
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    try:
        counts = run(args.root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"PROFILE_PULSE_CONTRACT=FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("PROFILE_PULSE_CONTRACT=PASS")
    print("ENTRY_COUNTS=" + json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
