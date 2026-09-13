# Governance board charter — CMR/GDC knowledge compliance (issue #143)

Parent: #138. Blocked-by: #142 (resolved — remediation dispatch, #224).

## Why this exists

Issues #139-#142 each built one honest, offline, fail-on-violation gate:
the knowledge index (#139), CMR class/pattern/template conformance (#140),
the RCA + lessons ledger (#141), and remediation-issue dispatch (#142). Each
is real on its own — but nothing tied their outcomes to one accountable
decision-making body, and nothing said what happens when a violation repeats
or a lane wants an exception instead of a fix. This charter is that layer.

## Board composition and roles

| Role | Responsibility |
|---|---|
| **Board chair** | Convenes review, breaks ties, approves or denies exceptions |
| **Gate owner** | Maintains `governance/board/gate.py` and the required-check list; reports gate health |
| **Lane owners** | Own remediation for findings in their area; escalate blockers to the board |

In this fleet, the board is not a standing meeting of humans — it is the
brain/sister/subagent chain plus the human operator. The chair role is held
by whoever the operator designates for a review (recorded in the review
artifact, see below); the gate owner role is whoever last touched
`governance/board/`. This is deliberately lightweight: the enforcement gate
does not require a quorum to *run* (it runs on every `make verify`), only
exceptions and escalations require a board decision.

## Cadence

- **Continuous**: the enforcement gate (`governance/board/gate.py`) runs as
  part of `make verify` — every PR, every merge, no cadence to wait for.
- **Per-review**: a board review is recorded (see `reviews/`) whenever an
  exception is requested, an escalation fires, or a milestone closes. There
  is no fixed calendar cadence beyond that — reviews are triggered by events,
  not by a clock, because a calendar cadence on top of a continuous gate
  would just be theater.

## Quorum

A single accountable approver (the chair) is sufficient to decide an
exception or close an escalation, provided the decision is recorded in a
review artifact under `governance/board/reviews/` with the reasoning and
evidence. Recording *is* the quorum substitute: an undocumented decision is
not a board decision.

## Escalation

- A check failing the gate is a normal, expected, fixable event — it is not
  itself an escalation. The gate fails, the lane fixes it or requests an
  exception, done.
- A check that fails **3 or more times** (`governance/board/model.py`,
  `REPEATED_VIOLATION_THRESHOLD`) is a repeated violation and is escalated
  automatically: `governance/board/cli.py check` prints an `ESCALATION` line
  and the count is tracked in `governance/board/violations.jsonl`. Repeated
  violations require board review, not another silent re-run.
- A high-severity finding that has no timeboxed remediation path (i.e., the
  lane cannot fix it before the next merge and has not requested an
  exception) is routed to the board via the existing remediation-issue
  mechanism (#142): `governance/remediation` opens the issue, the board
  charter is what says who reads it and decides.

## Exceptions

An exception is a **named, reasoned, timeboxed** exemption for exactly one
check — never a blanket suppression, never permanent. See
`governance/board/exceptions.yaml` for the format and
`governance/board/gate.py`/`model.py` (`Exception_`) for enforcement:

- An active exception downgrades a failing check from `not-ok` to `excepted`
  — the gate does not turn red for it, but the failure stays fully visible in
  `.verify/board-report.json` and in `make verify` output. This is what keeps
  an exception from being a false-green: it is loud, not hidden.
- An expired exception (past its `expires` date) stops applying
  automatically. The underlying failure counts in full and is reported under
  `expired_exceptions` — nobody has to remember to revoke it.
- Renewing an exception is a new board decision (a new entry, a new
  `expires`), never an edit that pushes the date out on the same entry
  without a fresh review.

## What the gate enforces

`governance/board/cli.py check` (wired into `make verify` as the
`board-gate` check) re-runs, for real, every required gate:

| Check | Issue | Fails on |
|---|---|---|
| `knowledge-index` | #139 | invalid/incomplete institutional knowledge catalogue |
| `conformance` | #140 | un-classified or mis-classified milestoned work |
| `lessons` | #141 | an incident with no RCA, an action with no owner/evidence |
| `remediation` | #142 | the remediation scan itself cannot be assessed |

No-false-green doctrine (GR-12) applies to the board gate exactly as it does
to every check it wraps: `cannot-assess` is never reported as a pass, and a
suppressed-by-exception failure is never reported as a silent pass either —
see "Exceptions" above.

## Related

- Implements issue #143 (milestone M24), parent #138, blocked-by #142.
- Wraps: `governance/knowledge` (#139), `governance/conformance` (#140),
  `governance/lessons` (#141), `governance/remediation` (#142).
- `make board-gate` runs the check directly; `make verify` runs it as part of
  the standard gate via `scripts/check-board-gate.sh`.
