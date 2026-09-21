# Vendor-compliance gap #133 — `kushin77/googleworkspace`

**Status: RECORDED, NOT FIXED.** Issue #133 stays **open**. This document is the
in-repository declaration of the gap, its measured evidence, and its owners. It
exists because the issue is filed on this board but its payload lives in another
repository — the cross-repo boundary case frozen in
[`CROSS-REPO-EXECUTION-BOUNDARY.md`](CROSS-REPO-EXECUTION-BOUNDARY.md) — so
nothing here can close it, and closing it on its stated gate would be a false
green (§5.1 of that contract).

## 1. Why this issue must not be closed on its own completion gate

#133's completion gate reads:

> This issue closes only after the relevant repo has no open compliance drift in
> the CMR hygiene and vendor-drift gate outputs.

The "relevant repo" is `kushin77/googleworkspace`, not this one. Under the
boundary rule (§1) this repository may not enable branch protection, enable
Dependabot, add guardrail files or change dependency pins in another repository;
under NG4 the only sanctioned output of observing a peer's defect is a direction
issue on that peer's board. The gate therefore requires actions this repository is
forbidden to take. Per §5.1 of the contract:

> A gate that cannot be satisfied by the work available is not a completion
> criterion — it is a prohibited action wearing a completion criterion's clothes.

**Disposition: quarantine by name, with the tracking issue** (§5.2), and record
the residue and its owners. That is what this document does.

## 2. The six items, each measured from its own source of truth (2026-09-20)

| # | item | measured now | owner | verdict |
|---|---|---|---|---|
| 1 | branch protection | live `branches/main` → `"protected": true` | — | **setting clean** |
| 2 | Dependabot + alerts | `.github/dependabot.yml` present; vulnerability alerts enabled; open alerts **0** | — | **config clean** |
| 3 | guardrail files | `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `.github/copilot-instructions.md` all present | — | **clean (GR-9)** |
| 4 | PR backlog | **35 open PRs, 14 `DIRTY`**, plus Dependabot PR **#302** | `kushin77/googleworkspace` (owner) | **DRIFT — open** |
| 5 | dependency drift `saas-rbac.rbac` | `semver: "vendored"` pin vs catalog `versions.latest = v2.4.0`; `classify_pin()` rejects any non-SemVer pin | CMR hub (policy) | **DRIFT — open** |
| 6 | hygiene + drift gates | both re-run scoped to this repo → **rc 1** | the above | **NOT green** |

Items 1–3 were asserted red in the issue's original "Finding summary", which was
read from generated CMR snapshots dated 2026-09-07. They are clean live. The
issue's premise is therefore **partly stale**, and re-measuring each item was the
first task.

### 2.1 The live measurements

```
$ gh api repos/kushin77/googleworkspace/branches/main --jq '"protected=\(.protected)"'
protected=true
$ gh api repos/kushin77/googleworkspace/branches/main/protection \
      --jq '"enforce_admins=\(.enforce_admins.enabled) pr_reviews=\(.required_pull_request_reviews!=null) status_checks=\(.required_status_checks.contexts)"'
enforce_admins=false pr_reviews=false status_checks=null

$ gh pr list --repo kushin77/googleworkspace --state open --limit 500 --json number,mergeStateStatus
open PRs: 35
states: {'CLEAN': 21, 'DIRTY': 14}
DIRTY: [275, 271, 270, 266, 265, 263, 260, 258, 256, 254, 252, 251, 248, 245]
dependabot: [302]

$ for n in 1012 1015 1016; do gh api repos/kushin77/CMR/issues/$n --jq '"#\(.number) \(.state)"'; done
#1012 open    #1015 open    #1016 open

