# RCA-0002 — a completion reported on files that were never committed

| Field | Value |
|---|---|
| RCA id | `RCA-0002` |
| Incident | `INC-0002` |
| Origin | `#148` — the issue whose work was reported done |
| Severity | `high` |
| Owner | engine lane |
| Reviewed | `2026-09-13` |

## Impact

Work on issue #148 was reported complete **with passing test evidence** while
every file it claimed to deliver was still untracked in the working tree.
Nothing had shipped: a clean clone, a reviewer, or any downstream consumer saw
no change at all. The reporting agent believed the issue was finished, so the
gap stayed invisible until the same lane re-read the tree.

## Detection

Manual, during the next pass over the same lane — not by a gate. The gate of
record could not see it: `make verify` runs against the working tree, and the
working tree contained untracked (but valid) files.

## Root cause

A working tree is not a commit, and "the tests pass here" was equated with "the
work is delivered". The verification story had no step that could distinguish
the two, so a passing local run was accepted as proof of delivery (GR-12: the
verdict must come from a mechanism that can fail).

## Corrective actions

- `CA-0003` — ship the actual work: PR #158 (`94d12fa`) landed
  `engine/core/leaderboard.py` and its suite (3 files, 280 insertions,
  `engine/core/tests/test_session_registry.py`).
- `CA-0004` — make delivery checkable: confirm every claimed file with
  `git ls-files --error-unmatch <file>` before reporting an issue done.

## Lessons

`LESSON-0002` — a work item that is not committed does not exist. Report
completion only after `git ls-files --error-unmatch <file>` succeeds for every
file named in the evidence, or after the commit is on the branch.

## Evidence

- PR #158 — `94d12fa`: `engine/core/leaderboard.py`,
  `engine/core/tests/test_session_registry.py`, `engine/core/__init__.py`.
- `git show --stat 94d12fa` — 3 files changed, 280 insertions.

## Follow-up

Nothing outstanding. This is the incident the lessons gate encodes mechanically:
`CA-0004`'s rule is why an RCA artifact that is not tracked fails the gate with
`rca-artifact-untracked` rather than being accepted on file existence alone.
