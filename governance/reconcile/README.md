# Reconcile — a dead session leaves a clean workspace

> **Status:** institutional · **Issues:** #304, #628, #973 · **Gate:**
> [`scripts/check-reconcile.sh`](../../scripts/check-reconcile.sh) (`make verify`)
> · **Rule:** `AGENTS.md` golden rule 17

[`governance/isolation`](../isolation/README.md) opens a lane;
[`governance/lifecycle`](../lifecycle/README.md) closes one that finished. This
package handles the third case: a lane whose agent **died**.

## 1. Heartbeats

Every session writes `.fleet/sessions/<session_id>.json` while it runs:

```json
{"session_id": "2c7c76bfc1de", "issue": 304, "agent": "copilot-brain",
 "lane": "governance-reconcile", "worktree": "/home/.../ao-304-f48134aa",
 "branch": "issue-304", "pid": 41234, "at": 1789341000.5,
 "at_iso": "2026-09-13T22:30:00Z", "state": "running", "note": ""}
```

The beat is refreshed on an interval by a `Beater` thread and removed on clean
exit. Writes are atomic (temp file + rename) so a sweep never reads a partial
record and has to guess. The **pid recorded is the process being watched** — for
a dispatched lane that is the subagent, not the loop that spawned it; recording
the loop's pid would make every lane look alive for as long as the loop runs,
which is exactly the signal the sweep needs to be able to lose.

