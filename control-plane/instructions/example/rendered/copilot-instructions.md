# Example governed repository — GitHub Copilot mirror

GitHub Copilot working in this repository. This file is a **generated mirror** of the canonical instruction source: the same rules and precedence appear in AGENTS.md, CLAUDE.md and .cursorrules. Never edit by hand — regenerate.

A minimal canonical instruction set demonstrating the model-agnostic instruction layer: governed platform and agent-pack layers with tenant local rules supplied through the frozen override contract, rendered to every per-tool mirror.

## Precedence

When instructions conflict the highest-precedence rule wins; a rule in a
lower-precedence layer never overrides a rule in a higher-precedence layer.

1. platform — Platform governed layer (governed)
2. agent-pack — Agent-pack governed layer (governed)
3. local — Local tenant layer (tenant-owned)

## Rules

### platform — Platform governed layer (governed)

- **issue-first** — Work is tracked in an issue before it is done; every change carries a reference to the issue it serves.
- **verify-before-done** — A task is done only when its verification gate is green and the actual output is reported; never an unverified claim.
- **no-secrets** — Credentials and tokens come from the environment or a secret manager; nothing is hardcoded, committed, or echoed to logs.
- **stay-in-lane** — Make the smallest focused change for your lane; no unrelated edits, no unfinished markers, no debug leftovers.
- **no-direct-push** — Never push directly to the protected branch; changes land through a reviewable pull request with a green gate.

### agent-pack — Agent-pack governed layer (governed)

- **scope-to-tenant** — A session token scopes every governed-agent call to exactly one tenant; there is no fallback to another tenant.
- **allowlist-tools** — Governed agents call tools on an explicit allowlist; an unknown tool is a denial, never a silent pass.

### local — Local tenant layer (tenant-owned)

- **acme-feature-branches** — Feature work lands on short-lived branches and merges via pull request only.
- **acme-reviewer-required** — Every pull request is reviewed by a second pair of eyes before merge.

## Canonical source

example-governed-repo@1.0.0 — rendered from the agent-orchestrator model-agnostic instruction layer.
Repository: acme/example-governed-repo — Acme's governed agent repository.

<!-- ao-instructions: {"schema":"ao.instructions.mirror/v1","canonical":{"id":"example-governed-repo","version":"1.0.0"},"tool":"copilot-instructions.md","rules":["issue-first","verify-before-done","no-secrets","stay-in-lane","no-direct-push","scope-to-tenant","allowlist-tools","acme-feature-branches","acme-reviewer-required"],"precedence":["platform","agent-pack","local"]} -->
