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
| `board-incident-without-rca` | error | an incident-labelled issue was closed with no RCA record |
| `board-incident-pending` | deviation | an incident-labelled issue is open and has no RCA record yet |
| `board-incident-exempt` | deviation | an explicitly exempt issue; the reason is printed every run |
| `rca-review-overdue` | deviation | the RCA was not re-read within the cadence |
| `corrective-action-open` | deviation | the action is still in flight, tracked by its issue |
| `suggestion-open` | deviation | the improvement idea is still open |

`--strict` escalates every deviation to an error. That is the honest position:
the in-flight work and the historical backlog are real and named, and a
milestone that has caught up can raise the bar without editing the gate.

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

**Exemptions.** The scope declaration lives in [`policy.yaml`](policy.yaml).
An exemption must carry its reason, and the gate reports it (`board-incident-`
`exempt`) on every run — an exemption that is invisible is indistinguishable
from a bypass. The single exemption today is #141, the issue that installs this
process: requiring an RCA for the rule that requires RCAs is circular.

**Review cadence.** Every RCA is re-read and re-stamped (`reviewed_at`) at
least every 180 days (`review_cadence_days`); past that the gate reports
`rca-review-overdue`. At each milestone close, the open actions and suggestions
in the report (`.verify/lessons-report.json`) are re-checked and attached to
the closing evidence. Ledger review is part of milestone close-out, not a
separate ceremony.

## The incidents recorded so far

Five real incidents from this repository's own history, each with an artifact,
a corrective action and a lesson:

| Incident | Origin | RCA | What it was |
|---|---|---|---|
| `INC-0001` | #153 | [`RCA-0001`](rca/RCA-0001-chronological-dispatch-false-green.md) | a gate merged before the text it asserted, green locally and red on every clone |
| `INC-0002` | #148 | [`RCA-0002`](rca/RCA-0002-uncommitted-completion-claim.md) | work reported complete while every file was still untracked |
| `INC-0003` | #155 | [`RCA-0003`](rca/RCA-0003-duplicate-lane-doc-change.md) | two lanes shipped a byte-identical 53-line change |
| `INC-0004` | #157 | [`RCA-0004`](rca/RCA-0004-claim-replay-historical-truth.md) | the claim audit judged a historical claim by today's snapshot |
| `INC-0005` | #157 | [`RCA-0005`](rca/RCA-0005-stale-snapshot-frontier.md) | a 19-minute-old snapshot named a closed issue as the frontier (**open**, #170) |

## Measured state (2026-09-13)

`bash scripts/check-lessons.sh` on this branch, exit code 0:

```text
incidents: 5 (4 closed) | rcas: 5 | corrective actions: 7 (1 open) | lessons: 4
| suggestions: 3 | board incident-labelled issues scanned: 1 (1 exempt)
  WARNING suggestion-open         SUGGEST-0001 is open (owner: gate lane); ...
  WARNING suggestion-open         SUGGEST-0003 is open (owner: fleet lane); ...
  WARNING suggestion-open         SUGGEST-0002 is open (owner: governance lane); ...
  WARNING corrective-action-open  CA-0007 is open; remediation is tracked in #170
  WARNING board-incident-exempt   incident-labelled issue #141 is exempt: ...
lessons: OK (5 incident(s), 4 lesson(s) enforced, 5 deviation(s) tracked)
```

Five errors were raised and fixed while seeding the ledger — five artifacts
that were not yet committed (`rca-artifact-untracked`). That is the gate doing
its job on its own author: an uncommitted RCA does not exist (`RCA-0002`).

## Layout

| Path | Role |
|---|---|
| [`ledger.jsonl`](ledger.jsonl) | the canonical ledger (single source of truth) |
| [`policy.yaml`](policy.yaml) | incident label, exemptions and review cadence |
| [`rca-template.md`](rca-template.md) | the RCA template every artifact follows |
| [`rca/`](rca/) | the RCA artifacts themselves |
| [`model.py`](model.py) | record kinds, vocabularies, findings, report |
| [`checker.py`](checker.py) | detection logic over the ledger and the board |
| [`cli.py`](cli.py) | `check` / `status` / `record` / `template` |

## Related

- `governance/dispatch/` — the claim gate (the same audit lineage as
  `RCA-0004` and `RCA-0005`).
- `governance/conformance/` — the CMR class ladder a lesson's `class` rung
  comes from.
- `../../docs/QA-GATE.md` — how a check is wired into `make verify`.
- `../../docs/GOVERNANCE.md` — the repo governance contract.
