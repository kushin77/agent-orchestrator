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

# Refresh the committed boundary snapshot (the only network-touching path).
python3 governance/board/cli.py export-boundary --out .board/boundary-snapshot.json

# Run the offline tri-state boundary gate (0 OK / 1 NOT-OK / 2 CANNOT-ASSESS).
python3 governance/board/cli.py boundary-check
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
| [`cli.py`](cli.py) | `check` / `exceptions` / `export-boundary` / `boundary-check` operator surface |
| [`boundary.py`](boundary.py) | cross-repo execution-boundary detector (issues #125, #388) |
| [`boundary-baseline.json`](boundary-baseline.json) | legacy quarantine of the 11 vendor-compliance children, by name |
| [`exceptions.yaml`](exceptions.yaml) | the exception registry (empty by default) |
| `violations.jsonl` | append-only record of every gate failure, by check (created on first failure — not committed) |
| [`reviews/`](reviews/) | recorded board reviews with compliance evidence |

## Cross-repo execution boundary (issue #388)

The boundary detector (`boundary.py`, landed by #387) flags a backlog item filed
on this board whose body declares a foreign repo — work that must be handed to
another repo's board, never done from here (NG4). This module wires it into
`make verify`:

- `cli.py export-boundary` regenerates `.board/boundary-snapshot.json` from the
  GitHub REST API. It is the only network-touching path here (GR-15: no GitHub
  Actions). The snapshot carries each issue's `number, title, state, labels,
  milestone, parent, blocked_by, closed_at` **and `body`** — the body is what the
  detector reads, and the main `.board/snapshot.json` has no body (which the
  detector would read as a false green by missing field).
- `cli.py boundary-check` (wrapped by `scripts/check-cross-repo-boundary.sh`) is
  the offline tri-state gate: `0` OK, `1` NOT-OK, `2` CANNOT-ASSESS. It reads the
  committed snapshot, runs the detector, and applies `boundary-baseline.json`.
- `boundary-baseline.json` quarantines the 11 legacy vendor-compliance children
  (#126-#129, #131-#137) by name, honoured only while the issue tracking each
  entry (#358, the Board attack plan) is open. A quarantine whose tracker closed
  is a stale finding, and a new cross-repo child is never excused.
- A snapshot whose records lack `body` is CANNOT-ASSESS, never OK.

## Related

- Implements issue #143 (milestone M24), parent #138, blocked-by #142.
- Wraps `governance/knowledge` (#139), `governance/conformance` (#140),
  `governance/lessons` (#141), `governance/remediation` (#142).
