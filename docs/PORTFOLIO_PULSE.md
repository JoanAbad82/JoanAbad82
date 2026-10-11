# Portfolio Pulse V1: GitHub profile visibility curator

Purpose: make the public profile useful and current for developers, agents,
and potential contributors. This is NOT click generation, bulk starring,
comment promotion, spam, or manufactured community activity.

Schedule: Wednesday at 07:23 UTC on the GitHub Actions cloud runner.
Also supports manual workflow_dispatch. No local computer required.
If there is no meaningful change, no commit is generated.

Inputs: GitHub public issue and pull request search, and repository visibility
metadata, fetched using HTTPS GET. The token is the repository-scoped GitHub
Actions credential, not a personal access token or paid model API.

Output: a clearly marked block in README.md and public profile-pulse.json.
The block lists at most three externally merged upstream pull requests, three
incoming open PRs by non-bot collaborators, and four starter issues that are
open and not linked to PRs according to GitHub search. Each item links to its
canonical repository URL. The curator never approves external code and calls
open PRs review pending. Private repository names are never included.
External text is sanitized before embedding in Markdown.

Safety: the only writable repository is JoanAbad82/JoanAbad82; only the two
output files can be committed. No external issues, comments, posts or PRs
are created. Normal fast-forward push only, no force pushes. Invalid GitHub
responses, incomplete searches and invalid README markers stop publication.
All core safety properties have offline unit tests.

Proof and interpretation: links prove observed GitHub public records, not
authenticity of an author's motivations, reputation or program correctness.
Weekly profile freshness may improve discoverability, but no measurable
increase in visits is guaranteed.

Evaluation: compare the separate traffic-audit measurements after 14 and 30
days with the historical baseline, plus human external PRs and genuinely
accepted upstream contributions. On 11 October 2026 the 11 public nonfork
repos had an aggregate 33 GitHub page views and 3435 clones over 14 days.
These are repository sums, not identifiable unique people or IA agents.
Two first-time external contributor PRs and two forks were observed in the
last seven days. Do not infer traffic sources from clone counts.

Limitations: Github Actions scheduled workflows run only on the default
branch and may be automatically disabled after 60 days with no repository
activity; cron jobs can be delayed. GitHub Search coverage and result
ordering are not real-time guarantees.

For maintenance, edit scripts/portfolio_pulse.py and its unit tests.
Never remove or duplicate the section markers in README.md.
