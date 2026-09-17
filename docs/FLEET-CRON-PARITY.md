# FLEET-PARITY.md — dual-run parity harness (issue #714, EPIC #706 D6)

EPIC #706 ports the fleet's three cron jobs off this laptop into a container on
shared-services. Before any cutover, the container persona must be shown to
agree with the host persona — measured, not asserted. `infra/fleet/parity.py`
is that measurement.

## What it does

1. Reads the schedule from its one owner, `fleet/cron.py:MARKERS`, and the
   dry-run form of each marker from `infra/fleet/dev_run.py:ROLES` (D2's own
   role table — never a second copy). A schedule the two disagree about is
   refused by name (`role-table-drift`), the same rule D2 uses.
2. Takes **one** snapshot of `.fleet`/`.board` and copies it into two isolated
   tmp roots, one per persona. Both personas necessarily point
   `AO_FLEET_DIR`/`AO_FLEET_BOARD_DIR` at their own copy (that is the
   isolation); what differs between them is the rest of the environment:
   - `local` — the ambient host environment, unextended beyond the two
     isolation paths (how `fleet/cron.py`'s own crontab lines invoke each job
     today).
   - `container` — `infra/fleet/env_contract.py`'s full resolved environment
     layered on top (`AO_FLEET_REPO`, `AO_FLEET_DRY_RUN`,
     `AO_FLEET_CRON_INTERVAL`, `AO_FLEET_PORT`, `AO_FLEET_ROLE_TIMEOUT`, …) —
     the same contract `infra/fleet/entrypoint.sh` enforces. A reported diff
     is therefore a claim that one of those variables changes a role's
     decision, never a claim about the state roots (isolated by construction
     on both sides).
3. Dispatches every role **twice** per persona, back to back, against the same
   copy — simulating a lost-lock race. The second pass's own manifest must be
   empty (no-op); a non-empty one is reported as `not-idempotent`.
4. Normalizes captured output (timestamps, pids, durations, session ids,
   absolute repo paths) before comparing across personas — the harness
   compares *decisions*, not incidental bytes.
5. Never dispatches `fleet/watchdog.py run` for real: it spawns detached
   rungs, and `dev_run.ROLES` already marks it `not-dispatched` with its
   reason. That row is present in the evidence, never silently dropped.

`fleet/lease.py` (D5) is blocked-by this issue and does not exist in this
checkout. `parity.py` imports it lazily inside a `try/except` and skips the
lease-enforced assertion when it is absent (`lease_asserted: false`). Absent
the lease, `one_writer` is established **structurally**: each persona's
dispatch runs against its own isolated snapshot copy, so only that persona's
own process can be the writer of its own copy. A genuine violation — the same
logical path attributably written by both personas in the same tick — is
reported by name, never assumed away.

## Evidence schema — `.verify/fleet-parity.json`

Top-level keys match issue #714's own acceptance shape (`{ticks, one_writer,
diffs}`), plus the richer detail behind them:

| Key | Meaning |
|---|---|
| `schema` | `fleet-cron-parity-v1` |
| `verdict` | `ok` / `not-ok` / `cannot-assess` |
| `ticks` | number of dispatch ticks compared (default 3, `--ticks`) |
| `one_writer` | `true` only when no overlapping attributable write was observed between personas across every tick |
| `diffs` | `[]` on success; each entry names the tick, role, and the two personas' decisions that disagreed (or the overlapping paths, for a `one_writer` violation) |
| `idempotency` | `[]` on success; each entry names the persona/tick whose second (lost-lock) dispatch was not a no-op |
| `lease_asserted` | whether `fleet/lease.py` was importable and exercised directly |
| `schedule` | the markers read from `fleet/cron.py` |
| `roles` | the dry-run role table read from `infra/fleet/dev_run.py`, and each role's disposition |
| `refusal` | present only on `cannot-assess`: `{code, detail}` |

Exit codes (repo tri-state convention): `0` OK / `1` NOT-OK (a diff, a
non-idempotent rung, or a `one_writer` violation) / `2` CANNOT-ASSESS (the
schedule could not be read, or the role table drifted from `fleet/cron.py`).

## Running it

```bash
make fleet-parity                       # pytest suite, then 3 real ticks
python3 infra/fleet/parity.py --ticks 5 # more ticks, evidence at the default path
```

No network. Every dispatch is bounded by `--timeout` (default 120s per role).
The harness never mutates the live `.fleet`/`.board`: it snapshots once and
operates on isolated copies for the whole run.

## Scope note

This lane owns `infra/fleet/parity.py`, `infra/fleet/tests/test_parity*.py`,
the single `fleet-parity` Makefile target, and this document. It does not
touch `infra/fleet/dev_run.py`, `inventory.yaml`, `fleet/cron.py`,
`fleet/lease.py`, or any compose/Dockerfile/entrypoint file — those are other
lanes' files (see `docs/EXECUTION-PLAN.md`).
