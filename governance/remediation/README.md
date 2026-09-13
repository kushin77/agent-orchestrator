# Remediation — automated violator remediation issues (issue #142)

Policy drift is invisible until something turns it into work. This module
consumes findings from `governance/conformance` (issue #140) — and, by the
same duck-typed `Finding` contract, any future scanner — and generates
remediation-issue payloads: title, labels, summary, owner lane, corrective
steps, the policy reference and the evidence that proves the violation.
Repeated findings for the same `(code, subject)` merge into one issue with a
growing occurrence count instead of spawning duplicates, and issues that
breach a severity or SLA threshold are flagged for governance-board
escalation.

## Pipeline

```
governance/conformance (Finding) -> generator.build_issue -> generator.merge -> RemediationIssue
                                                                                       |
                                                                                  github.route
                                                                                       |
                                                                        gh issue create / comment / edit
```

* `remediation_model.py` — the domain model: severity ladder
  (`critical`/`high`/`medium`/`low`), SLA hours per severity, owner-lane
  routing by finding code, the `(code, subject)` dedup key, and
  `RemediationIssue` (labels, markdown `body()`, `escalate`).
* `generator.py` — `Finding` → `RemediationIssue` (`build_issue`), dedup/merge
  across a scan (`merge`), and the full pipeline (`generate`).
* `github.py` — the only network-touching module: search existing
  `remediation:auto`-labelled issues for the `<!-- remediation-key: ... -->`
  marker a prior run wrote, create or comment accordingly, and escalate
  (`route`). Takes an injectable `runner` so it is unit-tested without `gh`.
- `cli.py` — `scan` (offline: board conformance + change-set checks, write
  `.verify/remediation-report.json`) and `route` (scan, then create/update
  GitHub issues; `--apply` performs the `gh` calls, the default is dry-run).

## Severity, lane and SLA

| Finding code | Severity | Lane | SLA |
|---|---|---|---|
| `secret-exposure` | critical (always) | security | 24h |
| `iac-mandate-unmet` | critical (always) | platform | 24h |
| `dependency-missing` | error→high / warning→medium | qa | 72h / 168h |
| `class-*`, `classification-incomplete`, `scope-mismatch`, `policy-invalid` | error→high / warning→medium | governance | 72h / 168h |
| (default, unmatched) | error→high / warning→medium | governance | 72h / 168h |

A finding that keeps recurring (`occurrences >= 3`) escalates to the
governance board regardless of its own severity — "prevent repeated
violations" (issue #142 scope) means low-severity drift that never stops
still surfaces.

## Running it

```bash
python3 governance/remediation/cli.py scan                       # offline, writes the report
python3 governance/remediation/cli.py route --repo <owner/repo>  # dry-run: shows what would be created
python3 governance/remediation/cli.py route --repo <owner/repo> --apply  # creates/updates/escalates
make remediation-scan       # the "on changes" hook — run after any change
make remediation-dispatch   # the scheduled-audit hook — the ops runner invokes this on its cron
```

Deliberately **not** a new GitHub Actions workflow: `governance/conformance`'s
own IaC mandate fails any change set that adds one (fleet GR-15 — automation
stays code-native, driven by a Makefile target the ops runner invokes on
schedule, the same precedent as `knowledge-index-build`).

A real output sample — `python3 governance/remediation/cli.py scan` run
against this repo's own board — is committed at `samples/sample-report.json`
(the full report) and `samples/sample-issue-body.md` (one generated issue's
title, labels and body, as `gh issue create` would receive them).
