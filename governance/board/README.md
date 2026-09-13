# Board — governance board and enforcement gate (issue #143)

The decision-making and enforcement layer over #139-#142. The charter
(`CHARTER.md`) is the human-readable governance document — roles, cadence,
quorum, escalation, exceptions. This module is its mechanical enforcement.

## Usage

```bash
# Re-run every required gate for real and report the aggregate status.
# Exit 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
python3 governance/board/cli.py check

# List declared exceptions and whether each is still active.
python3 governance/board/cli.py exceptions
```

`make board-gate` runs the check; `make verify` runs it as a check via
`scripts/check-board-gate.sh`.

## What it does, and does not do

The gate does not re-implement #139-#142 — it shells out to the exact
scripts `make verify` already runs (`scripts/check-knowledge-index.sh`,
`scripts/check-conformance.sh`, `scripts/check-lessons.sh`,
`scripts/check-remediation.sh`) and refuses to report `ok` unless every one
of them exits 0. This is deliberate: a gate that re-derives its inputs from a
different code path than the one it wraps can drift from what it claims to
enforce (the same drift risk `scripts/check-drift.sh` exists to catch
elsewhere in this repo).

`governance/sync` (provenance/drift/blast-radius for the CMR-hub sync
contract) is not wired into the board gate today — it operates per-consumer
against seed fixtures and has no single "the fleet's provenance is clean"
predicate yet. It is out of scope for this gate until #139-#142's sibling
issue for sync exists; noted here so the omission is honest, not silent.

## Layout

| File | Role |
|---|---|
| [`CHARTER.md`](CHARTER.md) | board roles, cadence, quorum, escalation, exceptions |
| [`model.py`](model.py) | check result, exception, and report data model |
| [`gate.py`](gate.py) | runs the required checks, applies exceptions, tracks repeat violations |
| [`cli.py`](cli.py) | `check` / `exceptions` operator surface |
| [`exceptions.yaml`](exceptions.yaml) | the exception registry (empty by default) |
| `violations.jsonl` | append-only record of every gate failure, by check (created on first failure — not committed) |
| [`reviews/`](reviews/) | recorded board reviews with compliance evidence |

## Related

- Implements issue #143 (milestone M24), parent #138, blocked-by #142.
- Wraps `governance/knowledge` (#139), `governance/conformance` (#140),
  `governance/lessons` (#141), `governance/remediation` (#142).
