# Lessons — RCA and corrective-action enforcement (issue #141)

Every failure, breach, defect, operational incident and preventable drift is
analysed and recorded, so the organization can show what it learned instead of
relearning it. This module is the enforcement: one canonical ledger, one
template, one gate.

## The single ledger

[`ledger.jsonl`](ledger.jsonl) is **the** authoritative record. It is JSONL —
one record per line, in file order. There is no second ledger, no per-pillar
ledger, and no "lessons" section in another document: a learning that is not in
this file does not exist, and a corrective action that is not in this file is
not recorded (AC4).

Four record kinds:

| Kind | Id | Meaning |
|---|---|---|
| `incident` | `INC-0001` | a failure, breach, defect or preventable drift |
| `rca` | `RCA-0001` | the root-cause analysis of one incident |
| `corrective-action` | `CA-0001` | an action the RCA requires; owned and evidenced |
| `lesson` | `LESSON-0001` | a **closed** learning, proven by commit evidence |
| `lesson` | `SUGGEST-0001` | an **open** improvement idea: owner + remediation |

Relations: an `rca` names its `incident` and lists its `corrective_actions`;
an action names its `rca`; a lesson names its `rca`. Everything is traceable in
both directions, and the gate fails a reference that does not resolve.

Those relations are **typed ticket edges**, not free strings (issue #402):
[`edges.py`](edges.py) is the single place a lessons reference becomes a node id,
and the ticket-graph edge vocabulary is closed at four types — `caused-by`
(`RCA-*` → `INC-*`), `origin` (an `RCA`/`INC` → its source `issue-*` / `pr-*` /
`commit-*` / `event-*`), `mitigates` (a `CA-*` → its `RCA-*`, a lesson → the
incident of its RCA) and `remediation-of` (an open action → the `issue-*` that
carries it). The edges are **derived**, never stored: a stored copy would be a
second source of truth the ledger could drift from.

## Recording an incident

1. **Write the incident line** with its origin — `{"kind": "issue"|"pr"|
   "commit"|"event", "ref": ...}`. Issue and PR refs are `#<number>`; issue
   refs are resolved against the committed board snapshot.
2. **Copy [`rca-template.md`](rca-template.md)** to
   `governance/lessons/rca/RCA-<n>-<slug>.md` and fill in every section. An
   artifact missing a section fails the gate, so an "RCA" cannot be a one-line
   apology.
3. **Record the `rca` line** with `artifact` pointing at that file.
4. **Record one `corrective-action` line per action**, each with evidence.
   An open action must name the issue that carries it (`remediation_issue`).
5. **Record the lesson** — a `LESSON-<n>` with commit evidence once the change
   lands, or a `SUGGEST-<n>` with an owner while it is still open.

Append a record with the CLI rather than by hand; an invalid record is refused
instead of written:

```bash
python3 governance/lessons/cli.py record --file /tmp/record.json
python3 governance/lessons/cli.py check          # the gate of record
python3 governance/lessons/cli.py status         # summarize, do not judge
```

## What the gate enforces

`bash scripts/check-lessons.sh` (`make lessons`) is wired into `make verify`
and `make lint`. Errors fail the gate; deviations are reported with the issue
that carries them.

| Code | Severity | What it means |
|---|---|---|
| `ledger-invalid` | error | a line does not parse, or is not a record object |
| `ledger-empty` | error | an empty ledger is not a pass |
| `unknown-kind` | error | a record kind outside the four above |
| `duplicate-id` | error | one id must have exactly one authoritative line |
| `record-incomplete` | error | a required field is missing or blank |
| `invalid-field` | error | a field outside its declared vocabulary |
| `unknown-reference` | error | `rca`→`incident`, action→`rca` or lesson→`rca` dangles |
| `incident-without-rca` | error | AC2: every incident has an RCA artifact |
| `rca-without-origin` | error | AC3: the RCA names no originating issue or event |
| `origin-unresolved` | error | the origin ref is malformed or not on the board |
| `rca-artifact-missing` | error | the artifact path does not exist |
| `rca-artifact-untracked` | error | the artifact exists but is not committed |
| `rca-artifact-incomplete` | error | a required template section is absent |
| `rca-without-corrective-action` | error | an analysis that requires nothing |
| `corrective-action-unrecorded` | error | AC4: the RCA names an action that is not in the ledger |
| `corrective-action-unlinked` | error | an action no RCA claims |
| `corrective-action-without-evidence` | error | a closed action with no proof |
| `corrective-action-without-owner` | error | AC6: an open action with no remediation issue |
| `incident-closed-without-lesson` | error | closed before the lesson was recorded |
| `lesson-without-evidence` | error | a learning with nothing behind it |
| `lesson-without-commit-evidence` | error | a closed lesson must name the commit that shipped it |
| `lesson-invalid-status` | error | a `LESSON-` that is open, or a `SUGGEST-` that is closed |
| `suggestion-without-remediation` | error | an open idea with no proposed change |
| `suggestion-without-owner` | error | an open idea with nobody accountable |
| `evidence-unresolvable` | error | a cited commit is not in this repository's history, or a cited artifact is absent (a **deviation** in a shallow clone, where history cannot be resolved) |
| `edge-unresolved` | error | a cross-record reference cannot be typed as a ticket edge (a malformed `origin` or `remediation_issue`, issue #402) |
| `board-incident-without-rca` | error | an issue carrying the `incident` **record** label is closed and no `INC-*` line names it as its origin |
| `board-incident-pending` | deviation | the same, while that issue is still open |
| `rca-review-overdue` | deviation | the RCA was not re-read within the cadence |
| `corrective-action-open` | deviation | the action is still in flight, tracked by its issue |
| `suggestion-open` | deviation | the improvement idea is still open |
| `doc-rca-id-unknown` | error | issue #1052: a `docs/rca/*.md` writeup cites an `RCA-NNNN` id that is not a recorded ledger id |
| `doc-rca-artifact-mismatch` | error | issue #1052: a `docs/rca/*.md` heading MINTS (starts with) an `RCA-NNNN` id whose ledger `artifact` names a different file — a lightweight doc may CITE a ledger RCA, it may not MINT one |
| `readme-incident-count-mismatch` | error | issue #1052: the hand-written incident count below ("The incidents recorded so far") does not match the ledger's actual count |
| `board-link-missing` | error | issue #1178: a `LESSON-*`/`SUGGEST-*` that reaches no board issue and declares no `orphan` reason — silence is what left twenty records unreachable |
| `board-link-dangling` | error | issue #1178: an incident's `origin`, an orphan's `ticket` or an open action's `remediation_issue` names a board issue the committed snapshot does not carry |
| `board-link-unlabelled` | error | issue #1178: the ledger names the issue as an incident's origin and the board does not carry the `incident` label, so the board rule cannot select it |
| `board-link-orphan` | deviation | issue #1178: a record that reaches no board issue, reported with its declared reason |
| `board-link-goal-unresolved` | deviation | issue #1178: the record reaches an issue that has neither an epic (`Parent: #N`) nor a milestone |

`--strict` escalates every deviation to an error. That is the honest position:
the in-flight work and the historical backlog are real and named, and a
milestone that has caught up can raise the bar without editing the gate.

## The board rule, provoked rather than asserted

[`negative_control.py`](negative_control.py) runs on every gate pass (part 2 of
`scripts/check-lessons.sh`) and plants one fact per probe, requiring the named
verdict. It exists because this rule was wrong about *every* issue that held its
label, and the by-hand exemptions that followed made it unable to fail at all:

| Probe | What it must observe |
|---|---|
| `AREA-LABEL-IS-NOT-AN-INCIDENT` | a closed issue in the incident-response AREA produces no board finding |
| `RECORD-LABEL-WITHOUT-A-RECORD-IS-REFUSED` | a record-labelled issue with no ledger record is still an error, named |
| `LEDGER-INCIDENT-WITHOUT-RCA-IS-REFUSED` | a genuine incident RECORD with no RCA is still an error — the re-keying did not trade one inert check for another |
| `RECORD-LABEL-WITH-A-RECORD-IS-ACCEPTED` | the detector reads the RECORD: the same issue, backed by `INC-*`, passes |
| `OPEN-RECORD-LABEL-IS-A-DEVIATION` | an open one is a reported deviation, not an error |
| `EXEMPTIONS-CANNOT-BE-DECLARED` | a policy declaring `board.exemptions` is refused by name |
| `AREA-LABEL-CANNOT-BE-THE-RECORD-LABEL` | an `area:` label in that position is refused by name |
| `SHIPPED-POLICY-DECLARES-THE-RECORD-LABEL` | *this* repository's policy names the record label and has no exemption path |
| `REAL-BOARD-HAS-NO-UNRECORDED-RECORD-LABEL` | the same verdict against the REAL ledger, policy and snapshot — the four area-label holders measured, not assumed |
| `MUTANT-DROPS-THE-REFUSAL` | the control's own control: a scratch copy of the checker with the selector forced off must STOP refusing, or the probe above proves nothing |
| `DOC-RCA-UNKNOWN-ID-IS-REFUSED` | issue #1052: a `docs/rca/*.md` writeup citing an `RCA-NNNN` id the ledger never recorded is refused |
| `DOC-RCA-CITATION-IS-ACCEPTED` | citing a KNOWN ledger id in prose, without minting it, is accepted |
| `DOC-RCA-MINT-MISMATCH-IS-REFUSED` | a heading that MINTS a ledger id whose `artifact` names a different file is refused — the exact double-booking shape of the pre-fix `docs/rca/2026-09-16-pr-queue-clearing.md` |
| `README-INCIDENT-COUNT-MISMATCH-IS-REFUSED` | a README incident count that does not match the ledger is refused |
| `README-INCIDENT-COUNT-MATCH-IS-ACCEPTED` | the same sentence, with the ledger's real count, is accepted |
| `REAL-DOCS-RCA-HAVE-NO-UNKNOWN-IDS` | the REAL `docs/rca/` tree cites only ids the REAL ledger holds |
| `REAL-README-INCIDENT-COUNT-MATCHES-LEDGER` | the REAL README's count matches the REAL ledger's count |
| `MUTANT-DROPS-DOC-RCA-REFUSAL` | the doc-rca-id-unknown refusal, proven able to fail, via the same generic mutant driver |
| `MUTANT-DROPS-README-REFUSAL` | the readme-incident-count-mismatch refusal, proven able to fail, via the same generic mutant driver |
| `DUPLICATE-ID-IS-REFUSED` | refusal (a) of issue #1052: two ledger lines sharing one id is still refused, named |
| `MUTANT-DROPS-DUPLICATE-ID-REFUSAL` | the duplicate-id refusal, proven able to fail |

The last probe is the one that matters most. A control proved only against a
fixture never sees reality, and a probe that fires under every mutation proves
nothing — so the mutant is built, the refusal is observed to disappear, and the
harness reports it by name.

## The board linkage: ledger -> issue -> epic (issue #1178)

A register that cannot be reached from the board is an island. The only
direction this module enforced was *labelled issue -> ledger record*, and the
label selected an empty set on real data, so the rule could fail only against
the mutants above. [`linkage.py`](linkage.py) makes the other direction —
**ledger -> issue -> epic** — a measurement and a rule.

```bash
python3 governance/lessons/cli.py linkage                # the census
python3 governance/lessons/cli.py linkage --orphans-only # just the unreachable
```

A record reaches the board when a path of its own fields gets there: its
`origin` (kind `issue`), the `remediation_issue` of an action, or the `rca` /
`incident` record it names. **A pull request is not a ticket**: a `pr`, `commit`
or `event` origin declares where a record came from but is not a board node, so
it does not make the record reachable. A reachable record whose issue has
neither a `Parent: #N` epic nor a milestone has reached an *issue* but no
*goal*, which is a deviation.

Three rules make it binding:

1. **Silence is refused.** A `LESSON-*`/`SUGGEST-*` that reaches no board issue
   must declare it: `"orphan": {"reason": "..."}`. The thirteen records that
   were unreachable and silent now carry that declaration, each with the reason
   (their RCA traces to a pull request or a recorded event, not to an issue).
   The declaration is a *claim*, not an exemption: a false one is refused, and
   the `orphan.ticket` it may carry must resolve on the board.
2. **A dangling reference is refused.** An incident's `origin` was never
   resolved against the board before this issue — only an RCA's was — so an
   incident could name an issue that no longer existed and nothing saw it.
3. **The label lands where the ledger names it.** `board-link-unlabelled`
   requires every issue the ledger names as an `INC-*` origin to carry the
   `incident` record label. This is the inverse of `board-incident-without-rca`,
   and it is what keeps the scope declaration from being empty: the holder set
   is derived from the ledger, not from the label. On `origin/master` before
   this issue the gate measured **1** holder (the label was on #1029 alone,
   whose `INC-0007` names it) while `cli.py status` printed **0** — `status`
   called `check_ledger(..., snapshot=None)` and so could never count a holder
   at all. `status` now loads the snapshot, and five issues carry the label.

**The measured census** (this revision, 75 records): 34 reach a board issue, 26
of those reach a goal, 41 are orphans, 0 references dangle. An orphan whose
provenance is a PR or an event is reported as a deviation rather than hidden:
the remedy is a board issue, and the filings that would close the largest
groups are listed in the lane issue (#1178) rather than mass-created as a proxy
for the rule.

The two probes that matter are `REAL-DATA-LABEL-REMOVAL-IS-REFUSED` and
`REAL-DATA-DECLARATION-REMOVAL-IS-REFUSED`: they take the **real** ledger and
the **real** committed snapshot, remove exactly one real fact, and require the
real check to refuse it by id. A rule proved only against a fixture has not
been shown able to fail on the data it actually reads.

## Escalation, ownership and review cadence

**Ownership.** The lane that owns the incident owns its RCA and its corrective
actions; the owning lane is recorded in the artifact's field table and on each
action. `governance/lessons/` (governance lane) owns the ledger, the policy and
this gate — not the incidents in it.

**Escalation.** An action that its owning lane cannot close within the
milestone is escalated by recording the follow-up issue in `remediation_issue`
— the gate refuses an open action that names no issue, so an unresolved action
cannot quietly disappear. A `critical` severity incident is raised to the
parent epic (#138) at the next review. An enforcement **error** blocks merge:
the gate is part of `make verify`.

**Scope, and why there are no exemptions.** The scope declaration lives in
[`policy.yaml`](policy.yaml) and is a *record* label: `incident` means "this
issue records an incident", and it is deliberately distinct from
`area:incident-response`, which says where the work lives. The gate refuses two
things by name, so the defect this replaced cannot come back as a YAML edit: an
`area:` label in `board.incident_label`, and any `board.exemptions` list at all.
A by-issue exemption is how this rule became inert — every holder of the area
label ended up exempt, so the check could no longer fail (issue #766). An issue
that records no incident simply does not carry the record label, and the finding
is only raised when the ledger has no `INC-*` line for it.

**Review cadence.** Every RCA is re-read and re-stamped (`reviewed_at`) at
least every 180 days (`review_cadence_days`); past that the gate reports
`rca-review-overdue`. At each milestone close, the open actions and suggestions
in the report (`.verify/lessons-report.json`) are re-checked and attached to
the closing evidence. Ledger review is part of milestone close-out, not a
separate ceremony.

## One ledger, one view

There is exactly one authoritative ledger — [`ledger.jsonl`](ledger.jsonl). The
hub's `vendor/CMR/docs/LESSONS.md` is a *rendered* view of the same register, not
a second source: the gate reads this ledger alone, and the CMR-hub asset is
treated as an optional, absent-unless-checked-out submodule source
(`governance/knowledge/sources.py`). Migrating that view into this register is
**dual-read, single-write**: readers may still resolve a hub asset when the
submodule is present, but nothing writes it, so the two cannot diverge into a
silent duplicate. The hub-side retirement is a follow-up, tracked on the board;
until it lands the hub asset remains read-only here.

## PMO-readable

Every learning carries what the PMO needs without a second store:
[`edges.py`](edges.py) exposes `pmo_rows(records, snapshot)` — one row per
learning with its `owner`, `status`, `class` and the `goal` (the epic, or the
milestone) it belongs to, derived from its origin through the committed board
snapshot. The lessons lane never invents an authority: `owner` comes from the
record when it carries one (a `SUGGEST-*` does) and the ticket projection
supplies it otherwise, and `goal` is resolved from the board snapshot rather
than stored again here.

## The incidents recorded so far

Seventeen real incidents from this repository's own history, each with an
artifact, a corrective action and a lesson — a `LESSON-*` once the change has
landed, a `SUGGEST-*` while it is still in flight — the eleven recorded below,
and the six the EPIC #708 wave registered in its own section. That count is
gate-asserted, not hand-maintained: `scripts/check-lessons.sh` fails
(`readme-incident-count-mismatch`) if this sentence and the ledger's actual
incident count ever diverge again (issue #1052 — a prior hand-merge conflict
in PR #1036 forced a manual union of two independently-authored counts).

| Incident | Origin | RCA | What it was |
|---|---|---|---|
| `INC-0001` | #153 | [`RCA-0001`](rca/RCA-0001-chronological-dispatch-false-green.md) | a gate merged before the text it asserted, green locally and red on every clone |
| `INC-0002` | #148 | [`RCA-0002`](rca/RCA-0002-uncommitted-completion-claim.md) | work reported complete while every file was still untracked |
| `INC-0003` | #155 | [`RCA-0003`](rca/RCA-0003-duplicate-lane-doc-change.md) | two lanes shipped a byte-identical 53-line change |
| `INC-0004` | #157 | [`RCA-0004`](rca/RCA-0004-claim-replay-historical-truth.md) | the claim audit judged a historical claim by today's snapshot |
| `INC-0005` | #157 | [`RCA-0005`](rca/RCA-0005-stale-snapshot-frontier.md) | a 19-minute-old snapshot named a closed issue as the frontier (**open**, #170) |
| `INC-0006` | #800 | [`RCA-0006`](rca/RCA-0006-agentconsole-wrong-host.md) | the AgentConsole go-live was planned against this repository's own Cloud Run pipeline while the fleet's hosting contract fixes the remote shared-services cluster as the only live host |
| `INC-0007` | #1029 | [`RCA-0007`](rca/RCA-0007-declared-not-exercised-golive.md) | Epic #607's go-live was declared for months and never exercised — the declarations were not backed by an exercised path (recorded by the #1029 lane) |
| `INC-0008` | #506 | [`RCA-0008`](rca/RCA-0008-date-bomb-seed-without-evaluation.md) | the two tests that were #506's acceptance proof pinned the quota **seed** day while the runner resolved the **evaluation** bucket from the live clock, so `47 passed` expired with the calendar and a quota-exhausted tenant was allowed for three days (**open**: the fix is PR #1026, unmerged) |
| `INC-0015` | #997 | [`RCA-0015`](rca/RCA-0015-zero-byte-gate-lock-wedge.md) | a leftover 0-byte gate lock was read as a live claim, wedging the box-wide concurrency cap and parking every other worktree's gate with no indication which lock caused it — promoted from `docs/rca/2026-09-16-pr-queue-clearing.md` (issue #1052) |
| `INC-0016` | #1036 | [`RCA-0016`](rca/RCA-0016-shared-core-file-collision.md) | three PRs collided extending the same shared "core" files with no append-only procedure; the class recurred against this ledger and README in PR #1036 — promoted from `docs/rca/2026-09-16-pr-queue-clearing.md` (issue #1052) |
| `INC-0017` | #1052 | [`RCA-0017`](rca/RCA-0017-rca-id-double-booking.md) | `docs/rca/2026-09-16-pr-queue-clearing.md` minted `RCA-0007`/`RCA-0008` in its own headings, double-booking ids the ledger already held for a different incident, and this README's hand-written incident count needed a hand-merge in PR #1036 |

## Measured state (2026-09-17)

`bash scripts/check-lessons.sh` on this branch, exit code 0. Both parts are
quoted, the provoked control included; the long policy refusals and the
per-suggestion/per-action deviation lines are elided:

```text
incidents: 17 (9 closed) | rcas: 17 | corrective actions: 21 (10 open) | lessons: 7
| suggestions: 13 | board issues carrying the `incident` record label: 5 | ledger
records reaching the board: 34 of 75 (41 orphaned)
  WARNING suggestion-open         SUGGEST-0001 is open (owner: gate lane); ...
  WARNING board-link-orphan       LESSON-0001 declares itself board-orphaned (lesson); ...
  WARNING corrective-action-open  CA-0007 is open; remediation is tracked in #170
lessons: OK (17 incident(s), 7 lesson(s) enforced, 72 deviation(s) tracked)
  probe AREA-LABEL-IS-NOT-AN-INCIDENT: PASS — a CLOSED issue labelled 'area:incident-response' produced 0 board finding(s) and is not among the 1 scanned record-labelled issue(s) ([901]); errors=[]
  probe RECORD-LABEL-WITHOUT-A-RECORD-IS-REFUSED: PASS — code=board-incident-without-rca subject=#900 errors=['board-incident-without-rca']
  probe LEDGER-INCIDENT-WITHOUT-RCA-IS-REFUSED: PASS — code=incident-without-rca count=1 errors=['corrective-action-unlinked', 'incident-without-rca', 'unknown-reference', 'unknown-reference']
  probe RECORD-LABEL-WITH-A-RECORD-IS-ACCEPTED: PASS — scanned=1 board finding(s)=0 errors=[] (the ledger traces #900)
  probe OPEN-RECORD-LABEL-IS-A-DEVIATION: PASS — code=board-incident-pending count=1 errors=[]
  probe EXEMPTIONS-CANNOT-BE-DECLARED: PASS — board.exemptions is not a supported scope declaration (...); the retired refs #141/#494/#495/#497 cannot be declared
  probe AREA-LABEL-CANNOT-BE-THE-RECORD-LABEL: PASS — board.incident_label='area:incident-response' is an AREA label (...)
  probe SHIPPED-POLICY-DECLARES-THE-RECORD-LABEL: PASS — incident_label='incident' exemptions attribute=False cadence=180
  probe REAL-BOARD-HAS-NO-UNRECORDED-RECORD-LABEL: PASS — snapshot: 5 issue(s) carry 'incident' (scanned=5), 4 carry 'area:incident-response' and 0 of them is treated as an incident; board findings=0 errors=[] retired refs reported=(none)
  probe MUTANT-DROPS-THE-REFUSAL: PASS — NOT-REFUSED board-incident-without-rca (the probe is proven able to fail)
  probe DOC-RCA-UNKNOWN-ID-IS-REFUSED: PASS — code=doc-rca-id-unknown count=1 errors=['doc-rca-id-unknown']
  probe DOC-RCA-CITATION-IS-ACCEPTED: PASS — doc-rca finding(s)=0 errors=[]
  probe DOC-RCA-MINT-MISMATCH-IS-REFUSED: PASS — code=doc-rca-artifact-mismatch count=1 errors=['doc-rca-artifact-mismatch']
  probe README-INCIDENT-COUNT-MISMATCH-IS-REFUSED: PASS — code=readme-incident-count-mismatch count=1 errors=['readme-incident-count-mismatch']
  probe README-INCIDENT-COUNT-MATCH-IS-ACCEPTED: PASS — finding(s)=0 errors=[]
  probe REAL-DOCS-RCA-HAVE-NO-UNKNOWN-IDS: PASS — doc-rca finding(s)=0
  probe REAL-README-INCIDENT-COUNT-MATCHES-LEDGER: PASS — finding(s)=0
  probe MUTANT-DROPS-DOC-RCA-REFUSAL: PASS — NOT-HELD doc-rca-id-unknown (the probe is proven able to fail)
  probe MUTANT-DROPS-README-REFUSAL: PASS — NOT-HELD readme-incident-count-mismatch (the probe is proven able to fail)
  probe DUPLICATE-ID-IS-REFUSED: PASS — code=duplicate-id count=1 errors=['duplicate-id']
  probe MUTANT-DROPS-DUPLICATE-ID-REFUSAL: PASS — NOT-HELD duplicate-id (the probe is proven able to fail)
  probe BOARD-LINK-MISSING-IS-REFUSED: PASS — code=board-link-missing subject=LESSON-0002 errors=['board-link-missing']
  probe BOARD-LINK-ORPHAN-DECLARATION-IS-ACCEPTED: PASS — missing=0 orphan-deviations=4 errors=[]
  probe BOARD-LINK-DANGLING-ORIGIN-IS-REFUSED: PASS — code=board-link-dangling subject=INC-0001 count=1 errors=[...]
  probe BOARD-LINK-UNLABELLED-IS-REFUSED: PASS — code=board-link-unlabelled subject=#901 errors=['board-link-unlabelled']
  probe BOARD-LINK-LABEL-PRESENT-IS-ACCEPTED: PASS — finding(s)=0 errors=[]
  probe REAL-LEDGER-HAS-NO-SILENT-ORPHAN: PASS — the REAL ledger: 0 silent orphan(s), 0 dangling reference(s), errors=[]
  probe REAL-BOARD-LINKAGE-CENSUS-IS-NOT-EMPTY: PASS — the REAL ledger names 5 issue(s) as an incident origin; 5 carry `incident`; the board rule scanned 5; unlabelled=0
  probe REAL-DATA-LABEL-REMOVAL-IS-REFUSED: PASS — the REAL ledger names 5 issue(s); removed 'incident' from #148 and the REAL check reported 1 finding(s) for it, errors=['board-link-unlabelled']
  probe REAL-DATA-DECLARATION-REMOVAL-IS-REFUSED: PASS — the REAL ledger carries 13 declaration(s); removed LESSON-0001's and the REAL check reported 1 finding(s) for it, errors=['board-link-missing']
  probe MUTANT-DROPS-BOARD-LINK-MISSING: PASS — NOT-HELD board-link-missing (the probe is proven able to fail)
  probe MUTANT-DROPS-BOARD-LINK-DANGLING: PASS — NOT-HELD board-link-dangling (the probe is proven able to fail)
  probe MUTANT-DROPS-BOARD-LINK-UNLABELLED: PASS — NOT-HELD board-link-unlabelled (the probe is proven able to fail)
  PROBES: PASS (33 of 33)
negative-control: OK — an area label cannot manufacture an incident, a
record-labelled issue with no ledger record is still refused, no exemption can
be declared, the refusal is proven able to fail, and the ledger -> board linkage
(a silent orphan, a dangling reference, an incident-origin issue with no record
label) is refused on the real ledger and board with one real fact changed
```

The real-board probe is the measurement the fix is about: **4 issues carry
`area:incident-response` and 0 of them is treated as an incident**, while the
gate is green with no exemption anywhere. Before this change all four were
findings, then all four were exempt — i.e. the rule was wrong about every
holder, and then unable to fail (issue #766).

Five errors were raised and fixed while seeding the ledger — five artifacts
that were not yet committed (`rca-artifact-untracked`). That is the gate doing
its job on its own author: an uncommitted RCA does not exist (`RCA-0002`).

### The EPIC #708 wave (2026-09-14)

Six incidents from the fleet runaway-prevention wave. Each one is **open** against
the child issue that carries its corrective action, and each RCA is a committed
artifact in [`rca/`](rca/). The wave's incidents carry an `event` origin rather
than an issue origin because the committed board snapshot does not reach them —
which is `INC-0014` itself.

| Incident | Tracked by | RCA | What it was |
|---|---|---|---|
| `INC-0009` | #729 | [`RCA-0009`](rca/RCA-0009-infra-limits-tmpfs-exhaustion.md) | a shared 16 GiB `/tmp` filled to 100 %, and a 0-byte write passed as evidence |
| `INC-0010` | #726 | [`RCA-0010`](rca/RCA-0010-a2a-no-arbitration.md) | agent-to-agent dispatch with no arbiter between two claimants |
| `INC-0011` | #724 | [`RCA-0011`](rca/RCA-0011-gate-stacking.md) | gates stacked on one box with no lock and no queue |
| `INC-0012` | #723 | [`RCA-0012`](rca/RCA-0012-uncapped-redispatch.md) | an uncapped retry stacked 137+ gates and starved the box |
| `INC-0013` | #725 | [`RCA-0013`](rca/RCA-0013-inert-gate.md) | delivered gates that no gate invokes |
| `INC-0014` | #727 | [`RCA-0014`](rca/RCA-0014-stale-snapshot-no-trigger.md) | a committed snapshot with a timestamp, a tolerance and no trigger |

Six errors were raised and fixed while recording the EPIC #708 wave — the six
new RCA artifacts, each refused as `rca-artifact-untracked` until it was
committed. That is the gate doing its job on its own author a second time: an
uncommitted RCA does not exist (`RCA-0002`), and five errors had already been
raised the same way when this ledger was seeded.

## Layout

| Path | Role |
|---|---|
| [`ledger.jsonl`](ledger.jsonl) | the canonical ledger (single source of truth) |
| [`policy.yaml`](policy.yaml) | the `incident` record label and the review cadence — no exemptions |
| [`rca-template.md`](rca-template.md) | the RCA template every artifact follows |
| [`rca/`](rca/) | the RCA artifacts themselves |
| [`model.py`](model.py) | record kinds, vocabularies, findings, report |
| [`edges.py`](edges.py) | the typed ticket edges, node kinds and the PMO view (issue #402) |
| [`checker.py`](checker.py) | detection logic over the ledger and the board |
| [`cli.py`](cli.py) | `check` / `status` / `record` / `template` |
| [`negative_control.py`](negative_control.py) | the provoked controls for the board rule, mutant included (issue #766) |
| [`tests/`](tests/) | the detection suite — one planted defect per named finding |

## Related

- `governance/dispatch/` — the claim gate (the same audit lineage as
  `RCA-0004` and `RCA-0005`).
- `governance/conformance/` — the CMR class ladder a lesson's `class` rung
  comes from.
- `../../docs/QA-GATE.md` — how a check is wired into `make verify`.
- `../../docs/GOVERNANCE.md` — the repo governance contract.
