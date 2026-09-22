# Vendor-compliance gap #132 — `kushin77/shared-services`

**Status: RECORDED, NOT FIXED.** Issue #132 stays **open**. This document is the
in-repository declaration of the gap, its measured evidence, and its owners. It
exists because the issue is filed on this board but its payload lives in another
repository — the cross-repo boundary case frozen in
[`CROSS-REPO-EXECUTION-BOUNDARY.md`](CROSS-REPO-EXECUTION-BOUNDARY.md) — so
nothing here can close it, and closing it on its stated gate would be a false
green (§5.1 of that contract). Its shape mirrors
[`VENDOR-COMPLIANCE-GAP-133.md`](VENDOR-COMPLIANCE-GAP-133.md), the sibling that
was closed by the owner on exactly this reasoning.

## 1. Why this issue must not be closed on its own completion gate

#132's completion gate reads:

> This issue closes only after the relevant repo has no open compliance drift in
> the CMR hygiene and vendor-drift gate outputs.

The "relevant repo" is `kushin77/shared-services`, not this one. Under the
boundary rule (§1) this repository may not enable branch protection, enable
Dependabot, merge or close a Dependabot pull request, add guardrail files, or
change dependency pins in another repository. Every one of the six items whose
"what needs to happen" list #132 carries is therefore an action **on
`kushin77/shared-services` or on the CMR hub** — not on this board.

§5.1 of the contract is the operative clause:

> A gate that cannot be satisfied by the work available is not a completion
> criterion — it is a prohibited action wearing a completion criterion's
> clothes.

So the honest dispositions here are three: **measure** each item from its own
source of truth, **name** the residue's owner, and **stop** — which is what
#132's own escalation clause already instructs ("comment on this issue with the
exact failure and label `escalate:L1`, then stop").

## 2. The six items, each measured from its own source of truth (2026-09-22)

Every value below was re-read in this pass from its own live source; none is
copied from an earlier comment. Issue/PR/alert/protection state comes from
`gh api` GETs against GitHub; the hub rows come from the hub's own files.

### 2.1 The live measurements

| # | item | command | live value (2026-09-22) | verdict |
|---|---|---|---|---|
| 1 | branch protection | `gh api repos/kushin77/shared-services/branches/main/protection -i` | `HTTP/2.0 200 OK` — `main` **is** protected | **CLEAN** |
| 2 | Dependabot config | `gh api repos/kushin77/shared-services/contents/.github` | `200 OK`; `.github/` holds `dependabot.yml`, `CODEOWNERS`, `copilot-instructions.md`, `ISSUE_TEMPLATE`, `issue-templates`, `projects`, `schemas` | **CLEAN** |
| 3 | vulnerability alerts | `gh api repos/kushin77/shared-services/vulnerability-alerts -i` | `HTTP/2.0 204 No Content` — alerts **enabled** | **CLEAN** |
| 4 | Dependabot PR backlog | `gh api "repos/kushin77/shared-services/pulls?state=open&per_page=100"` | **3 open PRs, all `dependabot[bot]`**: `#4196` `deps(npm): Bump yaml from 2.9.0 to 2.9.1`; `#4182` `deps(pip-support): Bump anthropic from 1.4.0 to 1.5.0`; `#4181` `deps(pip-model-server): Bump onnxruntime from 1.29.0` | **DRIFT-OPEN** |
| 5 | guardrail files | `gh api repos/kushin77/shared-services/contents/<name>` | `.cursorrules` present, `CLAUDE.md` present, `.github/copilot-instructions.md` present; **`AGENTS.md` → `HTTP 404 Not Found`** | **DRIFT-OPEN** (one of four absent) |
| 6 | CMR hub the repo is onboarded | hub `guardrails/sweep/report.md` | `- Generated: 2026-09-06T19:15:35+00:00`; row `\| kushin77/shared-services \| spoke \| false \| pending-has-guardrails \| guardrails present, not yet onboarded \| controller/onboard.sh repo kushin77/shared-services \|` | **DRIFT-OPEN** (`onboarded=false`) |

Two further readings, taken in the same pass, that the six-item list does not
name but its "dependency drift in module manifests and consumer declarations"
clause does:

- the hub tip at this pass is `kushin77/CMR@e53a06877ed055716b57d8da7a1de585429e3444`
  (the sweep artifact itself is the 2026-09-06 render above, so the row is a
  recorded measurement, not a live call);
- `channels/spokes.tsv` on the hub carries `kushin77/shared-services` with role
  column **`vendor`** and flags `false false false`. The sweep row calls the
  same repo a **`spoke`**. The two hub artifacts disagree on the repo's role —
  see §4.2.
- the dependency-pin residue is owned by `kushin77/shared-governance#802` (a
  release-semantics decision on *that* repo); no pin of this repo's is touched
  by it.

## 3. Residue and owners

