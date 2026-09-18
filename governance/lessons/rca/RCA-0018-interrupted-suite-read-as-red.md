# RCA-0018 — an interrupted pytest suite reported as a phantom master red

| Field | Value |
|---|---|
| RCA id | `RCA-0018` |
| Incident | `INC-0018` |
| Origin | `#1082` |
| Severity | medium |
| Owner | governance lane (`scripts/check-portal-auth-env.sh`) |
| Reviewed | 2026-09-18 |

## Impact

`scripts/check-portal-auth-env.sh` mapped **every** non-zero `pytest` exit
code to FAIL. When a neighbouring lane's Ctrl-C in the shared box shell
interrupted the suite mid-run (pytest exit code `2`), the gate reported a red
it had no evidence for: the suite never finished, yet the check asserted a
definite failure. A phantom "master red" was dispatched off that report,
consuming triage time chasing a defect that did not exist.

## Detection

Read by a human triaging the dispatched "master red" (issue #1082); nothing
in the check distinguished "the suite ran and failed" from "the suite could
not run to completion" — both produced the same FAIL verdict.

## Root cause

The check shelled out to `pytest` and treated its exit code as binary
(`0` → pass, anything else → FAIL), when `pytest`'s exit codes are not
binary: `1` is a real test failure, `2` is an interrupted run (e.g. Ctrl-C),
`5` is "no tests collected". Collapsing all non-zero codes into FAIL let an
external interruption masquerade as a definite defect the gate could not
actually evidence.

## Corrective actions

- `CA-0023` (#1115, #1133) — `scripts/check-portal-auth-env.sh` now maps
  `pytest` exit codes individually: an interrupted run (rc `2`) is
  CANNOT-ASSESS, never a pass and never a FAIL it cannot evidence; "no tests
  collected" (rc `5`) still FAILs; and the check pins its own pytest rootdir
  so a stray `/tmp/pytest.ini` cannot hijack collection (the same class as
  #1212).

## Lessons

- `LESSON-0008` (closed, commits `074e3d481cfc16911da5c639d3e88e7002a0b180`
  and `075a246b8eeec0ed3846ff34cbbd5af422dcbaef`) — a gate that shells out to
  a test runner must map the runner's exit codes individually: only "tests
  ran and failed" is FAIL; "could not run" is CANNOT-ASSESS. Treating every
  non-zero exit as FAIL turns an environmental interruption into a phantom
  defect.

## Evidence

- `commit` `074e3d481cfc16911da5c639d3e88e7002a0b180` — #1115, declares the
  environment-variable surface and gates it, laying the groundwork for the
  rootdir pin.
- `commit` `075a246b8eeec0ed3846ff34cbbd5af422dcbaef` — #1133, pins the
  auth-env check's own pytest rootdir and maps pytest exit codes
  individually (interrupted → CANNOT-ASSESS, no-tests-collected → FAIL).
- `issue` `#1082` — the report that named the phantom red.

## Follow-up

Related: `#1212` (a stray `/tmp/pytest.ini` hijacking rootdir) is the same
class of defect — a gate that lets ambient state outside its own inputs
decide its verdict. No further action open on this RCA.
