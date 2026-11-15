"""Offline adversarial tests for Portfolio Pulse."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import portfolio_pulse as pulse

def issue(owner="JoanAbad82", repo="mtgsynergy", n=67, kind="issues", author="JoanAbad82",
          state="open", title="Verified task"):
    item = {"html_url":f"https://github.com/{owner}/{repo}/{kind}/{n}","number":n,
            "title":title,"state":state,"repository_url":f"https://api.github.com/repos/{owner}/{repo}",
            "user":{"login":author,"type":"User"}}
    if kind == "pull":
        item["pull_request"] = {"merged_at":"2026-10-11T18:50:20Z" if state=="closed" else None}
    return item

def fetch_for(accepted=(), merged=(), inbound=(), tasks=(), private=(), reviews=None):
    groups=dict(accepted_upstream=accepted,accepted_inbound=merged,external_inbound=inbound,contribution_tasks=tasks)
    reviews = reviews or {}
    def fetch(url, token):
        if url.startswith("/search/issues?"):
            from urllib.parse import parse_qs
            query=parse_qs(url.split("?",1)[1])["q"][0]
            key=next(k for k,q in pulse.SEARCH.items() if q==query)
            return {"items":list(groups[key]),"incomplete_results":False}
        if "/pulls/" in url and url.endswith("/reviews?per_page=100"):
            repo, tail = url.removeprefix("/repos/").split("/pulls/",1)
            return reviews.get(f"{repo}#{tail.split('/')[0]}", [])
        if url.startswith("/repos/"):
            repo=url.removeprefix("/repos/")
            return {"private":repo in private,"full_name":repo}
        raise AssertionError("Nonallowlisted request")
    return fetch

class PortfolioPulseTests(unittest.TestCase):
    def snapshot(self):
        return pulse.collect(fetch=fetch_for(
            accepted=[issue("pic-standard","pic-standard",182,"pull","JoanAbad82","closed",
                           "docs: explain Ed25519 signatures"),
                      issue("PRIVATE-CORP","hidden",2,"pull","JoanAbad82","closed","Internal roadmap")],
            merged=[issue("JoanAbad82","github-hidden-gems",33,"pull","CRYPTONIKAV","closed",
                          "Add valid/invalid RUN_CANDIDATES_V1 fixtures")],
            inbound=[issue("JoanAbad82","mtgsynergy",68,"pull","jay23606"),
                     issue("JoanAbad82","mtgsynergy",69,"pull","dependabot[bot]")],
            reviews={"JoanAbad82/mtgsynergy#68": [{"user":{"login":"JoanAbad82"},"state":"COMMENTED"}]},
            tasks=[issue("JoanAbad82","master-security-review",10)],
            private=("PRIVATE-CORP/hidden",)))
    def test_external_merged_and_two_real_inbound(self):
        a=self.snapshot()
        self.assertEqual([v["number"] for v in a["accepted_upstream"]],[182])
        self.assertEqual([v["number"] for v in a["accepted_inbound"]],[33])
        self.assertEqual([v["number"] for v in a["external_inbound"]],[68])
        self.assertEqual(a["external_inbound"][0]["review_status"],"maintainer review recorded")
        self.assertEqual([v["number"] for v in a["contribution_tasks"]],[10])
        self.assertNotIn("PRIVATE-CORP",json.dumps(a))
    def test_open_pr_review_state_is_not_merge_approval(self):
        snap = self.snapshot()
        section = pulse.render(snap)
        self.assertIn("**Merged community contributions**", section)
        self.assertIn("github-hidden-gems #33", section)
        self.assertIn("**merged**", section)
        self.assertIn("mtgsynergy #68", section)
        self.assertIn("**maintainer review recorded**", section)
        self.assertNotIn("**review pending**", section)

    def test_only_actual_merged_pr_accepted(self):
        not_merged = issue("JoanAbad82","github-hidden-gems",33,"pull","CRYPTONIKAV","closed")
        not_merged["pull_request"]["merged_at"] = None
        self.assertIsNone(pulse.parse_issue(not_merged,"accepted_inbound"))
        self.assertIsNone(pulse.parse_issue(issue("JoanAbad82","mtgsynergy",68,"pull","jay23606"),"accepted_inbound"))
        self.assertIsNone(pulse.parse_issue(issue("JoanAbad82","mtgsynergy",77,"pull","JoanAbad82","closed"),"accepted_inbound"))

    def test_unauthorized_or_dismissed_reviews_do_not_count(self):
        reviews={"JoanAbad82/mtgsynergy#68":[
            {"user":{"login":"outside-reviewer"},"state":"APPROVED"},
            {"user":{"login":"JoanAbad82"},"state":"DISMISSED"},
        ]}
        snap=pulse.collect(fetch=fetch_for(inbound=[issue("JoanAbad82","mtgsynergy",68,"pull","jay23606")],reviews=reviews))
        self.assertEqual(snap["external_inbound"][0]["review_status"],"no maintainer review recorded")

    def test_incomplete_reviews_fail_closed(self):
        def fail_reviews(url,token):
            if "/reviews?" in url:return {"unexpected":"data"}
            return fetch_for(inbound=[issue("JoanAbad82","mtgsynergy",68,"pull","jay23606")])(url,token)
        with self.assertRaises(pulse.CuratorError):
            pulse.collect(fetch=fail_reviews)

    def test_reject_forged_api_repo_identity(self):
        i=issue()
        i["repository_url"]="https://api.github.com/repos/not-owner/not-repo"
        self.assertIsNone(pulse.parse_issue(i,"contribution_tasks"))
    def test_reject_wrong_url_and_closed_issue(self):
        self.assertIsNone(pulse.parse_issue(issue(state="closed"),"contribution_tasks"))
        x=issue();x["html_url"]="https://evil.example/repo/issues/7"
        self.assertIsNone(pulse.parse_issue(x,"contribution_tasks"))
        x=issue();x["html_url"]="https://github.com/JoanAbad82/mtgsynergy/issues/67#trick"
        self.assertIsNone(pulse.parse_issue(x,"contribution_tasks"))
    def test_claimed_issue_hidden_from_open_tasks(self):
        pr=issue("JoanAbad82","github-hidden-gems",33,"pull","CRYPTONIKAV")
        pr["body"]="Addresses #29."
        snapshot=pulse.collect(fetch=fetch_for(
            inbound=[pr],
            tasks=[issue("JoanAbad82","github-hidden-gems",29),
                   issue("JoanAbad82","master-security-review",10)]))
        self.assertEqual([x["number"] for x in snapshot["contribution_tasks"]],[10])
    def test_private_owned_and_bots_excluded(self):
        f=fetch_for(inbound=[issue("JoanAbad82","private-tools",17,"pull","collaborator"),
                             issue("JoanAbad82","mtgsynergy",21,"pull","dependabot[bot]")],
                    private=("JoanAbad82/private-tools",))
        self.assertEqual(pulse.collect(fetch=f)["external_inbound"],[])
    def test_html_and_markdown_inert(self):
        snap=self.snapshot()
        snap["external_inbound"][0]["title"]="<script>alert(1)</script> [click](https://evil.example) @everyone"
        result=pulse.render(snap)
        self.assertNotIn("<script>",result)
        self.assertIn("&lt;script&gt;",result)
        self.assertIn("＠everyone",result)
        self.assertNotIn("[click](https://evil.example)",result)
    def test_bounded_profile_readme_idempotent(self):
        before="# Profile\n\n## Selected work\n\nPreserve forever.\n"
        block=pulse.render(self.snapshot())
        after=pulse.insert_bounded_section(before,block)
        self.assertEqual(after.count(pulse.START),1)
        self.assertEqual(pulse.insert_bounded_section(after,block),after)
        self.assertTrue(after.endswith("## Selected work\n\nPreserve forever.\n"))
    def test_markers_fail_closed(self):
        block=pulse.render(self.snapshot())
        for before in ("README without anchor", pulse.START+"a"+pulse.END+pulse.START, pulse.END+"bad"+pulse.START):
            with self.subTest(before=before):
                with self.assertRaises(pulse.CuratorError):
                    pulse.insert_bounded_section(before,block)
    def test_snapshot_stable_without_timestamp(self):
        one=json.dumps(self.snapshot(),sort_keys=True,ensure_ascii=False)
        two=json.dumps(self.snapshot(),sort_keys=True,ensure_ascii=False)
        self.assertEqual(one,two)
        self.assertNotIn("fetched_at",one)

    def test_five_recent_upstream_merged_prs_are_deduped_and_sorted(self):
        specs = [
            ("maths", "moodle-qtype_stack", 1954, "2026-11-09T16:11:41Z"),
            ("rakeshnitb23", "toolism", 164, "2026-11-07T11:07:41Z"),
            ("ishandutta2007", "Top-AI-repos", 47, "2026-11-05T14:24:54Z"),
            ("StellarDevTools", "stellar-devkit", 87, "2026-11-03T06:59:36Z"),
            ("bpftrace", "bpftrace", 5398, "2026-10-22T21:04:13Z"),
            ("pic-standard", "pic-standard", 182, "2026-10-10T13:49:37Z"),
        ]
        items = []
        for owner, repo, number, merged_at in specs:
            entry = issue(owner, repo, number, "pull", "JoanAbad82", "closed",
                          f"Verified upstream contribution {number}")
            entry["pull_request"]["merged_at"] = merged_at
            items.append(entry)
        # GitHub search is ordered by updated_at, which need not be merged_at.
        # The sixth (older) PR and duplicate must not displace newer merges.
        scrambled = [items[i] for i in (4, 0, 5, 3, 1, 2, 0)]
        expected = [
            "https://github.com/maths/moodle-qtype_stack/pull/1954",
            "https://github.com/rakeshnitb23/toolism/pull/164",
            "https://github.com/ishandutta2007/Top-AI-repos/pull/47",
            "https://github.com/StellarDevTools/stellar-devkit/pull/87",
            "https://github.com/bpftrace/bpftrace/pull/5398",
        ]
        a = pulse.collect(fetch=fetch_for(accepted=scrambled))
        self.assertEqual([row["url"] for row in a["accepted_upstream"]], expected)
        b = pulse.collect(fetch=fetch_for(accepted=list(reversed(scrambled))))
        self.assertEqual(a["accepted_upstream"], b["accepted_upstream"])
        rendered = pulse.render(a)
        self.assertEqual(rendered.count("StellarDevTools/stellar-devkit #87"), 1)
        self.assertEqual(rendered.count("bpftrace/bpftrace #5398"), 1)
        self.assertNotIn("pic-standard #182", rendered)

    def test_upstream_merge_timestamp_is_strictly_validated(self):
        malformed = issue("StellarDevTools", "stellar-devkit", 87, "pull",
                          "JoanAbad82", "closed")
        for invalid in ("2026-02-31T00:00:00Z", "2026-11-03T06:59:36+02:00",
                        "invalid", 999):
            with self.subTest(value=invalid):
                malformed["pull_request"]["merged_at"] = invalid
                got = pulse.collect(fetch=fetch_for(accepted=[malformed]))
                self.assertEqual(got["accepted_upstream"], [])

    def test_upstream_sort_ties_have_stable_url_order(self):
        older = issue("ExampleOrg", "alpha", 4, "pull", "JoanAbad82", "closed")
        newer = issue("ExampleOrg", "zeta", 9, "pull", "JoanAbad82", "closed")
        older["pull_request"]["merged_at"] = "2026-11-03T06:59:36Z"
        newer["pull_request"]["merged_at"] = "2026-11-03T06:59:36Z"
        first = pulse.collect(fetch=fetch_for(accepted=[older, newer]))
        second = pulse.collect(fetch=fetch_for(accepted=[newer, older]))
        self.assertEqual(first["accepted_upstream"], second["accepted_upstream"])
        self.assertEqual([x["number"] for x in first["accepted_upstream"]], [9, 4])

    def test_only_accepted_upstream_limit_changes(self):
        self.assertEqual(
            pulse.LIMITS,
            {"accepted_upstream": 5, "accepted_inbound": 3,
             "external_inbound": 3, "contribution_tasks": 4},
        )

    def test_truncated_upstream_search_fails_closed(self):
        upstream = issue("StellarDevTools", "stellar-devkit", 87,
                         "pull", "JoanAbad82", "closed")
        delegate = fetch_for(accepted=[upstream])

        def truncated(url, token):
            result = delegate(url, token)
            if url.startswith("/search/issues?"):
                from urllib.parse import parse_qs
                query = parse_qs(url.split("?", 1)[1])["q"][0]
                if query == pulse.SEARCH["accepted_upstream"]:
                    result["total_count"] = 101
            return result

        with self.assertRaisesRegex(pulse.CuratorError, "Truncated accepted upstream search"):
            pulse.collect(fetch=truncated)

    def test_api_call_allowlist(self):
        with self.assertRaises(pulse.CuratorError):
            pulse.github_get("/user/repos",None)
        with self.assertRaises(pulse.CuratorError):
            pulse.github_get("/repos-private",None)
    def test_incomplete_results_abort(self):
        def broken(url,token):return {"items":[],"incomplete_results":True}
        with self.assertRaises(pulse.CuratorError):
            pulse.collect(fetch=broken)
if __name__=="__main__":unittest.main()
