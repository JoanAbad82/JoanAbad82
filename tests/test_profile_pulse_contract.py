"""Deterministic positive and adversarial tests of public Portfolio Pulse contract."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import validate_profile_pulse as contract


class PublicPortfolioPulseContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = contract.load_object(ROOT / "schemas/profile-pulse.schema.json")
        cls.snapshot = contract.load_object(ROOT / "profile-pulse.json")

    def clone(self) -> dict:
        return deepcopy(self.snapshot)

    def test_published_snapshot_passes(self) -> None:
        contract.validate_contract(self.schema, self.clone())

    def test_profile_and_agent_docs_link_exact_public_uris(self) -> None:
        contract.check_discovery(ROOT)

    def test_missing_schema_reference_fails(self) -> None:
        data = self.clone()
        del data["$schema"]
        with self.assertRaises(contract.ContractError):
            contract.validate_contract(self.schema, data)

    def test_schema_reference_must_not_redirect(self) -> None:
        data = self.clone()
        data["$schema"] = "https://evil.example/schema.json"
        with self.assertRaises(contract.ContractError):
            contract.validate_contract(self.schema, data)

    def fixture_with_open_external_pr(self) -> dict:
        """Synthetic adversarial fixture independent of live PR availability."""
        data = self.clone()
        data["external_inbound"] = [{
            "repo": "JoanAbad82/mtgsynergy",
            "number": 999999999,
            "author": "offline-fixture",
            "title": "SYNTHETIC FIXTURE - NOT A REAL PULL REQUEST",
            "url": "https://github.com/JoanAbad82/mtgsynergy/pull/999999999",
            "review_status": "maintainer review recorded",
        }]
        return data

    def test_review_status_rejects_approval_claims(self) -> None:
        data = self.fixture_with_open_external_pr()
        data["external_inbound"][0]["review_status"] = "approved for merge"
        with self.assertRaises(contract.ContractError):
            contract.validate_contract(self.schema, data)

    def test_wrong_repo_number_link_fails(self) -> None:
        data = self.fixture_with_open_external_pr()
        data["external_inbound"][0]["number"] += 1
        with self.assertRaisesRegex(contract.ContractError, "URL does not match"):
            contract.validate_contract(self.schema, data)

    def test_unsafe_url_fails(self) -> None:
        data = self.fixture_with_open_external_pr()
        data["external_inbound"][0]["url"] = "https://not-github.example/JoanAbad82/mtgsynergy/pull/68"
        with self.assertRaises(contract.ContractError):
            contract.validate_contract(self.schema, data)

    def test_forged_extra_field_fails(self) -> None:
        data = self.fixture_with_open_external_pr()
        data["external_inbound"][0]["authorization"] = "push to main"
        with self.assertRaises(contract.ContractError):
            contract.validate_contract(self.schema, data)

    def test_wrong_inbound_owner_fails(self) -> None:
        data = self.clone()
        data["accepted_inbound"][0]["repo"] = "another-org/github-hidden-gems"
        item = data["accepted_inbound"][0]
        item["url"] = f"https://github.com/{item['repo']}/pull/{item['number']}"
        with self.assertRaisesRegex(contract.ContractError, "not owned"):
            contract.validate_contract(self.schema, data)

    def test_upstream_must_be_own_authorship(self) -> None:
        data = self.clone()
        data["accepted_upstream"][0]["author"] = "outsider"
        with self.assertRaisesRegex(contract.ContractError, "authored by profile owner"):
            contract.validate_contract(self.schema, data)

    def test_duplicate_across_sections_fails(self) -> None:
        data = self.clone()
        duplicate = deepcopy(data["accepted_inbound"][0])
        duplicate["review_status"] = "maintainer review recorded"
        data["external_inbound"].append(duplicate)
        with self.assertRaisesRegex(contract.ContractError, "Duplicate"):
            contract.validate_contract(self.schema, data)

    def test_non_integer_number_fails(self) -> None:
        data = self.clone()
        data["contribution_tasks"][0]["number"] = True
        with self.assertRaises(contract.ContractError):
            contract.validate_contract(self.schema, data)

    def test_oversized_category_fails(self) -> None:
        data = self.clone()
        first = data["contribution_tasks"][0]
        data["contribution_tasks"] = [
            {**first, "number": n, "url": f"https://github.com/{first['repo']}/issues/{n}"}
            for n in range(1, 6)
        ]
        with self.assertRaises(contract.ContractError):
            contract.validate_contract(self.schema, data)


    def test_five_upstream_entries_validate_and_six_fail(self) -> None:
        data = self.clone()
        self.assertEqual(len(data["accepted_upstream"]), 3)
        for repo, number, title in (
            ("StellarDevTools/stellar-devkit", 87, "Strict Doctor inputs"),
            ("bpftrace/bpftrace", 5398, "Runtime Rust uprobe test"),
        ):
            data["accepted_upstream"].append({
                "repo": repo,
                "number": number,
                "title": title,
                "url": f"https://github.com/{repo}/pull/{number}",
                "author": "JoanAbad82",
            })
        contract.validate_contract(self.schema, data)
        data["accepted_upstream"].append({
            "repo": "pic-standard/pic-standard",
            "number": 182,
            "title": "Signed evidence example",
            "url": "https://github.com/pic-standard/pic-standard/pull/182",
            "author": "JoanAbad82",
        })
        with self.assertRaises(contract.ContractError):
            contract.validate_contract(self.schema, data)

    def test_other_contract_limits_remain_unchanged(self) -> None:
        props = self.schema["properties"]
        self.assertEqual(props["accepted_upstream"]["maxItems"], 5)
        self.assertEqual(props["accepted_inbound"]["maxItems"], 3)
        self.assertEqual(props["external_inbound"]["maxItems"], 3)
        self.assertEqual(props["contribution_tasks"]["maxItems"], 4)
        self.assertEqual(props["schema"]["const"], "GITHUB_PORTFOLIO_PULSE_V1")

    def test_missing_discovery_link_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for filename in ("README.md", "AI_CONTEXT.md", "AGENT_INTERACTION.md"):
                text = (ROOT / filename).read_text(encoding="utf-8")
                if filename == "AI_CONTEXT.md":
                    text = text.replace(contract.SNAPSHOT_URI, "")
                (root / filename).write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(contract.ContractError, "AI_CONTEXT.md"):
                contract.check_discovery(root)


if __name__ == "__main__":
    unittest.main()