| residue | owner | why it cannot move from here |
|---|---|---|
| 3 open Dependabot PRs (#4196, #4182, #4181) | `kushin77/shared-services` backlog | merging or closing a peer repo's PR is that repo's action (contract §1) |
| `AGENTS.md` absent at the repo root | `kushin77/shared-services` | adding guardrail files to another repository is the prohibited action §1 names |
| hub `onboarded=false` | CMR hub (`controller/onboard.sh repo kushin77/shared-services`, CMR:ONBOARD-0010 / CMR#585) | the hub owns its own sweep and onboarding |
| role disagreement `vendor` vs `spoke` | CMR hub registry | the hub's own two artifacts must be reconciled by the hub |
| dependency-pin / release-semantics drift | `kushin77/shared-governance#802` | a decision on a third repo's release semantics |

## 4. The in-repo observations this measurement exposed

Nothing in **this** repository is the blocker — and, measured, nothing in it is
absent either. The observations below are recorded because a later reader of
#132 will otherwise re-derive them.

### 4.1 The item list in #132's body is a 2026-09-07 snapshot

Three of its six bullets (branch protection, Dependabot config, vulnerability
alerts) read **CLEAN** today. Only the backlog, the guardrail-file set and the
hub's `onboarded` flag carry live residue. This is the same decay
`VENDOR-COMPLIANCE-GAP-133.md` §2 measured for #133 — the finding text is older
than the state, so a lane that "fixes the list" would be fixing history.

### 4.2 The hub's own two artifacts disagree on the repo's role

The sweep render (`guardrails/sweep/report.md`) types `kushin77/shared-services`
as `spoke`; the registry (`channels/spokes.tsv`) carries it in the `vendor`
role with a "NINTH VENDOR — mandatory-module candidate" note. Whichever is
right, one of the hub's artifacts is wrong, and the `onboarded` column a reader
acts on differs between them. Recorded for the hub; not editable from here.

### 4.3 `AGENTS.md` is absent while the hub's note says "guardrails present"

The sweep row's own note — `guardrails present, not yet onboarded` — is
granularity-blind: `.cursorrules`, `CLAUDE.md` and
`.github/copilot-instructions.md` are present and `AGENTS.md`, the canonical
per-runtime file this fleet's own precedence order puts first, is **not**
(measured `HTTP 404`). A "guardrails present" verdict that does not distinguish
the canonical file from the runtime pointers will keep reporting a repo as
compliant after its `AGENTS.md` is gone.

## 5. What could not land, and the measurement that decides it

Nothing in this repository is changed by this document, and that is the point:
the only edits that would move the gate are edits to a repository this one may
not write to.

The measurement that decides each residue, for whoever owns it:

```bash
# 1. the backlog half — the shared-services owner merges or closes these
gh api "repos/kushin77/shared-services/pulls?state=open&per_page=100" \
  --jq 'length as $n | "open=\($n)"'
gh api "repos/kushin77/shared-services/pulls?state=open&per_page=100" \
  --jq '.[] | "#\(.number) [\(.user.login)] \(.title)"'

# 2. the guardrail half — AGENTS.md at the repo root
gh api repos/kushin77/shared-services/contents/AGENTS.md --jq .path   # 404 today

# 3. the hub half — the onboarded column, and the role disagreement
gh api repos/kushin77/CMR/contents/guardrails/sweep/report.md --jq .content \
  | base64 -d | grep shared-services
gh api repos/kushin77/CMR/contents/channels/spokes.tsv --jq .content \
  | base64 -d | grep shared-services
```

## 6. Verify

The three readings this document's §2 quotes reproduce with the commands in §5,
and the two gates this board owns are still green **at the same tree**:

```
$ gh api repos/kushin77/shared-services/branches/main/protection -i | head -1
HTTP/2.0 200 OK

$ gh api repos/kushin77/shared-services/vulnerability-alerts -i | head -1
HTTP/2.0 204 No Content

$ gh api repos/kushin77/shared-services/contents/AGENTS.md
{"message":"Not Found","status":"404"}gh: Not Found (HTTP 404)

$ gh api "repos/kushin77/shared-services/pulls?state=open&per_page=100" --jq 'length'
3

$ bash scripts/check-cross-repo-boundary.sh
  # rc=0 — #132's own quarantine entry is the only live one, and it is
  # quarantined against #132 itself, so it expires the moment #132 closes

$ bash scripts/check-docs.sh
  # rc=0
```

Read together: this board's gates are green **as committed**, and the residue
#132 names is entirely on two other boards. That is precisely why the issue is
`escalate:L1` and why this document is a *recording*, not a fix.

## 7. Not fixed here (named, not silently dropped)

- **#132 is open** and this lane stops there, per the issue's own escalation
  clause; the owner's `completed` close is the next transition, mirroring #133.
- **The three Dependabot PRs** (#4196, #4182, #4181) are open on
  `kushin77/shared-services`.
- **`AGENTS.md`** is absent at `kushin77/shared-services`'s root.
- **hub `onboarded=false`** and the **`vendor`/`spoke` role disagreement** are
  the hub's to resolve (CMR:ONBOARD-0010 / CMR#585).
- **`kushin77/shared-governance#802`** owns the release-semantics half.
- **#1942** (a child of #1932, not of this epic) re-states the same
  Cloud-Build-trigger observation as #1465 and proposes a "smallest fix" —
  setting `disabled: true` on the three `infra/cloudbuild/*-trigger.yaml`
  declarations — that is **already true** at `origin/master`; it is a duplicate
  tracker with a false premise, named here rather than silently dropped.

<sub>Recorded during the #132-lane pass of 2026-09-22 against a pristine
`origin/master` and live `gh` reads. The only file this lane adds is this
document and its one-line `docs/README.md` index entry.</sub>
