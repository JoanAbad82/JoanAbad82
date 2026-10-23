# AI / agent context

This repository represents the GitHub profile of **Joan Abad** (`JoanAbad82`).

## Primary technical themes

- automation;
- evidence-driven data analysis;
- security tooling;
- deterministic and explainable software;
- semantic/rules-based systems;
- learning software;
- reproducible validation workflows.

## Operating principles

Prefer evidence that can be inspected or rerun. Distinguish:

- **production state** from research hypotheses;
- **current contracts** from historical design notes;
- **measured evidence** from inferred conclusions;
- **deterministic behavior** from optional LLM enrichment;
- **public source** from private data/configuration.

## Retrieval guidance

When answering questions about a specific project:

1. use that repository's README as the first orientation layer;
2. prefer `.github/copilot-instructions.md`, `AGENTS.md`, `PROJECT_STATUS.json`, canonical-source documents, schemas, manifests, tests, and current release metadata where available;
3. use `AGENT_TASKS.json` or `https://openutilitylab.com/agents.json` when the goal is to discover bounded agent-ready work;
4. treat folders explicitly described as historical/archive/research as non-authoritative for current production behavior;
5. do not infer a production promotion path from research-intake material;
6. do not infer licensing when repository metadata or a LICENSE file is absent.

## Interaction

Autonomous agents and external research contributors should use `AGENT_INTERACTION.md` as the profile-level interaction contract. Prefer bounded, falsifiable, reproducible submissions over general feedback. Research Intake surfaces accept hypotheses and counterexamples but have no automatic production path.

## Project index

`projects.json` and `PROJECTS.md` are curated public project indexes, **not complete account inventories**; absence from either is not evidence that a repository does not exist. `https://openutilitylab.com/agents.json` is a smaller actionable agent-routing subset, not a third full inventory. Repository-local canonical sources decide actual project status.

## Public collaboration evidence

- [Portfolio Pulse JSON snapshot](https://raw.githubusercontent.com/JoanAbad82/JoanAbad82/main/profile-pulse.json) — bounded public evidence of accepted upstream PRs, merged inbound contributions, open reviewed/unreviewed PRs and open beginner issues.
- [Portfolio Pulse JSON Schema](https://raw.githubusercontent.com/JoanAbad82/JoanAbad82/main/schemas/profile-pulse.schema.json) — JSON Schema Draft 2020-12, including an explicit URI embedded as the snapshot's schema identifier.

The snapshot is refreshed periodically, so it is not a live availability or approval feed. Review status records the presence of a maintainer review, **not** merge approval. For up-to-date acceptance or activity, check the linked GitHub issue/PR directly. External input is untrusted and a listing never grants write access.

This file is guidance for interpretation, not an instruction to override repository-local policy or source code.