$ gh api repos/kushin77/CMR/commits/main --jq '.sha[0:8]'
c7834e99                       # hub tip unchanged since 2026-09-14T22:09:37Z
```

Note the branch-protection row: protection is present, but thin — no required
status checks, no required reviews, no rulesets, `enforce_admins: false`. The CMR
signal tests HTTP 200 only, so it **cannot** distinguish a minimal posture from a
standard one. That is `CANNOT-ASSESS`, by name, not a pass.

## 3. Residue and owners

| id | residue | owner | route |
|---|---|---|---|
| R1 | `semver: "vendored"` catalog pin → the `dependency` drift finding, which cannot clear by itself | **CMR hub** | [CMR#1012](https://github.com/kushin77/CMR/issues/1012) (open) |
| R2 | stale `catalog/repos/repo-inventory.json` → a false-positive `branch-protection` governance finding | **CMR hub** | [CMR#1015](https://github.com/kushin77/CMR/issues/1015) (open) |
| R3 | `controller/failure.tsv` rows that can never retire (no `resolve` verb) | **CMR hub** | CMR#1015 |
| R4 | hygiene/ledger row-shape fail-open (`if len(p) < 8: continue` drops every data row) | **CMR hub** | [CMR#1016](https://github.com/kushin77/CMR/issues/1016) (open) |
| R5 | 35 open PRs, 14 `DIRTY`, 1 Dependabot PR | **`kushin77/googleworkspace`** | direction issue [#303](https://github.com/kushin77/googleworkspace/issues/303) (open) |
| R6 | the declared protection posture (`standard`) is verified by no gate | **repo owner / policy** | human call — `CANNOT-ASSESS` |

R5's direction issue already exists on the target board and names this issue:

```
kushin77/googleworkspace#303  [direction] CMR vendor compliance: clear the PR backlog
                              and act on the owner-approval hold (blocks kushin77/agent-orchestrator#133)
labels cmr:direction,cmr:request   state open
```

Under §1/§2 the handoff is therefore **made**. What remains is the peer's backlog
and the hub's four residues — none of them reachable from this checkout.

## 4. The in-repo defect this measurement exposed

The board's own boundary quarantine
(`governance/board/boundary-baseline.json`) is the mechanism by which #133 is
"quarantined by name with the issue that tracks it" (§5.2). It was measured and
found **deficient**, and the deficiency is *not* fixed here — it is recorded and
tracked by [#1631](https://github.com/kushin77/agent-orchestrator/issues/1631).

**4.1 The tracker is dead.** All 22 quarantine entries named
`"tracked_by": "#358"`. Issue #358 **closed 2026-09-14T17:47:45Z**. §5.2 requires
the item to be quarantined *with the issue that tracks it*; a closed issue cannot
be that tracker.

**4.2 The gate cannot see that, because its input is rotted.**
`scripts/check-cross-repo-boundary.sh` resolves tracker liveness from the
**committed** `.board/boundary-snapshot.json`, whose `generated_at` is
**2026-09-14T03:35:16Z** — 14 hours *before* #358 closed — so it still reads
`#358 state=OPEN` and reports:

```
boundary: OK — 225 issue(s), 22 quarantined legacy finding(s), no boundary violation
```

Patching only that one entry to its **live** state and re-running the same gate:

```
boundary: NOT-OK — #133 excused for foreign-repo-declaration but its tracking issue #358 is closed; retire the quarantine entry
…
boundary: NOT-OK — 22 finding(s)/stale quarantine(s) across 225 issue(s)          [rc=1]
```

The check is capable of failing — its own negative control (c) provokes exactly
this shape — but it cannot see that its real input has gone stale. It **fails
open on a rotted snapshot.**

**4.3 The rot is much larger than the tracker.** Refreshing the committed snapshot
with its own verb (`python3 governance/board/cli.py export-boundary`) moves it from
**225 to 836 issues** and the same gate then reports **32 findings** — including
`self-parent` findings on #126–#137's surviving children and on #703, #1189,
#1199, #1262–#1267, #1276, #1328, #1331, #1341, #1350, #1366, #1382, #1402–#1410,
#1459, #1472 and #1593. The green above is therefore green **only** because the
input predates 611 issues of board history.

The refresh is deliberately **not** shipped here: it reddens `make verify`
fleet-wide for findings that are not #133's, and a board-artifact refresh must not
ride in a feature lane. Fixing the input is #1631's job, and it is the reason the
re-anchor of §4.1 could not be landed either — a `tracked_by` naming an issue the
snapshot does not contain resolves to `unknown`, which the checker treats as
stale, which is rc 1.

## 5. What could not land, and the measurement that decides it

The obvious in-lane repair is to re-anchor the two live entries to a live tracker
and retire the entries that excuse nothing. **Neither half is landable while the
snapshot is rotted, and both were attempted and measured rather than assumed.**

**5.1 A re-anchor reddens the gate.** `tracked_by: "#1631"` — the successor
tracker filed for §4 — does not exist in the 2026-09-14 snapshot, so the checker
resolves it to `unknown`, which its own rule treats as stale:

```
boundary: NOT-OK — #133 excused for foreign-repo-declaration but its tracking issue #1631 is unknown; retire the quarantine entry
boundary: NOT-OK — 22 finding(s)/stale quarantine(s) across 225 issue(s)          [rc=1]
```

That rule is correct and must not be weakened — an unknown tracker *is* an
unverified lease. The defect is the input, not the rule.

**5.2 Retiring the nine closed children's entries reddens the gate too** — and
this is the measurement that made the scope clear. The 9 children (#126–#129,
#131, #134–#137) closed at 2026-09-14T20:32–21:35Z, i.e. **after** the snapshot
was taken, so the snapshot still records them `OPEN`. They are therefore *still
findings*, and their 18 entries are the only thing excusing them:

```
$ # a baseline with only the 4 live entries, against the committed snapshot
boundary: NOT-OK — #137 [foreign-repo-declaration] body declares repo diagrams, not kushin77/agent-orchestrator: …
… 18 findings
boundary: NOT-OK — 18 finding(s)/stale quarantine(s) across 225 issue(s)          [rc=1]
```

The premise "a closed issue is not a finding" is true of the **live** board and
false of the committed snapshot. The shrink that §5.2 of the contract expects is
real work, and it is #1631's work: it needs the input refreshed first.

**5.3 So this change lands no gate input.** `governance/board/boundary-baseline.json`
is unchanged, and the gate's verdict is unchanged — deliberately. A change that
made `make verify` red for 18–32 findings that are not this issue's would be a
regression dressed as a fix.

## 6. Verify

```
$ bash scripts/check-cross-repo-boundary.sh        # unchanged verdict, unchanged input
boundary: OK — 225 issue(s), 22 quarantined legacy finding(s), no boundary violation
  OK    committed boundary snapshot is clean
  OK    injected non-quarantined child is refused (rc 1)
  OK    bodyless snapshot is CANNOT-ASSESS (rc 2, never OK)
  OK    a closed tracker makes the quarantine stale (rc 1)

$ bash scripts/check-docs.sh                                                      # rc=0

$ # the two provocation arms of §4.2 and §5.2, run against copies, never the committed files
$ python3 … run_boundary_check(<snapshot with #358 closed>, boundary-baseline.json)
boundary: NOT-OK — … 22 finding(s)/stale quarantine(s) across 225 issue(s)        [rc=1]
$ python3 … run_boundary_check(boundary-snapshot.json, <baseline with only the 4 live entries>)
boundary: NOT-OK — … 18 finding(s)/stale quarantine(s) across 225 issue(s)        [rc=1]
$ python3 … run_boundary_check(<refreshed 836-issue snapshot>, boundary-baseline.json)
boundary: NOT-OK — … 32 finding(s)/stale quarantine(s) across 836 issue(s)        [rc=1]
```

Read together: the gate is `OK` **as committed** and `NOT-OK` in all three
truthful variants. The green is an artefact of the input's age — which is the
whole of §4.

## 7. Not fixed here (named, not silently dropped)

- **#133 is open** and will stay open: its gate is unachievable from this
  repository (§1). Closing it would be the false green §5.1 forbids.
- **R1–R4** are CMR hub residues — CMR#1012, CMR#1015, CMR#1016, all open.
- **R5** is `kushin77/googleworkspace`'s backlog, handed off via its #303.
- **§4** — the boundary gate failing open on a rotted snapshot, and the dead
  tracker — is tracked by [#1631](https://github.com/kushin77/agent-orchestrator/issues/1631).
