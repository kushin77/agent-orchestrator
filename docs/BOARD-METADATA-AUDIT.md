# Board metadata audit (labels · milestones · epic membership · chain edges)

> **Type:** program-management artifact — PMO lane, coordination only (no product
> code).
> **Measured:** 2026-09-17T16:26:16Z against `origin/master` =
> `2fa292cc0dee4a5910566706fb61c7ad69529521`.
> **Re-derive:** run the commands in §7. Every count below comes from one of them;
> none is asserted from memory.
> **Supersedes:** nothing. It complements `docs/BOARD-ATTACK-PLAN.md` (the
> milestone → epic → class *plan*, measured 2026-09-14 with 37 open issues); this
> file is the *live metadata* pass over the open board, and it does not rewrite the
> plan. `BOARD-ATTACK-PLAN.md` is superseded **in classification** by #358 and is
> left untouched.

## 0. The rule this pass serves

The conformance policy of record (`governance/conformance/policy.yaml`, printed by
`governance/conformance/cli.py policy`) makes four fields mandatory on every
in-scope issue — `class`, `type`, `priority`, `area` — and adds a fifth and sixth at
the rungs that claim more: `elite` also expects `gdc` **and** `pillar`, `enterprise`
also expects `gdc`. The conformance gate's scope is **open + milestoned**, so an
issue with no milestone is outside the gate entirely: it cannot fail, and it cannot
be reported either.

`epic` is a declared prefixed namespace, so `epic:<slug>` is legal metadata. The
convention this pass applies is the one the board already uses (17 issues carry
`epic:session-fleet`, 14 `epic:enterprise-workbook`, 13 `epic:vendor-compliance`, …):
label each issue with the slug of its **nearest ancestor epic root**, and self-label
the root.

## 1. The measured instant

