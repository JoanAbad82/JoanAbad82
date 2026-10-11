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

    def test_review_status_rejects_approval_claims(self) -> None:
        data = self.clone()
        data["external_inbound"][0]["review_status"] = "approved for merge"
        with self.assertRaises(contract.ContractError):
            contract.validate_contract(self.schema, data)

    def test_wrong_repo_number_link_fails(self) -> None:
        data = self.clone()
        data["external_inbound"][0]["number"] += 1
        with self.assertRaisesRegex(contract.ContractError, "URL does not match"):
            contract.validate_contract(self.schema, data)

    def test_unsafe_url_fails(self) -> None:
        data = self.clone()
        data["external_inbound"][0]["url"] = "https://not-github.example/JoanAbad82/mtgsynergy/pull/68"
        with self.assertRaises(contract.ContractError):
            contract.validate_contract(self.schema, data)

    def test_forged_extra_field_fails(self) -> None:
        data = self.clone()
        data["external_inbound"][0]["authorization"] = "push to main"
        with self.assertRaises(contract.ContractError):
            contract.validate_contract(self.schema, data)

    def test_wrong_inbound_owner_fails(self) -> None:
        data = self.clone()
        data["accepted_inbound"][0]["repo"] = "another-org/github-hidden-gems"
        data["accepted_inbound"][0]["url"] = "https://github.com/another-org/github-hidden-gems/pull/33"
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
