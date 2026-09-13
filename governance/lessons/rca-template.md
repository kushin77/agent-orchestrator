# RCA template — root-cause analysis (issue #141)

Copy this file to `governance/lessons/rca/<RCA-id>-<slug>.md` and record the
matching `rca` line in [`ledger.jsonl`](ledger.jsonl). The artifact path in that
line must be the file you created, and the file must be **committed** — an RCA
that exists only in a working tree does not exist (incident `INC-0002`).

Every heading below is required: the gate reads this file and fails an artifact
that omits a section, so an "RCA" cannot be a one-line apology.

| Field | Value |
|---|---|
| RCA id | `RCA-0000` (must match the ledger `id`) |
| Incident | `INC-0000` (must match the ledger `id`) |
| Origin | `#000` — the issue, PR, commit or event that started it |
| Severity | `critical` / `high` / `medium` / `low` |
| Owner | the lane or role accountable for the corrective actions |
| Reviewed | `YYYY-MM-DD` (re-review at least every 180 days) |

## Impact

What was actually broken, for whom, and for how long. Say what the user or the
fleet observed, not what the code did. If nothing was broken, this is not an
incident.

## Detection

How the failure surfaced — and, if it surfaced late, what *should* have caught
it. "A human noticed" is a detection gap and belongs in the root cause.

## Root cause

The mechanism, not the symptom. Name the specific control that was missing or
bypassed, and why. If the honest answer is "the gate could not fail", say that
and link the gate that now can (GR-12).

## Corrective actions

One bullet per corrective action, each naming the `CA-<n>` id recorded in the
ledger and the issue or PR that carries it. An action with no owner and no
tracking issue is a wish; the gate rejects it.

## Lessons

The durable learning, written as a rule someone else can follow without reading
this document. Each lesson is a `LESSON-<n>` (closed, with a commit as evidence)
or a `SUGGEST-<n>` (open, with an owner and a remediation) in the ledger.

## Evidence

The merge commits, PRs, issues or artifacts that prove the actions landed. A
`commit` evidence entry names a real SHA in this repository's history — the gate
resolves it.

## Follow-up

Anything deliberately left open, with the issue that carries it. Silence here is
read as "nothing outstanding", so an open `CA-<n>` belongs on this list.