The board was **33 open issues** at the start of this pass
(2026-09-17T16:14Z) and **42 open issues** at the measurement below: peer lanes
filed nine issues (#1156–#1164) while this pass ran. Two of the eight below-listed
gaps are the peer lane's own filings and were **deliberately left alone** (see §8).

| Quantity | Before (33 open) | After (same 33) |
|---|---|---|
| `class:` | 11 | **33** |
| `type:` | 12 | **33** |
| `priority:` | 7 | **33** |
| `area:` | 5 | **33** |
| `pillar:` | 11 | **31** |
| `gdc:` | 2 | **33** |
| milestone | **0** | **33** |
| `epic:<slug>` | 2 | **33** |

`pillar:` is 31 and not 33 **by design**: the two issues that declare
`class:enterprise` (#132, #133) expect only `gdc`, not `pillar`; every `elite` issue
in the set (11) carries both.

Across the whole live open board (42 issues at 2026-09-17T16:26:16Z):

| Field | Covering | Missing |
|---|---|---|
| `class` / `type` / `priority` | **42 / 42 each** | 0 |
| `area` | 41 / 42 | 1 — #1159 |
| `pillar` | 40 / 42 | 2 — #132, #133 (both `class:enterprise`, which does not require `pillar`) |
| `gdc` | 40 / 42 | 2 — #1157, #1159 |
| milestone | 34 / 42 | **8** — #1156, #1157, #1159, #1160, #1161, #1162, #1163, #1164 |
| `epic:<slug>` | 34 / 42 | the same 8 |

The 8 are all issues filed by **other lanes during this pass** (#1156–#1164).
They carry full `class/type/priority/area` because they were filed through
`governance/conformance/filing.py` — which is the point of that path — but they carry
no milestone or epic. Re-running §7 picks them up; closing them out is their owning
lanes' call, not this lane's (lane ownership, `AGENTS.md` rule 2).

## 2. Milestone → epic → class (after the pass)

A new milestone was created **only where no existing one names the work** (evidence
for that claim is in §4). `M31` is milestone number **19**, `M32` is number **20**.

| Milestone | Open | Content |
|---|---|---|
| **M26** — Session Fleet Operating Model | 6 | lifecycle/session close-out: #917, #973, #992, #1003, #1025, #1149 |
| **M29** — Module adoption & capability | 10 | surfaces/modules/vendor: #132, #133, #706, #878, #935, #936, #944, #966, #967, #968 |
| **M31** — Gate hygiene & master-red clearance *(new)* | 14 | delivery-control defects: #803, #877, #931, #1077, #1123, #1136, #1145, #1146, #1147, #1148, #1152, #1153, #1154, plus this audit (#1158) |
| **M24** — Enterprise Knowledge Index & Policy Normalization | 1 | #1028 (institutionalize a class of defect across RCA/pattern/template) |
| **Agent Single Pane of Glass (e2e)** | 2 | #946, #1082 |
| **M32** — ERPNext & DeepSeek FinOps *(new)* | 1 | #665 |
| *(none)* | 8 | the concurrently-filed #1156–#1164 |

Epic membership after the pass (`epic:<slug>` → the epic root it names):

| `epic:` slug | Open | Root | Root state |
|---|---|---|---|
| `epic:delivery-controls` | 13 | #803 | **open** |
| `epic:surfaces-elite` | 7 | #878 | **open** |
| `epic:session-fleet` | 6 | #160 | **closed** |
| `epic:git-governance` | 3 | #616 | **closed** |
| `epic:cron-automation` | 2 | #706 | **open** |
| `epic:vendor-compliance` | 2 | #125 | **closed** |
| `epic:erp-finops` | 1 | #665 | **open** |

## 3. Chain edges — declared, and the ones that must not be

The convention is the tooling's (`governance/dispatch/snapshot.py`): `Parent:` /
`Part-of:` yield a parent edge, `Blocked-by:` yields blockers, and an issue that
merely *mentions* an epic in prose has none. Measured over the refreshed snapshot
(603 issues, **367** carrying a declared chain edge):

| Quantity (open board) | Value |
|---|---|
| open issues with any declared chain edge | 24 |
| open issues declaring **no** parent | 18 |
| open issues declaring `Blocked-by:` | 0 |
| open issues with **no** chain edge at all | 18 |
| open issues whose declared parent is **CLOSED** (a dangling parent) | **7** |

The 7 dangling parents, named: #132 → #125, #133 → #125, #803 → #4, #877 → #616,
#935 → #731, #936 → #731, #946 → #607. The history is real — each was a genuine
child of an epic that has since closed — and the plan already recorded the same
shape (21 of 37 open issues at 2026-09-14).

**Thirteen chain edges were declared** in this pass, and **every one points at an
OPEN root**: `Part-of: #803` on #931, #1077, #1082, #1123, #1136, #1145, #1146,
#1147, #1153, #1154 (the delivery-control cluster), and `Part-of: #878` on #966,
#967, #968 (the surface cluster).

**Why no edge was declared for the remaining clusters — this is the load-bearing
finding.** The lifecycle cluster (#917, #973, #992, #1003, #1025, #1149) descends
from epic #160 and the git-governance cluster (#877, #944, #1028) from epic #616;
both roots are **closed**. `governance/dispatch/order.py` refuses any issue whose
declared parent is closed:

```
REASON_EPIC_CLOSED — #<n> declares Parent #<epic>, which is closed
```

so declaring `Part-of: #160` / `Part-of: #616` would not fill a metadata gap — it
would make those nine issues **permanently ineligible for every lane**, which is the
opposite of the intent. The epic column for those clusters is therefore satisfied by
the `epic:<slug>` label, and the *root* is the thing that needs attention: either it
reopens, or the children are re-parented to an open root. That is reported, not
silently "fixed" by writing a dangling edge.

## 4. What was created, and the evidence that it was needed

**Milestones (2 new, numbers 19 and 20).** The 18 pre-existing milestones name: the
KB/shared-frontend deliveries, M24 knowledge index, M25 per-repo fleet & CTO overlay,
M26 session-fleet operating model, the SPoG pane, M27 paperclip boundary, M28
peer-module integration, M29 module adoption (diagrams · code-indexing ·
monitoring), M30 enterprise chat. **None of them names a gate/verify/landing control
program**, and **none names ERPNext + DeepSeek FinOps**. The 13 delivery-control defects (plus
this audit issue) and #665 were the two clusters with no home, so:

- milestone **19** = `M31 - Gate hygiene & master-red clearance`
- milestone **20** = `M32 - ERPNext & DeepSeek FinOps (ledger-faithful subscription
  engine)`

**Labels (4 created).** `epic:surfaces-elite` (#878), `epic:delivery-controls`
(#803), `epic:cron-automation` (#706), `epic:git-governance` (#616) — one per open or
recently-active epic root that had no slug. A fifth, `epic:control-coverage`, was
created and then **deleted** in the same pass when the cluster was re-pointed from
the closed root #873 to the open root #803; it has no holders.

**One label removed.** `class:iac` was removed from #878, which declared
`class:elite` **and** `class:iac`. The conformance checker refuses a claim that
names two rungs (`class-ambiguous`) once the issue is in scope — and #878 only
entered the scope *because this pass gave it a milestone*. Before the removal the
gate read `conformance: FAIL (1 error(s), 1 warning(s))`; after it,
`conformance: OK (34 item(s) conform, 1 deviation(s) reported)`, rc 0.

## 5. Proof — the repo's own consumers, before and after

| Consumer | Before | After |
|---|---|---|
| `governance/conformance/cli.py check` | `OK (0 item(s) conform, 1 deviation)` — scope `open+milestoned` matched **0** issues | `OK (34 item(s) conform, 1 deviation)`, rc 0 |
| `governance/dispatch/cli.py status` | `CANNOT-ASSESS — snapshot-stale (125.3m > 15m)`, rc 2 | `rc 0`, active milestone `M29`, frontier `#132` |
| `governance/dispatch/cli.py audit` | — | rc 0 |
| `governance/pmo/cli.py report` | `OK — no finding over 603 ticket(s)` | `OK — no finding over 678 ticket(s)` |
| `governance/pmo/cli.py raid` | `OK — no finding over 603 ticket(s)` | `OK — no finding over 678 ticket(s)` |
| `bash scripts/check-pmo-rollup.sh` | `OK — 5 view(s) derived offline, 7 control(s) exercised` | `OK` (unchanged) |
| `snapshot --from-github` | 575 issues committed, 2h old, stale | **603** issues, 367 with declared chain edges |

The ticket count rose 603 → 678 because the ticket graph is derived from the chain
edges: the 13 declared edges are new relations the projection now carries.

## 6. Findings

**F1 — the gate is scope-limited to `open + milestoned`, so 8 open issues are
invisible to it.** `conformance check` reports `scope-mismatch 8 open issue(s) carry
no milestone and were not classified in this run`. Six of the eight would conform
once milestoned; #1157 and #1159 are also missing `gdc`, and #1159 `area`. This is
the same shape the plan recorded
as F3 — a gate whose scope is a subset of the board reports green on a board it did
not read.

**F2 — a dangling parent silently removes an issue from the fleet, and `status` does
not say so.** 7 open issues declare a closed parent. `eligible()` refuses them
`epic-closed` — but `frontier()` does not apply that check, so `dispatch status`
reports **#132** (which declares the closed #125) as the active milestone's frontier
while `claim --issue 132` would refuse it. The two functions disagree about the same
issue; the refusal is right and the frontier is the bug.

**F3 — the conformance gate reads a committed artifact, so its verdict is a function
of when the artifact was refreshed.** Measured twice in this pass: with the snapshot
refreshed at 16:24:43Z the gate was `FAIL (class-ambiguous #878)`; the label was
fixed at 16:25:20Z but the gate still read `FAIL` until the snapshot was refreshed
again at 16:25:50Z. The gate is honest — it never failed open — but "fix the label,
re-run the gate" is not sufficient; you must also re-refresh the board it reads.

**F4 — the board's metadata decays as fast as it is repaired.** The open count moved
33 → 42 in the twelve minutes this pass ran, and every one of the nine new issues
arrived un-milestoned and un-epic'd. A one-shot metadata pass is a snapshot, not a
control; the durable fix is at the filing path (`filing.py`), which already derives
`class/type/priority/area/gdc` but not milestone or epic.

**F5 — the filing default names a label that does not exist.** `policy.yaml`'s
`filing.defaults.area` is `governance`, but the label vocabulary has no
`area:governance` (the closest are `area:board`, `area:standards`). A filing that
omits `--declare area=` therefore derives a label GitHub will create on demand,
silently widening the vocabulary. (Filed independently by another lane as #1160
while this pass ran; recorded here because the audit measured it too.)

## 7. Reproduce every number

```bash
# 1. The live open board with labels, milestone and body.
gh issue list --repo kushin77/agent-orchestrator --state open --limit 400 \
  --json number,title,labels,milestone,body > /tmp/open.json

# 2. Refresh the snapshot the gate and the claim path read, then read it back.
python3 governance/dispatch/cli.py snapshot --from-github
python3 -c 'import json;d=json.load(open(".board/snapshot.json"));print(d["generated_at"],len(d["issues"]))'

# 3. The conformance verdict and the policy it grades against.
python3 governance/conformance/cli.py policy
python3 governance/conformance/cli.py check

# 4. The PMO views and the dispatch verdict.
python3 governance/pmo/cli.py report
python3 governance/pmo/cli.py raid
python3 governance/dispatch/cli.py status
python3 governance/dispatch/cli.py audit
bash scripts/check-pmo-rollup.sh

# 5. Chain-edge statistics over the refreshed snapshot (the repo's own parser is
#    governance/dispatch/snapshot.py; this reads what it wrote).
python3 - <<'PY'
import json
d=json.load(open(".board/snapshot.json"))
op=[i for i in d["issues"] if i["state"]=="OPEN"]; by={i["number"]:i for i in d["issues"]}
print("open", len(op))
print("no parent", sum(1 for i in op if not i["parent"]))
print("dangling parent", sum(1 for i in op if i["parent"] and by.get(i["parent"],{}).get("state")=="CLOSED"))
print("no edge at all", sum(1 for i in op if not i["parent"] and not i["blocked_by"]))
PY
```

## 8. Provenance and deviations

- **The claim was refused, and that is disclosed verbatim.** With the snapshot
  refreshed so the gate could actually answer, `governance/dispatch/cli.py claim
  --issue 1158 --agent pmo-sme --lane pmo` answered:

  ```
  claim: snapshot age 0.0m (threshold 15m)
  claim REFUSED: out-of-epic-pooled — #1158 is outside the active epic #665
  (parked in .board/pool.jsonl; promoted just-in-time by a brain directive when an
  active-epic child declares it as a blocker)
  ```

  This work was ordered by an operator directive, not a board item, and no chain
  edge was fabricated to admit it. The refusal is recorded, not worked around.
- **A milestone was assigned from each issue's own measured subject, not inherited
  from its epic.** Where an epic root has a milestone (#160 → M26) the child usually
  matches it; where the subject differs (#1148 is a *gate* defect under a *cron*
  epic) the issue's subject wins. The mapping is a judgement and is fully listed in
  §2 by issue number so it can be reviewed or corrected.
- **No dangling chain edge was created.** See §3: adding one would make the issue
  ineligible (`order.py` `REASON_EPIC_CLOSED`). The nine affected issues carry an
  `epic:<slug>` label and their closed root is reported as the gap to resolve.
- **Peers' issues were not touched.** #1156–#1164 were filed by other lanes during
  this pass; #1156 is the concurrent `platform-sme` lane's own artifact. This lane did
  not edit their labels, milestones or bodies.
- **`docs/BOARD-ATTACK-PLAN.md` was not edited** — it is the plan, this is the
  measurement. Its classification is superseded by #358 and left as the historical
  record.
- **`.board/snapshot.json` is refreshed and committed here**, deliberately, as the
  measured artifact the gate reads; the earlier PMO convention (refresh to measure,
  revert before committing, `BOARD-ATTACK-PLAN.md` §9) is not followed in this lane
  because the operator directive names the snapshot as a deliverable.
