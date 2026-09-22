# Governance doctrine consistency audit

**Date:** 2026-09-21
**Epic:** #1919
**Related (already tracked, not re-filed):** #1890 (security-header contract
restated 7 places), #1891 (commit-message rule enforced twice), #1892 (debt
ledger reimplemented 3 ways)

## Scope

Fine-tooth-comb audit of every governance/rules/agent-instruction/config
document for contradiction, staleness, and redundancy, after five days of
heavy PR activity including two same-day doctrine rewrites: #1789 (GR-5
policy reversal, flag-gated-OFF -> enabled-by-default) and #1714 (merge-train
/ gate-of-record retirement).

## Inventory (Step 1)

| File | Lines |
|---|---|
| AGENTS.md | 423 |
| CLAUDE.md | 49 |
| .cursorrules | 37 |
| docs/GOLDEN-RULES.md | 776 |
| docs/EXECUTION-PLAN.md | 483 |
| docs/GOVERNANCE.md | 363 |
| governance/*/README.md | 28 files |
| registry/personas/cards/*.yaml | persona cards (iac-sme, gcp-gatekeeper-sme, etc.) |
| .claude/settings*.json | not present in this checkout |

## P0 — most important finding

**#1789 (today) flipped GR-5 in AGENTS.md and docs/GOLDEN-RULES.md (AO-GR-6)
but did not propagate to at least seven other doctrine files**, including two
persona cards that dispatched agents load directly as guardrails
(`registry/personas/cards/iac-sme.yaml`, `gcp-gatekeeper-sme.yaml`). An agent
reading only one of these would enforce the retired "flag-gated OFF" default
against correctly-shipped, enabled-by-default work — a genuine, freshly
created contradiction. See #1920-1924.

## Contradictions filed (P0)

| Issue | File:line | Contradicts |
|---|---|---|
| #1920 | .cursorrules:30 | AGENTS.md:77-88, AO-GR-6 |
| #1921 | docs/GOVERNANCE.md:170 | AO-GR-6 |
| #1922 | docs/ARCHITECTURE.md:94, docs/CROSS-REPO-EXECUTION-BOUNDARY.md:75 | AO-GR-6 |
| #1924 | registry/personas/cards/iac-sme.yaml:46,54; gcp-gatekeeper-sme.yaml:65 | AO-GR-6 |
| #1925 | docs/EXECUTION-PLAN.md:458 | AO-GR-6 |

Checked and confirmed **consistent**: merge-train/gate-of-record retirement
(#1714) — docs/PR-RUNNER.md, docs/PR-QUEUE.md, docs/RELEASE-PLAN.md,
governance/merge/README.md, governance/landing/README.md all carry an
identical "Superseded 2026-09-21" banner. GR-5's non-flag-default meaning
(IaC/no-console-clicks) in governance/dispatch/README.md and
governance/landing/README.md is unaffected by #1789 and still correct.

## Stale references (P1)

| Issue | Finding |
|---|---|
| #1926 | docs/RELEASE-PLAN.md:60 — inside the "kept as history" body but outside the banner's stated topic scope (banner covers merge-train only, not GR-5) |
| #1927 | ~15 secondary docs (gap-analyses, operator/observability runbooks, docs/README.md index) still describe flag-gated-OFF as live rather than historical |

## Redundancy (P2)

| Issue | Finding |
|---|---|
| #1928 | GR-5 rule identity numbered three unreconciled ways: AGENTS.md "GR-5" (bundled IaC+flag-default), docs/GOLDEN-RULES.md AO-GR-5 (IaC only) + AO-GR-6 (flag-default, separately numbered), and `scripts/check-terraform-iac.sh`'s embedded "GR-28". #1789 explicitly declined to reconcile, mapping "by content, not by label." |

## Not re-filed

Security-header restatement, commit-message double-enforcement, and
debt-ledger triplication are #1890/#1891/#1892 — out of scope here by
instruction.

## Resolution (closed 2026-09-22)

All 12 children of epic #1919 are closed. #1920/#1921/#1922/#1924/#1925
(P0, above) and #1993 (a second-wave P0 finding: the #1789 reversal had not
propagated to the machine authorities — tagging, conformance, rollout) are
doctrine findings from this audit. #1992 (~40 further prose surfaces, second
audit wave) and #1927/#1926 (P1, above) round out the doctrine/stale set.
#1928 (P2, above) is the rule-numbering redundancy. #2001 and #2015 are
reconcile-engine bugs surfaced by lanes working this epic's children, not
findings of this audit itself — listed here as closed under #1919, not
retrofitted into the tables above.

| Issue | Resolution |
|---|---|
| #1920 | closed via #1930 |
| #1921 | closed via #1930 |
| #1922 | closed via #1930 |
| #1924 | closed via #1930 |
| #1925 | closed via #1930 |
| #1926 | closed 2026-09-22 |
| #1927 | closed 2026-09-22 |
| #1928 | closed via #2019 |
| #1992 | closed 2026-09-22 |
| #1993 | closed via #1996 |
| #2001 | closed via #2016 |
| #2015 | closed via #2040 |
