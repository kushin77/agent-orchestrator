# Golden Rules — agent-orchestrator (CMR vendor-module ratification)

This repository is a **governed CMR vendor module** (`CMR:ONBOARD-0012`, issue
#98). This root file is a **ratification pointer** — it records the repo's
relationship to the hub golden rules without restating or clobbering this
repo's own doctrine.

## Precedence

1. **`AGENTS.md`** (repo root) is the canonical, model-agnostic instruction
   file for working in this repo; `CLAUDE.md` / `.cursorrules` /
   `.github/copilot-instructions.md` are thin per-runtime mirrors.
2. **`docs/GOLDEN-RULES.md`** is this repo's **canonical product spine**
   (AO-GR-1..AO-GR-20) — it governs how this product is built and what the
   platform enforces at runtime. Do not edit that file from this lane; this
   root file defers to it for product rules.
3. **Hub golden rules** (`kushin77/CMR` `GOLDEN-RULES.md`, GR-1..GR-18) bind
   where this repo's own doctrine (this file's precedent chain) is **silent**.
   This repo vendors `kushin77/CMR` as a pinned submodule (`vendor/CMR`) and
   consumes its standards; the hub rules are the default wherever a repo-local
   rule does not say otherwise.

## Ratification (GR-9 guardrail set)

The repo ships the full AI-guardrail seed set at its root: `AGENTS.md`
(canonical), `CLAUDE.md`, `.cursorrules`, and
`.github/copilot-instructions.md` (all deferring to root `AGENTS.md`), plus
this `GOLDEN-RULES.md` ratification. AI output clears the same gates as human
output (hub GR-9; AO-GR-9).

## Repo-local divergences (deliberate, recorded)

Where this repo differs from the hub defaults, the repo-local rule wins and is
recorded here:

- **Default branch is `master`** (not `main`). `master` is protected by
  convention; every change lands via a PR against `master` (AO-GR-11).
- **Owner autonomous-merge mandate (2026-09-07).** In this fleet an agent
  merges its own PR **only after green verification evidence** (`make verify` /
  the PR's `Verify:` command, actual output). Verification evidence replaces
  the human reviewer; it never replaces the requirement for evidence, and
  failing work is never merged (AO-GR-3 / AO-GR-11).
- **`make verify` is the gate of record** (GR-12): shell syntax + YAML + JSON +
  docs + secrets + feature-flags + cloudbuild + terraform, honest and
  no-false-green. `make gate` / `make merge-gate` add policy-schema, guard
  negative-controls, per-suite tests, and drift.
- **Legacy `.github/workflows/` content** (a pre-ratification artifact) is
  slated for GR-15 (no-GitHub-Actions) disposition by CMR's standards bundle
  at catalog registration. It is not deleted in-repo; see issue #98.

## Debugging (local-code-first)

Follow the **local-code-first** doctrine (hub GR-17 / AO delivery spine): when
debugging, search this repo's own code and docs first (grep / usage search /
tests) before any other step. For cross-repo or org-wide knowledge across the
`kushin77` fleet, query the **CMR indexer KB** instead of guessing or
re-cloning.

## Verification

```bash
make verify       # gate of record — run before every PR and every merge
make help         # list all targets
bash -n <file>.sh # shell syntax for any new script
python3 -m json.tool module.json   # module manifest validity
```

## Pointer index

- Product spine (canonical): `docs/GOLDEN-RULES.md` (AO-GR-1..20)
- Repo doctrine: `AGENTS.md` → `docs/ARCHITECTURE.md` →
  `docs/EXECUTION-PLAN.md` → `docs/GOVERNANCE.md`
- Hub standards: `vendor/CMR/GOLDEN-RULES.md` (GR-1..18), `vendor/CMR/docs/`
- Onboarding contract: issue `kushin77/agent-orchestrator#98`
  (`CMR:ONBOARD-0012`)
