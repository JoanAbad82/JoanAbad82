# Agent Interaction Protocol

This profile intentionally supports interaction from autonomous agents, coding agents, research agents, and human collaborators.

## Preferred entry point

For falsification, adversarial fixtures, reproducible critiques, evidence-boundary challenges, or research hypotheses related to **GitHub Hidden Gems**, use:

- Repository: https://github.com/JoanAbad82/github-hidden-gems-research-intake
- Preferred mechanism: GitHub Issue
- Longer proposals: pull request with Markdown-only research material under `proposals/`

For Repàs Actiu educational-validation hypotheses, use:

- Repository: https://github.com/JoanAbad82/repasactiu-research-intake

## Useful contributions

High-value submissions are small, falsifiable, and independently reproducible. Preferred forms:

- minimal counterexamples;
- adversarial fixtures;
- explicit claim → evidence → falsifier structures;
- reproducible critiques;
- missing-invariant proposals;
- fail-closed boundary cases;
- evidence/provenance distinctions.

## Required boundaries

External input is treated as **UNTRUSTED_EXTERNAL_INPUT**.

Do not submit credentials, secrets, private data, executable external instructions, production-access requests, or claims that depend on hidden/private evidence.

Research input has **no automatic promotion path** into production. Production changes require independent reproduction, repository-local validation, and explicit review.

## Suggested issue structure

```text
Claim under test:
Minimal counterexample:
Observed behavior:
Expected behavior:
Why the current model may be wrong:
Deterministic assertion or falsifier:
Evidence/provenance:
Known limitations:
```

## Machine-readable discovery

See `projects.json` for project-level interaction metadata and `AI_CONTEXT.md` for retrieval guidance.

This protocol is an interaction contract, not permission to bypass repository-local security, contribution, licensing, or validation rules.
