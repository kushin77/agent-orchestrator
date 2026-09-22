# EPIC #1254 closure — mechanical, not doctrinal

**Epic:** [#1254](https://github.com/kushin77/agent-orchestrator/issues/1254) —
*"mechanical, not doctrinal — the merge path, PR contract, and every rule that
broke this week become gates (#1145)"* (sibling of #1268, which closed in
`docs/EPIC-1268-CLOSURE.md`).

**What this document is.** The clause-by-clause closure evidence for the epic's
three acceptance clauses, measured against a pristine `origin/master` tree.
Every command below was run and its real output is quoted verbatim; nothing here
is asserted from memory. Two of the three clauses cannot be met as written, and
§1 and §3 say so with the measurement that shows it rather than restating the
clause as though it held.

**Audited tree.** `origin/master`
`091012c31a4dd0eb3d986f41ac6fd0ec4726b0d7` — *"docs(epic): closure record for
EPIC #1268 — mechanical cross-runtime coordination (#1969)"* — read from a lane
worktree cut from `origin/master` (`issue-1254-close`).

> **Read the baseline from a fresh worktree, not the shared checkout.** The
> shared checkout's working tree is stale; it reported the baseline as **40**
> entries with `measured_at 2026-09-21T19:10:00Z`, while the audited tree
> reports **46** entries with `measured_at 2026-09-21T21:42:27Z`. Every number
> in this document comes from the audited tree.

**Children.** All **22** declared children are `closed` (re-read live, one
`gh api` per issue): `#1189 #1199 #1262 #1263 #1264 #1265 #1266 #1267 #1268
#1276 #1301 #1328 #1331 #1341 #1343 #1350 #1366 #1382 #1402 #1404 #1407 #1410`
— **0 open of 22**. The epic body's own child table (dated 2026-09-19, *"7 open
/ 15 closed"*) is stale. Sibling epic **#1510** is a separate epic and remains
`open`; it is deliberately untouched by this lane.

---

## 1. Clause (a) — a body ending in `AI-assistance:` reds by name, and cannot be merged

> *A PR whose body ends in `AI-assistance:` shows a red `control-plane-verify`
> naming `commit-missing-ticket-trailer` and cannot be merged from the GitHub UI
> or `gh pr merge`.*

This clause has two halves. **The first holds; the second is unsatisfiable
today** — measured, not argued.

### 1a. Producer half — MET: the composed message is refused by name

`gh pr merge --squash` composes the landed commit message as
`"<title> (#N)\n\n<body>"`, so the *body's last paragraph* becomes the message's
last paragraph. The one shared predicate
(`governance/isolation/trailer.py`, which delegates to
`scripts/check-pr-contract.sh`) walks back from the end of the message to the
trailing trailer block. A body whose last paragraph is `AI-assistance:` has no
trailer block at its end, so the predicate refuses it.

Measured through `trailer.classify_commit` on the exact two-commit scratch-repo
idiom `scripts/check-squash-message.sh` itself uses, with the two composed
messages differing only in the position of the trailer paragraph:

```
$ bash /tmp/ao1254/pred.sh
=== (a) producer half: body whose LAST paragraph is AI-assistance: ===
--- last 3 lines of the composed message ---

    🤖 Generated with Copilot
    AI-assistance: Copilot (Relentless, flash/LOW)
  A (AI-assistance: LAST)                -> commit-missing-ticket-trailer

=== (b) second half: same body, trailer paragraph moved LAST ===
--- last 3 lines of the composed message ---

    Refs kushin77/agent-orchestrator#1254
    Closes #1254
  B (trailer paragraph LAST)             -> <clean>
```

Message **A** classifies `commit-missing-ticket-trailer`, by name. This is the
same verdict the real merge-path guard prints: `scripts/check-squash-message.sh
--pr <N>` renders the composed message and returns the predicate's finding
verbatim (it does not reimplement the rule).

**Verdict 1a: met.** The producer-side control refuses the bad shape by name.
It is enforced by the **landing drivers**, not by GitHub:

| invocation site | what it does |
|---|---|
| `scripts/pr-queue.sh` | pre-merge guard: refuses the PR by name before `gh pr merge` |
| `scripts/land.sh` (#1675) | the single-developer landing driver runs it first and refuses by name |
| `scripts/check-pr-queue-squash-guard.sh` | gate that *provokes* the refusal in apply mode |
| `scripts/verify.sh` | runs `check-squash-message.sh --self-test` as an advisory arm |

### 1b. GitHub-level half — UNSATISFIABLE TODAY: no required check exists

The clause's second half presumes a **required status check** on `master`, so
that GitHub itself refuses the merge. Measured live:

```
$ gh api repos/kushin77/agent-orchestrator/branches/master/protection --jq '.required_status_checks'
                                # <-- empty
$ echo "<-- RC=$?"
<-- RC=0
$ gh api repos/kushin77/agent-orchestrator/branches/master/protection --jq 'keys'
["allow_deletions","allow_force_pushes","allow_fork_syncing","block_creations","enforce_admins","lock_branch","required_conversation_resolution","required_linear_history","required_signatures","url"]
```

The protection endpoint answers **rc 0** (so this is a readable, protected
branch — not a 404), and its key set contains **no `required_status_checks`
key at all**. There is therefore no GitHub-side merge block, and the clause's
second half cannot be satisfied as written. Clause (a) also names the venue
`control-plane-verify`, which is retired (§5).

**Verdict 1b: unsatisfiable today** — adjudicated in §5, not restated.

### 1c. The epic's Fix (1) as literally written is not on the audited tree

The epic's *Fix* item 1 named `infra/cloudbuild/verify.yaml` as the site for
"before `make verify`, run `bash scripts/check-squash-message.sh --pr
$_PR_NUMBER`". Measured on the audited tree, that wiring is **absent**:

```
$ grep -n "squash" infra/cloudbuild/verify.yaml
                # <-- no match
```

This is consistent with the fleet's later decisions rather than a regression:
GitHub Actions is disabled fleet-wide (GR-15), the Cloud Build verify venue was
retired by the single-developer method (#1698) and the Cloud Build retirement
work (#1415/#1465), and the producer control moved to the **landing drivers**
(§1a) that a lane actually runs. The control is live; its original host is not.

---

## 2. Clause (b) — the trailer moved last goes green, and the landed commit classifies `None`

> *The same PR with the trailer paragraph moved last goes green and merges; the
> landed commit classifies `None` under
> `governance.isolation.trailer.classify_commit`.*

### 2a. The same body with the trailer paragraph last passes

Message **B** in §1a — byte-identical to message **A** except that the trailer
paragraph `Refs kushin77/agent-orchestrator#1254` / `Closes #1254` is moved to
the very end — classifies `<clean>` (`None`). The only difference between the
refusal and the pass is the **position** of the trailer paragraph.

### 2b. A real landed commit classifies `None`

```
$ python3 -c "...governance.isolation.trailer.classify_commit(repo,'091012c3...')"
classify_commit(091012c31a4d) -> None
$ git log -1 --format='%B' 091012c3 | tail -4
Closes #1268
Refs kushin77/agent-orchestrator#1268

Co-authored-by: sim <sim@local>
```

`classify_commit(repo, sha)` runs the shared predicate over exactly one commit
(`--range <sha>^..<sha> --enforcement-gate <sha>^`) and returns the finding
code, or `None` when the commit passes. The real landed squash merge of PR
#1969 — the precedent this closure follows — returns **`None`**.

**Verdict 2: met.** The trailer-last body passes, and a real landed commit
classifies `None`.

---

## 3. Clause (c) — the 7-day negative control

> *Negative control: `landed-baseline.json` gains no new entry for 7 days after
> this lands.*

**A 7-day window cannot be demonstrated today: it has not elapsed.** But the
clause is not merely unmeasurable — **it is already falsified**, which is the
more useful finding and is reported rather than deferred.

Starting measurement on the audited tree:

```
$ python3 -c "...entry count + measured_at..."
entries        : 46
measured_at    : 2026-09-21T21:42:27Z
measured_head  : d7a5f7df1ad87f3be843076ae72966b280eec67e
measured_by    : issue #1669 lane, measured finding from scripts/check-isolation-landed
$ git log -1 --format='%h %ad | %s' --date=short -- governance/isolation/landed-baseline.json
d8dd917f 2026-09-21 | gate: record the squash merge 2548e5d0 (#1812) in landed-baseline.json (#1818) (#1820)
```

The epic's own landing-day commit is `85e41529` (2026-09-18, *"fix(isolation):
record 098d304 and d37132a — tenth and eleventh trailer-less landed commits
(#1145) (#1250)"*). Per-commit entry deltas since then, computed from the
baseline's own history:

| commit | date | prev | new | added | removed |
|---|---|---|---|---|---|
| `85e41529` | 2026-09-18 | 37 | 39 | **+2** | 0 |
| `9a8bf0a7` (#1704) | 2026-09-21 | 39 | 38 | +6 | −7 |
| `7725e1cc` (#1714) | 2026-09-21 | 38 | 39 | **+1** | 0 |
| `7527ee69` (#1747) | 2026-09-21 | 39 | 40 | **+1** | 0 |
| `ec475e25` (#1805) | 2026-09-21 | 40 | 45 | **+5** | 0 |
| `d8dd917f` (#1820) | 2026-09-21 | 45 | 46 | **+1** | 0 |

Net: **39 entries (2026-09-18) → 46 entries (2026-09-22), +7**, with **16 new
trailer-less landings recorded** in that window (`9a8bf0a7` relaxed the
predicate for the bare `Refs #<n>` and `Closes: #<n>` forms, which is why it
adds six and removes seven at once).

**Verdict 3: not met — falsified within the window.** New entries appeared
**within one to three days** of the epic's landmark, not zero in seven. The
newest, `2548e5d014` (the squash merge of #1812), carries its own cause in the
baseline entry: the merge was gated with
`scripts/check-squash-message.sh --pr 1809` — an **issue** number, not the
**PR** number — which 404'd, so the guard was never actually run against the
PR. That is precisely the shape §1b predicts: with no GitHub-side block, the
producer control holds only when the operator invokes it correctly, and a
wrong-argument invocation is a silent pass.

The clause as written is therefore an accurate statement of an **aspiration the
epic did not achieve**. It is disclosed, not dressed up, and it is recorded as
a residual in §7.

---

## 4. Children — all 22 closed, and the gate each contributed

Every child was re-read live (`gh api repos/kushin77/agent-orchestrator/issues/
<n> --jq .state`); all 22 answer `closed`. The mapping to the gate each child
contributed was read mechanically from the tree — `grep -rlE "#<n>\b"` over
`scripts/check-*.sh` and the governance modules — and is named as
"description" where no script cites the number literally.

| # | state | gate(s) it contributed |
|---|---|---|
| #1189 | closed | `check-dispatch-queue.sh` |
| #1199 | closed | `check-skip-ratchet.sh` (the skipped bucket ratchets) |
| #1262 | closed | `check-paperclip-control-mapping.sh` |
| #1263 | closed | `check-cloudbuild.sh` (live trigger/scheduler vs `infra/cloudbuild/*-trigger.yaml`) |
| #1264 | closed | `infra/cloudbuild/verify.yaml` (toolchain parity asserted in the verify runner) |
| #1265 | closed | `check-worktree-cap.sh` (+ `check-prune-worktrees.sh`, `check-reconcile-orphans.sh`) |
| #1266 | closed | `check-squash-message.sh` (`Closes #<n>` derived from the lane branch) |
| #1267 | closed | `check-pr-runner.sh` (`queueTtl`, `EXPIRED` a named failure) |
| #1268 | closed | sibling epic — `docs/EPIC-1268-CLOSURE.md` |
| #1276 | closed | merge rights / service identity — `check-branch-protection.sh` + `scripts/land.sh` (description) |
| #1301 | closed | `check-lifecycle-closeout.sh`, `check-lifecycle-reclaim.sh`, `check-session-isolation.sh` |
| #1328 | closed | `check-pr-contract.sh` (body declares class/posture/lifecycle/pillar/pattern/lane) |
| #1331 | closed | `check-merge-guard.sh`, `check-pr-queue.sh` (merged-tree green) |
| #1341 | closed | `check-cloudbuild.sh`, `check-fleet-cron-image.sh` |
| #1343 | closed | `check-pr-runner.sh`, `check-fleet-jobs.sh` |
| #1350 | closed | `check-gate-publish.sh`, `check-gate-status-scheduled.sh` |
| #1366 | closed | `check-lessons.sh` (the 2026-09-18 wave recorded by class) |
| #1382 | closed | `check-gate-publish.sh`, `check-branch-protection.sh` (the required check gets a producer) |
| #1402 | closed | `check-pr-contract.sh --landed` (+ `check-isolation-landed.sh`) |
| #1404 | closed | `governance/isolation/tests/test_authorship.py` (the fixture is no longer hijacked) |
| #1407 | closed | `check-gate-status.sh` (`post --detail`) |
| #1410 | closed | `check-skip-ratchet.sh` (third kind: assessability depends on a LIVE mechanism) |

Every child gate above runs inside `make verify` (`scripts/verify.sh`), which is
the epic's own intent — "the rule becomes a gate", not a paragraph.

---

## 5. Supersession adjudication for clause (a)

Clause (a)'s second half presumes that a required status check exists on
`master`. **It does not** (§1b), and that is a deliberate, recorded retirement,
not drift:

- **#1698** adopted the **single-developer landing method** — no required
  check, squash merge, lane verify advisory.
- **#1815** (closed) recorded the contradiction directly: *"branch-protection
  declaration still requires ao/gate-of-record while the adopted
  single-developer method says no required check."*
- The venue clause (a) names, **`control-plane-verify`**, is itself retired
  (#1361, superseded by #1295) and advisory-only (#1698) — the same retirement
  that forced the DoD amendment for EPIC #1268 (`docs/EPIC-1268-CLOSURE.md`
  §"DoD amendment", issue #1899).

**Adjudication.** Clause (a) is satisfied **on the producer side only**: the
predicate reds by name (§1a), and the landing drivers refuse the merge by name.
It cannot be satisfied **as a GitHub-level merge block**, and no work in this
lane could make it so without re-introducing a required check the fleet has
deliberately retired. Following the #1268/#1899 precedent, the epic body is
**amended** (not silently restated) to record this: clause (a)'s second half is
marked retired-with-measurement, and it is **not** a closure condition.

---

## 6. Verification (gate of record)

`make verify` on this branch (a docs-only change over `origin/master`) is the
evidence of record. Its verdict, and the pre-existing reds it shares with the
merge base, are recorded in the pull request body and compared against the
merge base in a scratch worktree — see `## Pre-existing red` there. A red that
reproduces identically at the merge base is pre-existing and is disclosed, not
claimed clean; a red introduced by this lane would be a defect of the lane.

---

## 7. Residual / disclosed — what is **not** evidenced here

1. **Clause (c) is falsified, not satisfied** (§3). The benchmark did not hold:
   16 new trailer-less landings were recorded in the window, and the newest one
   (`2548e5d014`, the squash of #1812) was caused by running the guard against
   an issue number instead of the PR. The producer control is only as strong as
   its invocation; with no GitHub-side block (§1b), a wrong argument is a
   silent pass. Closing this epic does **not** close that gap.
2. **Clause (a)'s GitHub-level half is unsatisfiable** and is retired by
   adjudication (§5). It is named here so the retirement is not read as a
   silent green.
3. **Clause (a)'s Fix (1) host is absent.** `infra/cloudbuild/verify.yaml`
   carries no `check-squash-message` step on the audited tree (§1c); the
   control lives in the landing drivers instead.
4. **A 7-day negative control cannot be demonstrated in one session.** Today's
   measurement is the *starting* point — 46 entries,
   `measured_at 2026-09-21T21:42:27Z`. Whether the baseline stays flat for the
   next seven days is an operational fact, not a closure condition.
5. **The epic body's child table is stale** (2026-09-19, "7 open / 15 closed");
   the live count is 22/22 closed. The body is amended with the measured count.
6. **Sibling epic #1510 remains open** and is untouched by this lane.

---

## 8. Reproduce

```bash
R=kushin77/agent-orchestrator
W=/home/akushnir/ao-worktrees/ao-1254-close-1790051372   # lane worktree, from origin/master
cd "$W"

# clause (a) producer half — composed message ending in `AI-assistance:`
bash /tmp/ao1254/pred.sh
#   A (AI-assistance: LAST)      -> commit-missing-ticket-trailer
#   B (trailer paragraph LAST)   -> <clean>

# clause (a) GitHub-level half — is a required check declared?
gh api repos/$R/branches/master/protection --jq '.required_status_checks'
gh api repos/$R/branches/master/protection --jq 'keys'

# clause (b) — a real landed commit classifies None
python3 -c "
import sys; sys.path.insert(0,'$W')
from governance.isolation import trailer
print(trailer.classify_commit('$W','091012c31a4dd0eb3d986f41ac6fd0ec4726b0d7'))
"

# clause (c) — the baseline's starting measurement and its own growth
python3 -c "
import json; d=json.load(open('governance/isolation/landed-baseline.json'))
print(len(d['entries']), d['measured_at'])"
git log --format='%h %ad | %s' --date=short -- governance/isolation/landed-baseline.json

# children — all closed
for n in 1189 1199 1262 1263 1264 1265 1266 1267 1268 1276 1301 1328 1331 \
         1341 1343 1350 1366 1382 1402 1404 1407 1410; do
  printf '#%-5s %s\n' "$n" "$(gh api repos/$R/issues/$n --jq .state)"
done
```

<sub>Closure evidence for EPIC #1254. Read-only measurement of a pristine
`origin/master` tree; the only files this lane adds are this document and its
one-line docs-index entry.</sub>
