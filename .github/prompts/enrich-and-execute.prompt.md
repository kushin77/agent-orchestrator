---
description: "CMR context-first execution role prompt. Goal-first. Enrich context and verify compliance BEFORE acting, then route by risk, execute, validate, and close out with evidence. Fill {{...}} fields, then execute."
harvested_from:
  - "kushin77/vscode-memory@.github/prompts/enrich-and-execute.prompt.md"
  - "kushin77/Gov-AI-Scout@.copilot-instructions"
---

# ROLE: ENRICH-AND-EXECUTE — {{task_id}}

## Connections

- **Owner-lane:** qa-sme
- **Class:** class
- **Connects-to:** consumes=none; called-by=none; gates=none
- **Env:** none
- **Updated-by:** qa-sme (2026-09-12)
- **Landed-by:** 99f4eec

## Goal
Execute `{{task}}` with full context and compliance verified **up front**.
Success = the task is done, every claim is evidenced, and no CMR rule was
discovered broken after the fact.

## Step 0 — Pre-execution compliance verification (before any file is touched)
- Check the golden rules that apply to the task: `GOLDEN-RULES.md`, `AGENTS.md`,
  and the conformance gate `guardrails/check-conformance.sh`. If a rule blocks
  this work (secrets, self-merge, `terraform apply`, lane ownership, NG4/NG6),
  stop and report it — do not work around it.
- State the compliance surface for this task: which rules bind it and how the
  finished work will prove compliance (e.g., `make verify`, conformance signal).

## Step 1 — Enrich context
- Read the canonical docs before re-deriving anything: `AGENTS.md`,
  `docs/ARCHITECTURE.md`, the issue + epic (`board/epics/`), lane ownership in
  `fleet/MANIFEST.tsv`. Cite what you read; never re-read files the canonical
  docs already cover.
- If a context source is unavailable, report it explicitly: "X unavailable —
  proceeding with Y" — never silently drop context.

## Step 2 — Check state and lane
- Confirm the current lane's `owns` globs; list the exact files you may touch
  and the files owned by other lanes (never edit them).
- Note in-flight state (open PRs, parallel lanes, existing ledgers) before
  planning, so the plan doesn't collide.

## Step 3 — Route by risk
- Apply the routing rule (see `guardrails/agents/route-by-risk.agent.md`):
  `fast` (single file, no risk keywords) · `deep` (multi-file/refactor) ·
  `strict` (destroy/delete/production/secret/deploy/migration).
- State the chosen path and why. For `deep`/`strict`, write the plan first:
  Files / Commands / Validation / Risk level + reason.

## Step 4 — Execute
- Follow the chosen path and all applicable `guardrails/instructions/*`.
- Match existing style; smallest focused diff; no `TODO`/`FIXME`/debug
  leftovers; no hardcoded values that belong in env/GSM.

## Step 5 — Validate
- Run the narrowest check that proves correctness, then `make verify` (or the
  issue's `Verify:` command). Fix every failure; never leave a red gate.

## Step 6 — Close out
- Working tree clean or changes atomic; conventional commit message if
  committing is in scope; never commit/push/merge your own work (NG2).
- Report: context used, compliance surface verified, path chosen + why, checks
  run with output, and what changed.

## Output expectations
- Which compliance rules bind the task and how they were verified.
- Which context was enriched and from which canonical files.
- Which execution path was selected and why.
- Which checks ran and their exact outcome (paste output).
- If blocked: exactly what is missing and how to unblock — never an unverified
  "done".

No-questions doctrine: apply `docs/DEFAULTS.md`; escalate only per GR-22 (secrets / apply / merge / irreversible).
