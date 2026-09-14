# Unified fleet-state projection (issue #323)

One work item's state is spread across five stores, each added by a different
lane. Nothing joined them, so the operator reconstructed "is anything of mine
still holding a lane?" by shelling into three CLIs, reading a ledger and listing
worktrees by hand — which is how an orphaned lane survived hours. `fleet/console.py`
is a dashboard for the **rungs only**; it joins none of these.

`fleet/state.py` is that one place. It is **read-only**: it opens the stores,
joins them by issue number and prints the projection. It never writes, moves or
reclaims anything — the destructive half deliberately stays in
`../governance/reconcile/`, so this module cannot become a second teardown path.

## The five stores it joins

| Store | Path | What it holds |
|-------|------|---------------|
| Lane identities | `<fleet>/lanes/*.json` | session id, agent, lane name, branch, worktree |
| Session heartbeats | `<fleet>/sessions/*.json` | the beat: pid, timestamp, declared state |
| Closure journals | `<fleet>/lifecycle/<issue>.json` | closing evidence and the verified commit |
| Claims | `.board/claims/*.json` | the claim ledger, one file per event |
| Directives | `<fleet>/sent/*.json`, `<fleet>/done/*.json` | authorisation, and whether it was consumed |

`<fleet>` is the namespaced runtime directory from `../fleet/runtime.py` — the
value of `AO_FLEET_DIR`, else `<repo>/.fleet`. A second fleet projecting its own
state therefore does not read the first fleet's.

The **authoritative** claim store is the one-file-per-event directory
`.board/claims/` (see `../governance/dispatch/claims.py`). The legacy single-file
ledger `.board/claims.jsonl` is frozen history and is read *only* because
`claims.read_ledger` merges it first so a replay stays time-ordered — ignoring it
would silently drop every claim written before issue #170.

## Derived, never a copy

Every field is recomputed from the stores on every invocation, so no snapshot can
go stale. Where two stores describe the same fact — the lane record's branch and
the heartbeat's branch, say — the projection does **not** pick a winner. It
compares them and reports a mismatch, naming both values:

```
lane-session-mismatch: branch: lane=issue-21-wrong session=issue-21
```

That is the property the gate pins: **a second store cannot disagree with the
first without the projection showing it.**

## Exit codes

The projection is also a health input, so one invocation answers the orphan
question *and* reports whether anything needs attention (repo tri-state
convention):

| Code | Meaning |
|------|---------|
| `0` | Assessed; nothing orphaned, shelved or wedged |
| `1` | Assessed; at least one item is orphaned, shelved or wedged |
| `2` | Cannot assess — neither the fleet dir nor the board dir exists |

Only **orphaned**, **shelved** and **wedged** items make it exit non-zero. A
`suspect` session — a fresh beat behind a missing process — is reported but not
failing, matching `../governance/reconcile/`'s rule that absence alone is weak
evidence.

## Findings

Each finding is named by a code from a closed vocabulary, so a caller cannot
invent a class the gate has not been taught to exercise.

| Code | Meaning |
|------|---------|
| `lane-session-mismatch` | the lane record and the heartbeat disagree about a field |
| `claim-agent-mismatch` | the claim holder is not the lane's or session's agent |
| `claim-without-lane` | a live claim with no lane and no heartbeat |
| `lane-without-heartbeat` | a provisioned lane whose session never beat |
| `orphan-lane-retained` | a lane whose beat is past the TTL is still provisioned |
| `shelved-without-claim` | a shelved lane holds no claim, so its work is unprotected |
| `claim-expired-with-lane` | a claim past its TTL still names a lane |
| `directive-consumed-claim-held` | the authorising directive is consumed but the claim is still held |
| `journal-closed-claim-held` | the closure journal carries evidence but a claim is still held |
| `directive-missing` | a claim cites a directive no mailbox in `sent/` or `done/` holds |

A record that cannot be parsed belongs to no single item, so it is reported
separately under `unreadable` rather than as a finding — but it still makes the
command exit non-zero: a store the projection cannot read is a store it cannot
attest, and reading that as an all-clear would be a false green.

## Usage

```bash
python3 fleet/state.py                 # human view; exit 1 when not clean
python3 fleet/state.py --json          # the same projection, machine-readable
python3 fleet/state.py --root <dir>    # project a fixture tree (tests and the gate)
```

`--root` also sets the default `<root>/.fleet` and `<root>/.board`, which is what
lets the gate and the unit tests project a fixture tree with no live store
involved.

## The gate

`scripts/check-fleet-state.sh` (target `make fleet-state`, wired into
`make verify` as the `fleet-state` check and into the `lint` chain) exercises the
projection against **fixtures with one item in each state** — live, suspect,
orphan, shelved and wedged — and requires the exit code and the named item for
each. Its self-control then takes a *clean* fixture, flips one session to
orphaned, and requires the projection to go non-zero **and name the offending
item**; restoring the fixture must return it to zero. A gate whose failure path
cannot be provoked is a formality, so the mutation is part of the gate, not a
one-off command.

The unit suite lives in `../fleet/tests/test_state.py` and is run by the
`pytest-fleet` check inside `make verify`.
