# `control-plane/cli/` — the remote command center CLI

The **client half** of the fleet's remote control channel (issue **#556**, RC-5
of EPIC **#551**; decided by [`ADR-0025`](../../docs/decision-records/ADR-0025-remote-control-transport.md)
and served by RC-3's [`control_api.py`](../../portal/server/control_api.py)).

It is a thin operator CLI. It speaks **only** the RC-3 API — `POST
/api/control/<family>/<action>` on the console app the fleet already runs — it
prints **one receipt per action**, and it **refuses locally** when it cannot
verify the plane. It never writes fleet state itself: it opens no file, signals
no process and reads no `.fleet/` record. Nothing else in this repo reaches the
fleet off the host.

```bash
python3 control-plane/cli/main.py --help           # the verb table (the issue's Verify)
python3 control-plane/cli/main.py status           # one receipt
python3 control-plane/cli/main.py --dry-run pause  # the request, sent nowhere
python3 control-plane/cli/main.py --json verbs     # the machine document
```

Flags go **before** the verb. Everything after the verb is forwarded verbatim to
the lever's own CLI, which owns the meaning of its arguments
(`… audit --snapshot .board/snapshot.json`).

## The verbs — one declared command id each

The CLI owns no vocabulary. Every verb it speaks is a reference into
[`control-plane/control/verbs.yaml`](../control/verbs.yaml) (RC-2, issue #553),
read at run time — so a registry that renames, withholds or drops a verb makes
the CLI refuse **by name** instead of drifting into a `422` from the plane.

| verb | command id | effect class | capability |
|---|---|---|---|
| `status` | `fleet.status` | `read` | `fleet:read` |
| `verbs` | `fleet.verbs` | `read` | `fleet:read` |
| `pause` | `fleet.pause` | `hold` | `fleet:operate` |
| `resume` | `fleet.resume` | `hold` | `fleet:operate` |
| `stop` | `fleet.stop` | `stop` | `fleet:operate` |
| `kill` | `fleet.kill` | `stop` | `fleet:operate` |
| `override` | `fleet.override` | `irreversible` | `fleet:override` |
| `audit` | `board.audit` | `read` | `board:read` |

**Why `audit` is the board's.** The landed vocabulary declares no `fleet.audit`.
The one declared, exposed verb whose local name is `audit` and whose subject is
the fleet's own ledger is `board.audit` (`governance/dispatch/cli.py audit`).
Inventing a verb to fit the brief is exactly what RC-2 forbids, so the CLI names
the declared id and records the choice here.

`override` is the only `irreversible` row, so it is the only verb that needs an
explicit `--confirm fleet.override` before anything is sent — and irreversibility
is read from the registry's `effect_class`, never inferred from a verb's name
(`docs/REMOTE-CONTROL-GAP-ANALYSIS.md` §8.4). A `--dry-run` is *not* gated by it:
the guard protects the send, not the inspection, and teaching an operator to type
the confirmation habitually is the habit the guard exists to prevent.

## The caller

The caller **is** the console session — `os-session-token`, presented as a
cookie. There is no second credential, no machine identity and no service
account (ADR-0025 D2). Supply it with `--session` or `AO_CONTROL_SESSION`; a
secret belongs in the environment or a secret manager, never in a tracked file
(GR-6). With no session, the CLI refuses **before sending anything**: an
unauthenticated control request is not something to ask the plane about.

The plane is `--plane` (default: the console's own bind, `127.0.0.1:8787`). There
is no second listener; a remote operator reaches it only through a declared,
flag-gated exposure (ADR-0025 D1.5).

## Every failure is a named reason

The plane's own refusal codes are rendered as names, so an operator can act on
the answer instead of decoding a number. The verdict is this repo's tri-state:
**0** the plane returned a receipt · **1** a named refusal · **2**
CANNOT-ASSESS — no verdict was obtainable. An unreachable plane therefore exits
`2`, not `0`, and never as a silent success.

| plane says | named reason | exit |
|---|---|---|
| `404 feature_disabled` | the family is currently switched off at the flag (`surfaces.remote_control` — declared in `infra/feature-flags/registry.yaml`, and **enabled by default** per the 2026-09-21 GR-5/AO-GR-6 reversal, so this is a runtime state, not the shipped posture): invisible, not merely unauthorised | 2 |
| `404 not_found` | the plane serves no control route at that address | 1 |
| `401 unauthorized` | the plane verified no caller session | 1 |
| `405 method_not_allowed` | the family is POST-only | 1 |
| `422 unknown_verb` | the plane does not declare the verb | 1 |
| `403 verb_not_exposed` | the registry withholds the verb remotely | 1 |
| `403 scope_denied` | the caller is outside the platform scope | 1 |
| `403 permission_denied` | the caller lacks the verb's declared capability | 1 |
| `409 duplicate_command` | already applied; the plane hands the **original receipt** back | 1 |
| `409 lever_refused` | the local lever declined; not retried another way | 1 |
| `503 lever_unreachable` | the lever could not be reached, so nothing was applied | 2 |
| `503 vocabulary_unavailable` | the plane could not read its own vocabulary | 2 |
| `400 invalid_request` | the plane rejected the request shape | 1 |
| (no answer at all) | the plane could not be reached; nothing was delivered | 2 |
| (a shape the CLI cannot read) | the plane's verdict is unknown | 2 |

The CLI's **own** refusals, all made before anything is sent: `no_session`,
`confirmation_required`, `verb_not_exposed`, `surface_drift`,
`vocabulary_unreadable`, `address_invalid`, `contract_unavailable`.

Where a status can carry several codes (`403`, `409`, `503`, and `404`) the code
the plane's **own envelope** carried is the one named. The seam's offline
`FixtureTransport` raises with the status alone, so on that path the CLI names
**every** code the status allows and takes the verdict those codes agree on —
a guess would be a refusal asserting a fact it does not hold.

A refusal prints to **stderr** (a pipeline of receipts is never polluted by one);
with `--json` the machine document goes to **stdout** so a caller can read
`refusal.code` there and the exit code carries the verdict.

## What it does not do

* **no dashboard** — text and structured JSON only (ADR-0022, consumed by
  ADR-0025 §6.2);
* **no fleet-state write** — the CLI's only reach is the plane, and its test
  suite asserts that no module in `aoctl/` opens a file or names a process/file
  mutation primitive;
* **no transport of its own** — every call goes through
  [`integrations/paperclip/client.py`](../../integrations/paperclip/client.py)
  (ADR-0016, consumed by ADR-0025 D5), and the suite asserts it on the import
  graph. The seam's offline `FixtureTransport` is what keeps the gate off the
  network.

## Layout

| File | What it is |
|---|---|
| `main.py` | the entry point: `python3 control-plane/cli/main.py <verb>` |
| `aoctl/cli.py` | argparse, the invocation order, the output shapes |
| `aoctl/vocabulary.py` | the surface table, and the registry consumed from RC-2 |
| `aoctl/plane.py` | the RC-3 wire, over the boundary's transport seam |
| `aoctl/refusals.py` | the named refusals and the tri-state exit contract |
| `aoctl/receipt.py` | the receipt, and the replay receipt RC-4 hands back |
| `aoctl/contract.py` | the console constants, read from the modules that declare them |
| `tests/` | the suite: the vocabulary, the refusals, the wire, the CLI, `main.py` |

## Running the suite

```bash
python3 -m pytest control-plane/cli -q
python3 control-plane/cli/main.py --help
```

**Not wired into the gate yet — deliberately.** RC-8 (#559) is this EPIC's single
writer for `Makefile`, `scripts/verify.sh`, `scripts/pytest-suites.txt` and
`docs/README.md`. RC-8 must declare `control-plane/cli` in the suite manifest so
`check-drift.sh` stops warning about a committed `tests/` directory, and (per
`check-gate-coverage`) a declared suite must also be **named** by a gate — so the
manifest entry and the `verify.sh` entry land together, in that lane.
