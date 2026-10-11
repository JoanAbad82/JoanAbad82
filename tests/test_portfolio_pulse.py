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
    return {"html_url":f"https://github.com/{owner}/{repo}/{kind}/{n}","number":n,
            "title":title,"state":state,"repository_url":f"https://api.github.com/repos/{owner}/{repo}",
            "user":{"login":author,"type":"User"}}

def fetch_for(accepted=(), inbound=(), tasks=(), private=()):
    groups=dict(accepted_upstream=accepted,external_inbound=inbound,contribution_tasks=tasks)
    def fetch(url, token):
        if url.startswith("/search/issues?"):
            from urllib.parse import parse_qs
            query=parse_qs(url.split("?",1)[1])["q"][0]
            key=next(k for k,q in pulse.SEARCH.items() if q==query)
            return {"items":list(groups[key]),"incomplete_results":False}
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
            inbound=[issue("JoanAbad82","mtgsynergy",68,"pull","jay23606"),
                     issue("JoanAbad82","github-hidden-gems",33,"pull","CRYPTONIKAV"),
                     issue("JoanAbad82","mtgsynergy",69,"pull","dependabot[bot]")],
            tasks=[issue("JoanAbad82","master-security-review",10)],
            private=("PRIVATE-CORP/hidden",)))
    def test_external_merged_and_two_real_inbound(self):
        a=self.snapshot()
        self.assertEqual([v["number"] for v in a["accepted_upstream"]],[182])
        self.assertEqual([v["number"] for v in a["external_inbound"]],[68,33])
        self.assertEqual([v["number"] for v in a["contribution_tasks"]],[10])
        self.assertNotIn("PRIVATE-CORP",json.dumps(a))
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
