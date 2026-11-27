"""Deterministic offline contract mutations and network-boundary tests."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import agent_surface_drift_guard as guard

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "JoanAbad82/JoanAbad82"
REPO = "JoanAbad82/github-hidden-gems-research-intake"
PRIMARY = "JoanAbad82/github-hidden-gems"
RAW = "https://raw.githubusercontent.com/"
GITHUB = "https://github.com/"
TASK_URL = RAW + REPO + "/main/AGENT_TASKS.json"
STATUS_URL = RAW + REPO + "/main/PROJECT_STATUS.json"
AGENT_PATH = ".github/agents/evidence-auditor.agent.md"
CAPS = ["falsification", "independent-reproduction"]
SCHEMA = "schemas/agent-task-contract.schema.json"
INDEX_SCHEMA = "schemas/agent-task-index.schema.json"


class MutationTests(unittest.TestCase):
    def setUp(self):
        self.local = {
            p: json.loads((ROOT / p).read_text(encoding="utf-8-sig"))
            for p in (SCHEMA, INDEX_SCHEMA)
        }
        contract_schema = self.local[SCHEMA]["$id"]
        index_schema = self.local[INDEX_SCHEMA]["$id"]
        task_index = guard.LIVE_BASE + "/tasks.json"
        query = GITHUB + REPO + "/issues?q=is%3Aopen"
        self.local["projects.json"] = {
            "agent_contract_schema": contract_schema,
            "agent_interaction": {"task_index_url": task_index, "task_index_schema": index_schema},
            "projects": [
                {"repository": PRIMARY, "native_agent_surface": {"custom_agents": [AGENT_PATH]}},
                {"repository": REPO, "capabilities": CAPS,
                 "interaction": {"task_contract_ref": TASK_URL, "open_challenges_url": query}},
            ],
            "capability_discovery": {cap: [REPO] for cap in CAPS},
        }
        self.agents = {
            "custom_agents": [{"name": "Evidence Auditor", "repository": PRIMARY,
                               "path": AGENT_PATH, "safety": "read-only", "user_invocable": True}],
            "task_surfaces": [{"repository": REPO, "task_index": TASK_URL,
                               "project_status": STATUS_URL, "capabilities": CAPS,
                               "live_agent_ready_query": query,
                               "recommended_review_agent": "Evidence Auditor"}],
            "concrete_tasks": {"url": task_index, "schema": index_schema},
            "project_index": GITHUB + PROFILE + "/blob/main/projects.json",
            "agent_task_schema": contract_schema,
            "repositories": [{"name": "github-hidden-gems"}],
            "capability_index": {cap: [REPO] for cap in CAPS},
            "interaction_protocol": GITHUB + PROFILE + "/blob/main/AGENT_INTERACTION.md",
            "open_research_challenges": GITHUB + PROFILE + "/blob/main/OPEN_RESEARCH_CHALLENGES.md",
        }
        self.tasks = {
            "$schema": index_schema, "schema_version": "1.0", "owner": "JoanAbad82",
            "purpose": "Synthetic fixture",
            "source_policy": {"state": "open", "label": "agent-ready", "repositories": [REPO]},
            "task_count": 1,
            "tasks": [{"task_id": REPO + "#1", "repository": REPO, "issue_number": 1,
                       "title": "Synthetic task", "url": GITHUB + REPO + "/issues/1",
                       "state": "open", "updated_at": "2026-11-26T00:00:00Z",
                       "task_contract": TASK_URL, "surface_capabilities": CAPS,
                       "labels": ["agent-ready", "human-review-required"],
                       "human_review_required": True, "recommended_review_agent": "Evidence Auditor"}],
        }
        self.contract = {
            "$schema": contract_schema, "schema_version": "1.0", "repository": REPO,
            "purpose": "Synthetic contract", "capabilities": CAPS, "live_query": query,
            "task_contract": {"external_input_trust": "UNTRUSTED_EXTERNAL_INPUT",
                              "production_write_access": False, "automatic_promotion": False,
                              "expected_artifact": ["report"], "completion_condition": "Evidence"},
        }
        self.llms = "\n".join([guard.LIVE_BASE + "/agents.json", task_index,
                               contract_schema, index_schema, GITHUB + PRIMARY + "/blob/main/" + AGENT_PATH,
                               TASK_URL])
        self.local = copy.deepcopy(self.local)
        self.agents = copy.deepcopy(self.agents)
        self.tasks = copy.deepcopy(self.tasks)
        self.contract = copy.deepcopy(self.contract)

    def run_guard(self, live_agents=None):
        responses = {
            guard.SITE_REPO_RAW + "/agents.json": json.dumps(self.agents),
            guard.LIVE_BASE + "/agents.json": json.dumps(self.agents if live_agents is None else live_agents),
            guard.SITE_REPO_RAW + "/tasks.json": json.dumps(self.tasks),
            guard.LIVE_BASE + "/tasks.json": json.dumps(self.tasks),
            guard.SITE_REPO_RAW + "/llms.txt": self.llms,
            guard.LIVE_BASE + "/llms.txt": self.llms,
            TASK_URL: json.dumps(self.contract),
            STATUS_URL: json.dumps({"repository": REPO, "task_contract": "AGENT_TASKS.json"}),
        }
        for url in (RAW + PRIMARY + "/main/" + AGENT_PATH,
                    self.agents["interaction_protocol"], self.agents["open_research_challenges"],
                    self.agents["project_index"]):
            responses[url] = "synthetic HTTP 200"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, data in self.local.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data), encoding="utf-8")
            errors = guard.DiagnosticErrors()
            with patch.object(guard, "fetch_text", side_effect=lambda url: responses[url]):
                try:
                    guard._run_checks(root, errors, [], [])
                except guard.DriftError as exc:
                    guard.record_error(errors, exc)
            return errors

    def test_healthy_control(self):
        self.assertEqual(self.run_guard(), [])

    def test_mutation_missing_source_policy(self):
        del self.tasks["source_policy"]
        self.assertTrue(any("source_policy" in e for e in self.run_guard()))

    def test_mutation_missing_issue_url(self):
        del self.tasks["tasks"][0]["url"]
        self.assertTrue(any("url" in e.lower() for e in self.run_guard()))

    def test_mutation_wrong_issue_identity(self):
        self.tasks["tasks"][0]["issue_number"] = 999999
        self.assertTrue(any("issue identity drift" in e for e in self.run_guard()))

    def test_mutation_integer_review_flag(self):
        self.tasks["tasks"][0]["human_review_required"] = 1
        self.assertTrue(any("boolean" in e for e in self.run_guard()))

    def test_mutation_wrong_surface_capabilities(self):
        self.tasks["tasks"][0]["surface_capabilities"] = ["not-a-real-capability"]
        self.assertTrue(any("surface_capabilities drift" in e for e in self.run_guard()))

    def test_mutation_nonstring_artifact(self):
        self.contract["task_contract"]["expected_artifact"] = [123]
        self.assertTrue(any("expected_artifact" in e for e in self.run_guard()))

    def test_mutation_duplicate_capability(self):
        self.contract["capabilities"].append(CAPS[0])
        self.assertTrue(any("non-unique" in e for e in self.run_guard()))

    def test_mutation_invalid_schema(self):
        self.local[SCHEMA]["type"] = "not-a-json-schema-type"
        errors = self.run_guard()
        self.assertEqual(errors.diagnostics[0]["code"], "INVALID_SCHEMA")

    def test_mutation_task_count(self):
        self.tasks["task_count"] = 9
        self.assertTrue(any("task_count mismatch" in e for e in self.run_guard()))

    def test_mutation_write_access(self):
        self.contract["task_contract"]["production_write_access"] = True
        self.assertTrue(any("production_write_access" in e for e in self.run_guard()))

    def test_mutation_deployment_difference(self):
        self.assertTrue(any("deployment drift" in e for e in self.run_guard(live_agents={})))

    def test_schema_format_validation(self):
        self.tasks["tasks"][0]["updated_at"] = "not-a-date"
        self.assertTrue(any("date-time" in e for e in self.run_guard()))

    def test_schema_uri_validation(self):
        self.tasks["tasks"][0]["url"] = "not a URI"
        self.assertTrue(any(d["code"] == "SCHEMA_VIOLATION" and "uri" in d["message"]
                            for d in self.run_guard().diagnostics))

    def test_missing_format_dependency_fails_closed(self):
        with patch("jsonschema.FormatChecker") as formats:
            formats.return_value.checkers = {}
            self.assertEqual(self.run_guard().diagnostics[0]["code"], "DEPENDENCY_ERROR")

    def test_boolean_task_count_even_if_numerically_equal(self):
        self.tasks["task_count"] = True
        self.assertTrue(any("integer" in e for e in self.run_guard()))


class NetworkPolicyTests(unittest.TestCase):
    def test_unapproved_destinations_are_blocked_before_network(self):
        bad = ["http://openutilitylab.com/tasks.json",
               "https://openutilitylab.com.evil.test/tasks.json",
               "https://openutilitylab.com@evil.test/tasks.json",
               "https://openutilitylab.com:443/tasks.json",
               "https://openutilitylab.com/tasks.json?x=1",
               "https://raw.githubusercontent.com/OtherOwner/repo/main/file",
               "https://raw.githubusercontent.com/JoanAbad82/repo/main/../file",
               "https://raw.githubusercontent.com/JoanAbad82/repo/main/%2e%2e/file",
               "https://openutilitylab.com/\ttasks.json",
               "https://127.0.0.1/tasks.json"]
        with patch.object(guard, "_open_url") as remote:
            for url in bad:
                with self.subTest(url=url), self.assertRaises(guard.DriftError) as exc:
                    guard.fetch_text(url)
                self.assertEqual(exc.exception.code, "NETWORK_POLICY")
            remote.assert_not_called()

    def test_redirect_blocked(self):
        handler = guard.NoRedirect()
        request = urllib.request.Request(guard.LIVE_BASE + "/tasks.json")
        with self.assertRaises(guard.DriftError) as exc:
            handler.redirect_request(request, None, 302, "Found", {}, "https://evil.test/")
        self.assertEqual(exc.exception.code, "NETWORK_POLICY")

    def test_response_limit(self):
        with patch.object(guard, "_open_url") as remote:
            response = remote.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b"x" * (guard.MAX_RESPONSE_BYTES + 1)
            with self.assertRaises(guard.DriftError) as exc:
                guard.fetch_text(guard.LIVE_BASE + "/tasks.json")
            self.assertEqual(exc.exception.code, "NETWORK_POLICY")
            response.read.assert_called_once_with(guard.MAX_RESPONSE_BYTES + 1)

    def test_pin_raw_revision_once_per_repository(self):
        run = {"sources": [], "remote_revisions": {}}
        sha = "a" * 40
        token = guard._RUN.set(run)
        try:
            with patch.object(guard, "fetch_json", return_value={
                "ref": "refs/heads/main", "object": {"type": "commit", "sha": sha}
            }) as ref:
                self.assertEqual(guard._pin_raw_url(TASK_URL), TASK_URL.replace("/main/", f"/{sha}/"))
                self.assertEqual(guard._pin_raw_url(STATUS_URL), STATUS_URL.replace("/main/", f"/{sha}/"))
                ref.assert_called_once()
            self.assertEqual(run["remote_revisions"], {REPO: sha})
        finally:
            guard._RUN.reset(token)

    def test_source_hash_is_over_exact_bytes(self):
        run = {"sources": [], "remote_revisions": {}}
        raw = b"\xef\xbb\xbfhello\r\n"
        token = guard._RUN.set(run)
        try:
            with patch.object(guard, "_open_url") as remote:
                response = remote.return_value.__enter__.return_value
                response.status = 200
                response.read.return_value = raw
                response.headers = {"ETag": "synthetic"}
                self.assertEqual(guard.fetch_text(guard.LIVE_BASE + "/llms.txt"), "hello\r\n")
            self.assertEqual(run["sources"][0]["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(run["sources"][0]["bytes"], len(raw))
        finally:
            guard._RUN.reset(token)

    def test_extended_report_distinguishes_network_and_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            legacy = Path(tmp) / "legacy.json"
            argv = ["guard", "--profile-root", tmp, "--json-report", str(legacy), "--evidence-report", str(path)]
            def fake(_root, errors, _warnings, _checks):
                errors.append("semantic drift")
                raise guard.DriftError("HTTP 503", "UNAVAILABLE")
            with patch.object(sys, "argv", argv), patch.object(guard, "_run_checks", side_effect=fake):
                self.assertEqual(guard.main(), 1)
            result = json.loads(path.read_text(encoding="utf-8"))
            original = json.loads(legacy.read_text(encoding="utf-8"))
            self.assertEqual(original, {k: v for k, v in result.items() if k != "details"})
            self.assertNotIn("details", original)
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual([d["code"] for d in result["details"]["diagnostics"]], ["DRIFT", "UNAVAILABLE"])
            self.assertEqual(result["errors"], ["semantic drift", "HTTP 503"])


if __name__ == "__main__":
    unittest.main()
