"""Offline, synthetic regressions for agent surface drift guard (Actions #42477750994)."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import agent_surface_drift_guard as guard


class ReadTimeoutResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        raise TimeoutError("synthetic read timed out")


class GuardNetworkFailures(unittest.TestCase):
    def test_timeout_at_connect_is_dr_error_with_context(self):
        url = "https://example.invalid/agent-surface.json"
        with patch.object(guard.urllib.request, "urlopen", side_effect=TimeoutError("synthetic connect timeout")):
            with self.assertRaises(guard.DriftError) as raised:
                guard.fetch_text(url, timeout=2)
        self.assertIn("timeout", str(raised.exception).lower())
        self.assertIn(url, str(raised.exception))

    def test_timeout_during_read_is_dr_error(self):
        url = "https://example.invalid/canonical.md"
        with patch.object(guard.urllib.request, "urlopen", return_value=ReadTimeoutResponse()):
            with self.assertRaises(guard.DriftError) as raised:
                guard.fetch_text(url, timeout=2)
        self.assertIn("timeout", str(raised.exception).lower())
        self.assertIn(url, str(raised.exception))

    def test_connection_reset_is_dr_error(self):
        with patch.object(guard.urllib.request, "urlopen", side_effect=ConnectionResetError("synthetic reset")):
            with self.assertRaises(guard.DriftError):
                guard.fetch_text("https://example.invalid/disconnected.json", timeout=2)

    def test_urlerror_wrapping_timeout_is_explicit(self):
        url = "https://example.invalid/wrapped-timeout"
        with patch.object(
            guard.urllib.request, "urlopen",
            side_effect=urllib.error.URLError(TimeoutError("synthetic nested timeout")),
        ):
            with self.assertRaises(guard.DriftError) as raised:
                guard.fetch_text(url, timeout=2)
        self.assertIn("Timeout", str(raised.exception))
        self.assertIn(url, str(raised.exception))

    def test_existing_urlerror_remains_fail_closed(self):
        with patch.object(
            guard.urllib.request, "urlopen",
            side_effect=urllib.error.URLError("synthetic network error")
        ):
            with self.assertRaisesRegex(guard.DriftError, "URL error"):
                guard.fetch_text("https://example.invalid/network", timeout=2)


class GuardReportFailures(unittest.TestCase):
    def _invoke(self, profile_root: Path, report_path: Path, *, fake_checks=None):
        argv = [
            "agent_surface_drift_guard.py",
            "--profile-root", str(profile_root),
            "--json-report", str(report_path),
        ]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()) as stdout:
            if fake_checks is None:
                rc = guard.main()
            else:
                with patch.object(guard, "_run_checks", side_effect=fake_checks):
                    rc = guard.main()
        return rc, stdout.getvalue(), json.loads(report_path.read_text(encoding="utf-8"))

    def test_real_validation_path_stops_at_synthetic_external_timeout(self):
        original_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "drift-report.json"
            with patch.object(
                guard, "fetch_json",
                side_effect=guard.DriftError("Timeout for synthetic remote agents.json"),
            ) as remote:
                rc, output, result = self._invoke(original_root, report)
            self.assertEqual(rc, 1)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("Timeout" in message for message in result["errors"]))
            self.assertIn("DRIFT_GUARD=FAIL", output)
            remote.assert_called_once()

    def test_missing_local_file_still_writes_failed_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "drift-report.json"
            rc, output, result = self._invoke(root, report)
            self.assertEqual(rc, 1)
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["checks_completed"], 0)
            self.assertTrue(any("Missing local file" in item for item in result["errors"]))
            self.assertIn("DRIFT_GUARD=FAIL", output)
            self.assertEqual(result["schema_version"], "1.0")

    def test_unexpected_timeout_preserves_partial_checks_and_report(self):
        def interrupt(_root, errors, warnings, checks):
            checks.append("synthetic check passed")
            warnings.append("synthetic warning")
            raise TimeoutError("synthetic unhandled timeout")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "drift-report.json"
            rc, output, result = self._invoke(root, report, fake_checks=interrupt)
            self.assertEqual(rc, 1)
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["checks_completed"], 1)
            self.assertEqual(result["warnings"], ["synthetic warning"])
            self.assertTrue(any("TimeoutError" in item for item in result["errors"]))
            self.assertIn("DRIFT_GUARD=FAIL", output)

    def test_unexpected_error_replaces_previous_report_atomically(self):
        def interrupt(_root, _errors, _warnings, _checks):
            raise RuntimeError("synthetic unexpected programmer failure")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "drift-report.json"
            report.write_text('{"status":"PASS","old":true}\n', encoding="utf-8")
            rc, _output, result = self._invoke(root, report, fake_checks=interrupt)
            self.assertEqual(rc, 1)
            self.assertEqual(result["status"], "FAIL")
            self.assertNotIn("old", result)
            self.assertTrue(any("RuntimeError" in item for item in result["errors"]))
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_synthetic_validation_drift_fails_with_report(self):
        def drift(_root, errors, _warnings, checks):
            checks.append("synthetic check passed")
            errors.append("synthetic JSON schema drift")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "drift-report.json"
            rc, output, result = self._invoke(root, report, fake_checks=drift)
            self.assertEqual(rc, 1)
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["errors"], ["synthetic JSON schema drift"])
            self.assertEqual(result["checks_completed"], 1)
            self.assertIn("synthetic JSON schema drift", output)

    def test_synthetic_success_keeps_existing_contract(self):
        def healthy(_root, _errors, _warnings, checks):
            checks.extend(["synthetic link verified", "synthetic contract verified"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "drift-report.json"
            rc, output, result = self._invoke(root, report, fake_checks=healthy)
            self.assertEqual(rc, 0)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["checks_completed"], 2)
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["warnings"], [])
            self.assertEqual(
                set(result),
                {"schema_version", "checked_at_utc", "user_agent", "status",
                 "checks_completed", "errors", "warnings"},
            )
            self.assertIn("DRIFT_GUARD=PASS", output)


if __name__ == "__main__":
    unittest.main()
