"""Timestamp freshness: backwards compatible schema; strict published contract."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import portfolio_pulse as pulse
import validate_profile_pulse as contract


class ProfileFreshnessTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = contract.load_object(ROOT / "schemas/profile-pulse.schema.json")
        cls.snapshot = contract.load_object(ROOT / "profile-pulse.json")

    def test_published_date_required_and_valid(self):
        contract.check_published_timestamp(self.snapshot)

    def test_v1_legacy_snapshot_accepted_by_schema(self):
        snap = deepcopy(self.snapshot)
        snap.pop("generated_at")
        contract.validate_contract(self.schema, snap)
        with self.assertRaisesRegex(contract.ContractError,"generated_at"):
            contract.check_published_timestamp(snap)

    def test_invalid_calendar_date_rejected(self):
        snap=deepcopy(self.snapshot)
        snap["generated_at"]="2026-19-99T10:00:00Z"
        with self.assertRaises(contract.ContractError):
            contract.check_published_timestamp(snap)

    def test_non_utc_or_malformed_date_rejected(self):
        for date in ("2026-10-12T10:00:00+02:00","2026-10-12",123,"2026-10-12T10:00:00.000Z"):
            with self.subTest(date=date):
                snap=deepcopy(self.snapshot)
                snap["generated_at"]=date
                with self.assertRaises(contract.ContractError):
                    contract.check_published_timestamp(snap)

    def test_future_date_rejected(self):
        snap=deepcopy(self.snapshot)
        snap["generated_at"]="2099-01-01T00:00:00Z"
        with self.assertRaisesRegex(contract.ContractError,"Future"):
            contract.check_published_timestamp(snap)

    def test_no_timestamp_churn_without_changed_evidence(self):
        evidence=deepcopy(self.snapshot)
        evidence.pop("generated_at")
        with TemporaryDirectory() as t:
            readme=Path(t)/"README.md"
            index=Path(t)/"profile-pulse.json"
            readme.write_text("# Test\n\n## Selected work\n\nUntouched.\n",encoding="utf8")
            with patch.object(pulse,"collect",side_effect=lambda **kw: deepcopy(evidence)):
                first, out = pulse.run(readme,index,None,generated_at="2026-10-11T12:00:00Z")
                initial=index.read_bytes()
                second, repeated = pulse.run(readme,index,None,generated_at="2026-10-12T12:00:00Z")
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(initial,index.read_bytes())
            self.assertEqual(repeated["generated_at"],"2026-10-11T12:00:00Z")
            changed=deepcopy(evidence)
            changed["contribution_tasks"]=[]
            with patch.object(pulse,"collect",return_value=changed):
                third, last = pulse.run(readme,index,None,generated_at="2026-10-12T12:00:00Z")
            self.assertTrue(third)
            self.assertEqual(last["generated_at"],"2026-10-12T12:00:00Z")
            self.assertEqual(readme.read_text(encoding="utf8").count(pulse.START),1)

    def test_explicit_direct_context_link_required(self):
        contract.check_discovery(ROOT)
        with TemporaryDirectory() as t:
            root=Path(t)
            for name in ("README.md","AI_CONTEXT.md","AGENT_INTERACTION.md"):
                data=(ROOT/name).read_text(encoding="utf8")
                if name=="README.md":
                    data=data.replace("[AI_CONTEXT.md](AI_CONTEXT.md)","AI_CONTEXT.md")
                (root/name).write_text(data,encoding="utf8")
            with self.assertRaisesRegex(contract.ContractError,"AI_CONTEXT"):
                contract.check_discovery(root)
