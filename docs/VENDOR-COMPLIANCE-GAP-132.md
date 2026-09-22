# Vendor-compliance gap #132 — `kushin77/shared-services`

**Status: RECORDED, NOT FIXED.** Issue #132 stays **open**. This document is the
in-repository declaration of the gap, its measured evidence, and its owners. It
exists because #132 is filed on this board but its payload lives in another
repository — the cross-repo boundary case frozen in
[`CROSS-REPO-EXECUTION-BOUNDARY.md`](CROSS-REPO-EXECUTION-BOUNDARY.md) §1 — so
nothing here can close it, and closing it on its stated gate would be a false
green (§5.1 of that contract). This is the same shape as
[`VENDOR-COMPLIANCE-GAP-133.md`](VENDOR-COMPLIANCE-GAP-133.md) for #133.

## 1. Why this issue must not be closed on its own completion gate

#132's completion gate is owned by `kushin77/shared-services`'s board. Under §1
this repository may not enable branch protection, enable Dependabot, merge or
close a Dependabot pull request, add guardrail files, or change dependency pins
in another repository. The gate therefore requires actions this repository is
forbidden to take — per §5.1, "a gate that cannot be satisfied by the work
available is not a completion criterion."

**Disposition: quarantine by name (§5.2), record the residue and its owners.**
That is what this document does.

## 2. Measured (2026-09-22, each item from its own source of truth)

| item | live value | verdict |
|---|---|---|
| branch protection | `branches/main/protection` → `HTTP/2.0 200 OK` | CLEAN |
| Dependabot config | `.github/dependabot.yml` present | CLEAN |
| vulnerability alerts | `vulnerability-alerts` → `HTTP/2.0 204 No Content` | CLEAN |
| Dependabot PR backlog | 3 open, all `dependabot[bot]`: #4196, #4182, #4181 | DRIFT-OPEN |
| guardrail files | `.cursorrules`, `CLAUDE.md`, `.github/copilot-instructions.md` present; **`AGENTS.md` 404** | DRIFT-OPEN |
| CMR hub `onboarded` | sweep row `kushin77/shared-services \| spoke \| false \| …` — not yet onboarded | DRIFT-OPEN |

Two further observations: the hub's own artifacts disagree on the repo's role
(`guardrails/sweep/report.md` types it `spoke`, `channels/spokes.tsv` carries it
as `vendor`), and #1942 re-states the Cloud-Build-trigger observation with a
"smallest fix" that is already true at `origin/master`.

### 2.1 Reproduction commands

```
$ gh api repos/kushin77/shared-services/branches/main/protection -i | head -1
HTTP/2.0 200 OK
$ gh api repos/kushin77/shared-services/contents/.github/dependabot.yml --jq .path
.github/dependabot.yml
$ gh api repos/kushin77/shared-services/vulnerability-alerts -i | head -1
HTTP/2.0 204 No Content
$ gh api "repos/kushin77/shared-services/pulls?state=open&per_page=100" \
      --jq '[.[] | {n:.number,by:.user.login}]'
[{"by":"dependabot[bot]","n":4196},{"by":"dependabot[bot]","n":4182},{"by":"dependabot[bot]","n":4181}]
$ gh api repos/kushin77/shared-services/contents/AGENTS.md -i | head -1
HTTP/2.0 404 Not Found
$ gh api repos/kushin77/agent-orchestrator/issues/132 --jq .state
open
```

## 3. Residue and owners

| id | residue | owner | route |
|---|---|---|---|
| R1 | 3 open Dependabot PRs (#4196, #4182, #4181) unmerged | **`kushin77/shared-services`** | owner backlog — no direction issue filed by this lane |
| R2 | `AGENTS.md` missing (guardrail set incomplete, GR-9) | **`kushin77/shared-services`** | owner backlog |
| R3 | CMR hub sweep row `onboarded=false` for `shared-services` | **CMR hub** | hub onboarding backlog |
| R4 | role disagreement: `guardrails/sweep/report.md` says `spoke`, `channels/spokes.tsv` says `vendor` | **CMR hub** | hub data-consistency backlog |

None of these are reachable from this checkout: fixing R1/R2 means writing to
`kushin77/shared-services`, and R3/R4 mean writing to the CMR hub. Per §1 this
repository only records and files.

## 4. Verify

```bash
gh api repos/kushin77/agent-orchestrator/issues/132 --jq .state   # open
gh api "repos/kushin77/shared-services/pulls?state=open&per_page=100" --jq 'length'   # 3
gh api repos/kushin77/shared-services/contents/AGENTS.md          # 404
bash scripts/check-docs.sh && echo DOCS-OK
```
