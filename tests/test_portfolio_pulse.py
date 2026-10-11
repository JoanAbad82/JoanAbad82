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