**Who writes the first beat (issue #917).** Measured 2026-09-16 on the shared
checkout: `.fleet/sessions/` held 0 beats while `.fleet/lanes/` held 78 records
and the worktree list 110 entries, so `status` printed `0 session(s), 0
orphan(s)` and the sweep had nothing to sweep — a vacuously green control. The
first beat is now written by the mint itself: `governance/isolation/cli.py open`
stamps the lane's session (`governance/isolation/session.py`) with the pid that
owns the lane, and `close` clears it, so every lane is in this plane from the
moment it exists whatever runtime opened it. `status` reads the lane plane
beside the session plane and says out loud when a record carries no beat
(`N lane record(s), K without a session beat`, plus a `NOTE` naming them) — the
sweep's input is never silently smaller than the lanes on disk.

## 2. Two signals, deliberately not one

| Status | When | Reclaimable |
|---|---|---|
| `live` | beat fresh, process alive | no |
| `suspect` | beat fresh, process **gone** | **no** — reported only |
| `orphan` | beat older than the TTL | yes |

Staleness catches a session that is alive but *wedged*, which a pid check cannot
see. Absence is decisive only once the beat has also gone stale: a fresh beat
behind a missing pid is the shape of a handover or a re-exec, and reclaiming a
live lane on that evidence would destroy the work the rule exists to protect.

## 3. Teardown: decided by where the work lives

| Work's location | Action | Outcome |
|---|---|---|
| landed on `master` | remove worktree, delete branch (local + remote), forget lane, release claim, clear beat | `reclaimed` |
| only on a remote branch | remove worktree, **keep** the remote branch, forget lane, release claim, clear beat | `parked` |
| nowhere else | keep everything, mark the session shelved, **keep the claim**, report | `shelved` |

**This worker never trades unmerged work for an unlocked issue.** A lane whose
commits exist only inside its worktree keeps both its worktree and its claim: it
is reported on every pass until someone resolves it, and reclaimed automatically
once its work lands. Unlocking the issue there would invite a second lane to
start the same work while the first lane's only copy sits unmerged — and the
claim's own reaper already exists for the case where the issue must move.

A `parked` or `shelved` lane is **re-evaluated every pass**, never written off.
That is what makes this a worker rather than a one-shot cleanup.

## 4. Usage

```bash
python3 governance/reconcile/cli.py status                        # who is alive
python3 governance/reconcile/cli.py status --disk                 # + the disk audit (#628)
python3 governance/reconcile/cli.py sweep --ttl-minutes 15        # dry run
python3 governance/reconcile/cli.py sweep --ttl-minutes 15 --apply
python3 governance/reconcile/cli.py watch --interval-seconds 60 --apply
python3 governance/reconcile/cli.py watch --once --apply          # one cron tick
```

Exit codes are tri-state: `0` OK, `1` NOT-OK (an orphan is present and nothing was
applied), `2` CANNOT-ASSESS — so a cron tick can tell "the fleet is clean" from
"someone left a lane behind" without parsing prose.

## 5. Enforcement

`scripts/check-reconcile.sh` runs in `make verify` as `reconcile`. It proves the
mechanism against a **real** repository — a scratch repo with a real (local) bare
remote and real linked worktrees — because a worker that deletes branches is only
trustworthy if its refusals are real too. It requires every outcome to be
provoked, including the load-bearing negative: an orphan with unmerged work is
`shelved` and its worktree **survives**.

The bookkeeping steps (`forget-lane`, `release-claim`, `clear-heartbeat`) are
asserted by `governance/reconcile/tests`, since a scratch repository has no
`governance/` of its own to call.

## 6. Disk audit — every artifact must be explained (#628)

Sections 1–3 all start from a session that **beat**. A lane that never beat is
therefore invisible to them: not "missed" but *unseen*. Measured on this
workstation (2026-09-14), `git worktree list` held **118** worktrees, **41 of them
for closed issues**, while `.fleet/sessions/` held no beats and the claim ledger
held no live claim — and `status` printed `0 session(s), 0 orphan(s)` and exited
OK. Absence of evidence was read as absence of orphans: a fail-open audit.

`governance/reconcile/cli.py status --disk` closes that by enumerating the
**disk** instead. Every `git worktree list` entry — except the primary checkout,
which is the repository itself and so cannot be orphaned — and every local
`issue-*` branch is an *artifact*, and every artifact must be explained by at
least one of:

| Evidence | Where it comes from |
|---|---|
| a **session beat** | `.fleet/sessions/<id>.json` names its worktree, its branch or its issue |
| a **claim record** | a live claim on the issue, from `.board/claims/` + `.board/claims.jsonl` |
| the **landing history** | `.fleet/lifecycle/<issue>.json`, journalled by `governance/lifecycle`'s close-out once an item's work landed |
| the **landing proof** | `governance/reconcile/landing.py` — the artifact's *work* is on the default branch (#1291) |

Those last two are deliberately separate, because the first one is **gitignored
runtime state**: measured at `origin/master 99f6b37` (2026-09-18), the audit
reported 35 findings in the real tree and **25 of them were false** — lane
branches and worktrees whose work was already on the default branch, reported as
orphans only because no close-out journal happened to exist for them. Every false
finding red the composite gate, and on this box that gate is a fleet-wide
serialization point (16 open pull requests at the time).

The landing proof reads the same fact from the default branch's **tracked**
history, which every checkout has, and it never uses a name as evidence:

| Proof | What it establishes | The case it exists for |
|---|---|---|
| `landed:ancestor:<sha>` | the tip itself is on the default branch | a merge-commit or direct landing |
| `landed:tree-contained:<sha>` | every path the tip changed is byte-identical on the default branch | a **squash merge**, whose tip is an ancestor of nothing |
| `landed:patch-identity:<sha>` | a commit on the default branch carries the tip's own `git patch-id` | a squash landing whose files a **later sibling also edited** |

Candidate landing commits are generated from the default branch's own subjects
(`Closes #n` / `#n`) — and the **proof is the patch, never the name**, which is
what separates a landed lane from an abandoned one whose issue number merely
appears in history. Two limits are deliberate: a **worktree** is only explained
when it holds **nothing uncommitted** (the proofs speak about committed work, and
uncommitted work is on no branch at all — measured: both of the two artifacts that
stopped being false findings this way had `HEAD` already on the default branch
and uncommitted paths beside it), and the default-branch ref being absent grants
no excuse at all (a fixture root has no `origin/master`, and an excuse that cannot
be proven is never given).

Measured effect on the real tree at `ca5fcda`: **23 branch findings → 10**, and
the two worktrees whose `HEAD` had landed but which hold uncommitted work stayed
findings. The residue is what §7 below quarantines by name.

**The audit removes nothing.** It has no `apply`, it calls no destructive
operation, and the `AuditOps` port it reads through has no such method for it to
call (`test_the_port_has_no_removal_method_at_all` asserts that structurally).
Reclaiming stays `sweep`'s job under the three-way rule of §3, which is unchanged:
the worker still never trades unmerged work for an unlocked issue. The audit only
makes the lanes `sweep` structurally cannot see visible and named, so a human — or
a later sweep, once a real beat exists — can act on them.

Its exit contract is the one that matters most, because the defect was a *wrong
OK*: `0` only when the state was read and every artifact was explained, `1` when
an artifact is unmatched, and **`2` CANNOT-ASSESS** when the state could not be
read — an unreadable `git`, an empty worktree listing (a repository always has at
least its own), a corrupt session beat, a corrupt claim record or a corrupt
journal. An audit that cannot tell "no orphans" from "could not look" reports the
second, never the first.

```bash
$ python3 governance/reconcile/cli.py status --disk
reconcile-audit (/path/to/repo): 434 artifact(s) — matched=40, unmatched=393, exempt=1
  unmatched worktree /home/akushnir/ao-worktrees/ao-142-58950fde
      no session beat, claim record or landing record names it
reconcile-status: 0 session(s), 0 orphan(s)
```

It is an option on the existing `status` verb rather than a verb of its own **on
purpose**: a new CLI verb is a surface change that the control-plane verb registry
gates (`control-plane/control/verbs.yaml`, contract-first), and that contract
belongs to its own lane. The audit is a read-only addition to a report that
already exists, so it is declared as one — and `status` without `--disk` behaves
exactly as before.

## 7. Named quarantine — the residue the gate must report, not red on (#1291)

Two facts have to be reconciled, and either one alone is a defect:

* `AGENTS.md` **rule 17**: an orphan whose work exists nowhere else **keeps** its
  worktree, its branch and its claim, and is *reported on every pass until someone
  resolves it*. The worker is forbidden to discard it.
* a red `make verify` is not a report — it is a **fleet-wide serialization
  point**: on 2026-09-18 one red check held 16 open pull requests.

And the age grace does not resolve them, it only *defers* them: measured at
`99f6b37`, the real-tree finding count went **27 → 30 → 31 in seven minutes with
no commit in between**, every new one an artifact crossing the 24-hour grace
while the fleet worked (~8-10 new violations per hour).

`governance/reconcile/real-tree-quarantine.json` reconciles them in this
repository's own idiom for legacy drift (`governance/lifecycle/baseline.json`,
rule 16), and it is checked in both directions:

| Rule | Effect |
|---|---|
| every exemption is **named** | one entry per artifact: `kind`, `name`, the `tip` it was **measured at**, and a `reason` — no pattern, no prefix, no wildcard |
| a **new** artifact is **never** absorbed | it is not in the document, so it fails immediately |
| an artifact whose **tip has moved** is **never** absorbed | it is a different artifact; it fails, and the lapsed entry is named as it fails |
| an entry that **excuses nothing** | the artifact is gone or no longer unmatched: the verdict names it in `stale_quarantine` and exits **1** — the document can only shrink |
| an entry that **is no longer needed** | the artifact's whole committed work is on the default branch by patch identity, and (for a worktree) it holds no uncommitted work **of its own**: the verdict names it in `refuted_quarantine` as `REFUTED-QUARANTINE` and exits **1**. A landed-by-content artifact is not the rule 17 class — its home is the reviewed baseline, and the reap is the reaper's (#1311) |
| it is a **lease** | entries are honoured only while the declared tracking issue is `open` **and** the declared measurement is younger than the declared `max_age_hours`; a missing, malformed, expired or not-open declaration is never read as "still excused" |
| it declares its **venue** | the repository instance the exemptions were measured in (`venue.git_common_dir`). At that venue every rule above bites; at any other the whole document is **inert** — each entry reported as `NOT-APPLICABLE`, **honouring nothing** (an artifact that *is* unmatched there stays a finding) and **not fatal** (a stale exemption is a claim about the declared venue's disk, which that checkout cannot observe). Entries with no venue at all are **CANNOT-ASSESS** |

Quarantined artifacts are still reported on **every pass**, by name, in the
verdict (`QUARANTINED <kind> <name> @<tip> — <reason>`), which is what satisfies
rule 17's "reported until someone resolves it": the tracking issue carries the
resolution, and the document carries the record of what is unresolved.

**Why a venue, and why #1317 was right and wrong (#1321).** An exemption names a
*disk artifact of one repository instance* — a local branch of this checkout, a
worktree path on this machine. The document is tracked, so it is read on
checkouts where that artifact was never there, and read venue-blind every entry
there "matches nothing" and **fails** as a stale exemption: measured on a
pristine clone, `0 new-and-old, 538 stale, not fatal; 23 stale quarantine
exemption(s)` → exit 1. `#1317` measured exactly that and emptied the document,
which un-quarantined all 23 on the one box that has them and red the fleet
again — a true measurement whose remedy deleted the record instead of scoping it.
The two checkouts need *different answers*: at the declared venue the exemptions
are in force with every tooth above; anywhere else they are inert. Inert is
fail-closed on the honouring half (the artifact stays a finding, because this
document does not speak for that checkout) and non-fatal on the staleness half (a
stale exemption is a claim about the *other* venue's disk).

Declaring a foreign venue cannot buy a green: it honours nothing, so the
artifacts the entries were hiding come back as findings by name. Nor can dropping
the venue (CANNOT-ASSESS) or emptying the list (the artifacts then fail as
unbaselined-and-old). A venue this check cannot read is CANNOT-ASSESS too —
never read as "some other venue", which would silently stop evaluating
exemptions.

```bash
$ bash scripts/check-reconcile.sh
real-tree-baseline: 134 unmatched artifact(s) on disk, 557 baselined, 49 young (< 24h, not failed), …
  quarantine: honoured — #1311 open, measured 0.0h ago (lease 24h, by governance/reconcile (issue #1311 lane))
  7 quarantined by name (reported, not failed)
  QUARANTINED branch issue-1114-dispatch-master-green @d084114b73e8 — AGENTS.md rule 17 …
real-tree-baseline: OK — no new unbaselined-and-old artifact
check-reconcile: OK — …
```

**Why a refutation, and why #1317 was also right about the reasons (#1311).**
Every rule above measures an entry against *itself*: gone, moved, lapsed. None of
them re-measures the claim the entry exists to make — that this work exists
nowhere else — so an entry whose premise has quietly become false is honoured,
reported, and excusing, forever. Measured on #1311: **five of the document's 23
entries** said "no commit on the default branch carries its work" while a commit
did, with an **identical** `git patch-id --stable`, because the squash that
landed them named the pull request in its subject and the issue nowhere in a
`Closes #n` line — exactly the miss `landing.py`'s declared candidate rule cannot
see. So an honoured entry is also re-measured for whether it is still needed, by
`landing.RepoLanding.surplus_patch_identity` (the default branch's commits
**touching a path the artifact changed**, patch-compared — a necessary condition
for an identical patch, not a heuristic) plus
`governance/isolation/worktree.foreign_uncommitted` (the repository's own
declaration of the paths a reclaim must refuse). An exemption that is not needed
is **REFUTED** and fails.

It is refuted, not withdrawn: the artifact *is* on disk and the audit still
cannot explain it, so the entry stays honoured and the verdict carries **one**
named failure — the demand that the document shrink. Refutation is evaluated only
for entries **in force** (same venue, live lease), and every unreadable state (no
default ref, a failed `git`, an unmeasurable status, a candidate list past its
bound) leaves the exemption **standing**, because the proof's only power is to
ask for a protection to be given up. That rule is what took this document from 25
entries to 7: 15 refuted by exactly that proof, plus 3 whose work a live
`git ls-remote` shows on a remote branch, all 18 recorded in the reviewed
baseline.

The gate proves the rules rather than asserting them (§6e): a fixture entry
naming a real, present, unmatched artifact at its exact tip **is** honoured; an
entry that matches nothing **fails** by name; an artifact whose tip has moved is
**not** absorbed; a lease that is closed or past its declared age honours
**nothing** and fails by name; an entry measured in **another repository
instance** is inert — reported by name, honouring nothing, not fatal; entries
with no venue (missing or empty) are **CANNOT-ASSESS**; and an unreadable
document is **CANNOT-ASSESS**, never a pass. The **refutation** rule above is
proved by the package suite instead (`governance/reconcile/tests`: the negative
control that must fire, plus the controls where it must not — no default ref, a
worktree holding its own uncommitted work, a document measured elsewhere), not by
§6e, whose fixtures predate it.

**What this costs, stated plainly.** The lease is a *lease*: after
`max_age_hours` (24, declared in the document) the exemptions stop being honoured
until someone re-measures the tracking issue and records it — one reviewed edit.
That is deliberate (a declaration nobody can falsify is decorative, and
`governance/lifecycle` takes the same fail-closed line, treating an *unknown*
tracking state as stale), but it does mean the gate reds on a stale declaration
even when nothing on disk drifted. A new artifact crossing the grace window also
reds, by design: green means "nothing new and unresolved", not "nothing left".
The same one-edit cost applies when a quarantined artifact is resolved: the entry
no longer excuses anything and must go in the same reviewed edit, which is what
keeps the document shrinking instead of rotting. **A quarantine is a snapshot of
one box's unresolved work, not a property of the repository** — it is honest
exactly as long as its entries are re-measured against the disk they name. That
is also why an entry's `reason` is a *measurement*, not a label: the #1311 pass
re-measured all 23 and found 15 of them protecting nothing (the artifact's work
on the default branch, or a worktree holding only machine-managed residue) and 3
more preserved on a remote branch — 18 entries and their premises replaced by one
reviewed edit, which is the only honest way this list was ever going to shrink.

## 8. Board reporting

A finding must reach the board, not only a log line. A `shelved`, `failed` or
`suspect` outcome files a GitHub issue carrying the named violation and its
evidence (`governance/lifecycle/report.py`, issue #321); a previously shelved
lane whose work has since landed is *resolved* — the finding is dropped so a
genuinely new shelve files again.

- **Idempotent.** One issue per fingerprint (the lane's issue number or session
  id), deduped through `.fleet/board-reports.json`, so repeated passes do not
  spam the board for the same unresolved lane.
- **Dry-run by default.** No board write happens without `--apply`; a dry-run
  pass prints `board: dry-run — would file for …`.
- **Offline-testable.** The board effects are an injected port, exercised by
  `governance/reconcile/tests/test_reconcile_boardreport.py` with no network.

## 9. `elite` artifacts (issue #885)

Four artifacts, each read by name from the code that used to hard-code (or
never had) its equivalent:

| Artifact | File | Read by |
|---|---|---|
| **controls** | [`controls.yaml`](controls.yaml) + [`policy.py`](policy.py) | `sweep.py` (`sweep()`, `policy.load()`) reads `sweep.max_actions_per_pass` and `sweep.outcome_codes` — never a bare literal |
| **audit trail** | [`ledger.py`](ledger.py) | `sweep.py` (one `sweep-decision` record per `Action`) and `real_tree_baseline.py` (one `real-tree-verdict` record per `check_real_tree()` call) |
| **schema** | [`reconcile.schema.json`](reconcile.schema.json) | `ledger.py` (validates every record before writing) and `live.py` (validates every projected row) |
| **live feed** | [`live.py`](live.py), through `status --live` | `cli.py`'s `cmd_status` |

**Controls.** `governance/policy/lease.py` already owns every timing this
package *shares* with the rest of the fleet (session TTL, heartbeat interval,
the real-tree age-grace window). `controls.yaml` declares the two controls
that are this package's **own**:

* `sweep.max_actions_per_pass` — a runaway guard. `sweep --apply` acts on
  every orphaned session it finds in one pass; without a cap, a wrong
  `--ttl-minutes` or a wedged clock turns "reconcile the fleet" into "tear
  down every worktree at once." Once the cap is reached, every further
  destructive decision this pass is `refused` (outcome `refused`, code
  `reconcile.batch-limit-exceeded`) and re-evaluated next pass — never
  dropped. `sweep.py:sweep()` reads it through `policy.load()`
  (`governance/reconcile/sweep.py`, the `resolved_controls` seam) instead of
  a hard-coded number; `governance/reconcile/tests/test_reconcile_policy.py` and
  `test_sweep.py::test_batch_limit_*` prove the mutation: a temp copy with
  `max_actions_per_pass: 0` refuses every destructive decision, by name.
* `sweep.outcome_codes` — the closed vocabulary `ledger.py` stamps a decision
  record with; a code not declared here is refused (`ControlsUnavailable`)
  rather than written.

**Audit trail.** `ledger.py` appends one JSON line per sweep decision
(including a `refused` one) and per `real_tree_baseline.check_real_tree()`
call, to `.fleet/reconcile/ledger.jsonl` under the reconciled root — never
rewritten, never truncated. Every record is validated against
`reconcile.schema.json#/$defs/ledger-record` **before** it is written
(`ledger.append` → `validate_record`); `ledger.verify()` re-checks every line
on demand, which is how the gate's schema-invalid provocation is expressed.
`test_ledger.py::test_refusal_produces_exactly_one_record` and
`test_sweep.py::test_a_refused_decision_produces_exactly_one_named_ledger_record`
prove the "one refusal → one record, validated" requirement.

**Schema.** `reconcile.schema.json` freezes four shapes this package
persists or emits: the heartbeat record, the real-tree-baseline entry, the
ledger record, and the live-feed row. Validated through
`governance/modules/schema.py` (reused, not copied, per the stdlib-only
subset-validator convention `governance/modules` and the hermes/paperclip
`faang` surfaces already established) — no third-party JSON-Schema library,
so the check stays offline and deterministic.

**Live feed.** `live.py` projects every session heartbeat against the real
disk — not a cache, a live query re-run on every call — and tags each row
`matched` or `drift`: a session whose heartbeat still names a worktree that is
no longer there (a hand `rm -rf`, a teardown that crashed mid-way) is `drift`,
named. Exposed through the existing `status` verb (`status --live`), the same
reasoning §6 already gives for `--disk`: a new CLI verb is a surface the
control-plane verb registry gates, and that contract belongs to its own lane.

```bash
python3 governance/reconcile/cli.py status --live
```

`scripts/check-reconcile.sh` §7–§10 provoke each artifact in turn: a mutated
control (limit `0`) refuses every teardown by name; a deleted ledger record is
detected by `ledger.verify()`; a hand-corrupted ledger line fails schema
validation by name; and a session whose recorded worktree is deleted out from
under it is reported as `drift` by `status --live`.

## 10. A filed finding reaches a terminal state (#973)

§8 files a finding once per fingerprint and never files it again. Filing was
idempotent; **unfiling did not exist.** The only `resolve` call in the repository
was the `shelved:` key below, so a `lifecycle:*` fingerprint lived in
`.fleet/board-reports.json` for ever.

Measured on 2026-09-17: the board carried `[lifecycle] LANE_NOT_RECLAIMED — #241`
while the item it named was fully reclaimed — #241's lane record, worktree, claim,
branch and heartbeat were all gone, and the live audit charged it **0 findings out
of 715** in the record. The finding was true when filed and false from then on.

Two harms follow, and the second is the one that matters:

* **stale board noise** — an open issue describing a violation that no longer
  exists, which reads as a live defect to everyone who opens it;
* **a fail-open dedupe** — because the fingerprint never left the ledger, a
  *genuine* recurrence of the same violation on the same subject is reported as
  `deduped` against the stale issue and **never filed**.

`findings.py` gives every filed finding the terminal state §3 already gives a
lane: it is **re-evaluated every pass**, never written off. The re-measurement is
the audit's own (`lifecycle`'s `audit(record, quarantine)`, the same call
`lifecycle/cli.py audit` makes) — a second implementation of the invariants would
drift, and the two would then disagree about whether a finding is owed.

| Re-measurement | Outcome | Entry | Board |
|---|---|---|---|
| the invariant is **still charged** for the subject | `still-owed` | kept | nothing |
| the board **could not be read** | `unmeasured` | kept | nothing |
| the audit **cannot speak about** the subject | `unmeasured` | kept | nothing |
| the board write **failed** | `failed` | kept, retried next pass | nothing |
| the invariant is **no longer charged** | `resolved` | retired | issue closed with the re-measurement as its evidence |
| a dry run, invariant no longer charged | `would-resolve` | kept | nothing |

Three properties are deliberate and load-bearing:

* **Absence of evidence is never read as absence of the violation.** An
  unreachable board, an uncollected subject and a lost write all *keep* the
  fingerprint and are named. Only a successful re-measurement that does not charge
  the invariant retires it.
* **Only `apply` resolves anything.** `BoardReporter.resolve` saves the ledger
  unconditionally and gates only the comment, so a dry run that called it would
  drop the dedupe entry with no board write at all — a silent suppression of a
  finding. Every retirement goes through `findings.resolve_key`, which refuses
  while `apply` is false. This also fixes the same hazard on the `shelved:` path.
* **The close happens before the ledger is retired.** The ledger entry is what
  makes the resolution retryable, so retiring it first would turn a failed board
  write into a permanently open issue nobody would look at again.

`reconcile:failed:` and `reconcile:suspect:` findings reach the same terminal
state through §3's own evidence: once the session has been reclaimed or parked,
nothing they assert is true any more, so a later pass retires them. A
`failed:`/`suspect:` issue for a lane that was reclaimed cleanly used to stay open
for ever, and swallow a recurrence the same way.

`recheck` is a **seam on `sweep()`**, not a call inside it: re-measuring reads the
board, and the gate drives `sweep()` offline against a scratch repository. With no
seam given, nothing is read and the pass is exactly what it was. With no lifecycle
fingerprint in the ledger this is a local file read and no board at all.

The recheck runs **after** every session decision in the pass, so a slow board
delays the next pass and never the teardown of an orphan. It is also bounded: the
collection shells out to `gh` (no timeout of its own) and once per item to
`git ls-remote`, and one collection measured **~150s** on 2026-09-17 with ~40 lanes
running, so it runs as a child process under `COLLECT_TIMEOUT_SECONDS` (300s, inside
the pass's own nominal budget). A read that runs out of budget is `unmeasured` —
nothing is resolved, the same verdict as a board that could not be read. Turning the
recheck *cadence* into its own declared control is the obvious next step and is left
out of this change on purpose: it is a policy value, not part of the defect.

```bash
python3 governance/reconcile/cli.py sweep --root <repo>            # names what would resolve
python3 governance/reconcile/cli.py sweep --root <repo> --apply    # closes + retires
```

## 11. The orphan walk — five kinds, named, budgeted (#1301)

Sessions that beat (§1–3) and artifacts a beat, claim or landing explains (§6)
still leave the contract's question unanswered: **which artifacts have no live
lane and no evidenced close-out?** Measured 2026-09-18 on this box: 47
worktrees no lane record names (31 of them `.claude/worktrees/agent-*` subagent
trees), 85 local `issue-*` branches with no lane, no recorded worktree and no
open PR, 11 open PRs bound to no lane, 19 lane records whose issue is closed,
111 `.fleet/sent/` orders naming a closed issue.

```bash
python3 governance/reconcile/cli.py sweep --orphans                 # walk + hold to the budget
python3 governance/reconcile/cli.py sweep --orphans --apply         # reclaim only with evidence
```

`governance/reconcile/orphans.py` walks all five kinds from files, git and the
board (the committed snapshot first, `gh` for what it lacks) and names each:

| finding | an artifact that | reclaim |
|---|---|---|
| `orphan-worktree` | a linked worktree no lane record names | under `--apply`, only when HEAD is content-landed and it holds no lane-authored dirt; tip recorded to `.fleet/reaped-branches.jsonl` first |
| `orphan-branch` | a local `issue-*` branch with no lane, no recorded worktree, no open PR — **its SHA is in the finding** | under `--apply`, only when content-landed; tip recorded first; never otherwise |
| `orphan-pr` | an open PR whose head is no lane's branch and whose body has no `lane: <lane_id>` line | never — add the line or open the lane |
| `orphan-issue-lane` | a lane record whose issue is closed | never here — `lifecycle close --lane <id>` is the evidence |
| `orphan-directive` | a `.fleet/sent/` order naming a closed issue | never here — `lifecycle close --issue n` / `retire` |

The counts are held to `governance/reconcile/orphan-budget.yaml` — the counts
measured the day the walk shipped plus churn headroom, and an `expires` date
after which the budget is 0 (the ratchet shape of `worktree-cap.yaml`, #1335).
Above it the sweep is NOT-OK by name, `orphan-budget-exceeded:<kind>:<n>/<budget>`;
a kind whose source could not be read is `unmeasured` and the sweep is
CANNOT-ASSESS, never zero. `scripts/check-reconcile-orphans.sh` proves every
finding on an injected port, reaps and refuses on a real scratch repository,
and walks the real tree from the main checkout against the declared budget.

