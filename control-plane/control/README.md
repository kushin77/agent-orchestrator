# `control-plane/control/` — the control-verb vocabulary

One declaration of every control verb the fleet can be asked to perform
(issue **#553**, EPIC **#551**; decided by `ADR-0025`).

| File | What it is |
|---|---|
| `verbs.yaml` | **the registry** — every verb, once: id, effect class, required capability, the audit action it records, whether it is idempotent, whether it may be reached remotely, and the refusal codes it can return |
| `schema/verbs.schema.json` | the shape, including the two **closed** sets (effect classes, refusal codes) |
| `cli.py` | `validate` — schema checks **plus** the cross-reference against the five lever files, in both directions |
| `tests/test_verbs.py` | unit tests for the closed sets, the audit rule and the cross-reference |

## Why it exists

ADR-0025 D2 fixed the API's shape: the resource segment is `fleet` and the
**action segment is owned here**. Before this file, a control action could be
named in `fleet/control.py`, `fleet/channel.py` or any of the three governance
CLIs, with nothing to say which of them was authoritative — and no way to tell
"we chose not to expose this" from "nobody wired this".

So every local verb is declared, including the ones we deliberately **withhold**
(`exposed: false` + a reason). A withheld verb is a decision on the record; a
dropped verb is a verb someone re-invents later.

## The two closed sets

**Effect class** — the blast radius, not the transport:

| class | meaning |
|---|---|
| `read` | changes nothing; safe to repeat; never audited as a control act |
| `hold` | suspends, resumes or (re)starts a unit; reversible by an opposite verb |
| `stop` | ends a running unit; reversible only by an explicit start/restart |
| `irreversible` | persists externally or in an append-only rail; no verb here undoes it |

**Refusal codes** — `401` no caller · `403` insufficient capability · `405`
wrong method · `409` duplicate in flight · `422` verb outside the vocabulary ·
`503` lever unreachable. Every verb carries at least `401/403/503`; the others
are declared only where the verb can really produce them.

## The rule that is easy to get wrong

**Exactly one audit record per applied command, and none for a read.** A read is
not a control act, so a read claiming an audit action is a defect, not a
formality — and a `hold`/`stop`/`irreversible` verb without one is the silent
action ADR-0022 refusal 4 forbids. Both directions are enforced and tested.

## Where an invocation is recorded (the canonical ledger)

Issue **#1548** asked this directory to either add a ledger of its own or
confirm that `.board/dispatch-audit.jsonl` already is one. Measured, the answer
is **neither as stated**: no new ledger is needed, and `dispatch-audit.jsonl` is
not a ledger of these verbs — it is the **dispatch-arbitration** rail. The rails
record different acts; this section is the one canonical statement of which is
which.

| the question | the rail | written by | row |
|---|---|---|---|
| why a **dispatch/assign** was allowed or refused | `.board/dispatch-audit.jsonl` | `governance/dispatch/audit.py`, called from `claims.arbitrate()` | `ao.dispatch/audit-v1`: `kind` (`grant`/`refusal`), `issue`, `agent`, `at`, `lane`, `epic`, `directive_id`, `reason`, `detail` |
| which **control act** was ordered (`hold`/`stop`/`irreversible`) | `.fleet/slog.jsonl` | `fleet/channel.py::_slog()` | `ts`, `id`, `from`, `to`, `type`, `correlation_id`, `issue`, `severity`, `body` |
| the tamper-evident, per-tenant history of an action | `telemetry/ledger/` | the ledger's own hash-chained store (`verify` is its gate) | `telemetry/ledger/audit_event.schema.json` |

Neither JSONL rail is tracked: `.board/dispatch-audit.jsonl` and
`.fleet/slog.jsonl` are runtime artifacts a fresh clone does not have. Their
absence is not a defect, and a reader who looks for "the ledger" in
`git ls-files` will find neither.

**The `audit:` value is a label, not a field.** `verbs.yaml` declares an `audit:`
action for every non-read verb; read against the writer, that value names the act
in prose — it is not a key in the row. `_slog()` writes no `action`/`verb` key,
and a control act travels as `body == "control:<action>"` (e.g. `control:pause`),
truncated like every other body. The arbitration rail's `kind` is the *verdict*
(`grant`/`refusal`), not the verb id.

So the closed decision is: **`dispatch-audit.jsonl` is the canonical ledger for
the claim/dispatch (assign) family, and is not a ledger of all 64 verbs in
`verbs.yaml`.** A directory-local ledger would be a second, weaker authority for
the same question — which is why #1548 added no file.

`tests/test_ledger_doc.py` holds this section to the code: it reads `_slog()`'s
real keys and refuses the moment this table names a field the writer does not
produce, or stops naming either rail.

### Where the rails are silent — measured, reported, not patched

Both of these are real, and both live in files this lane does not own, so #1548
reports them rather than patching them here:

* a refusal raised *after* arbitration succeeds is not recorded, and the row
  written for that invocation still says `grant` — `governance/dispatch/claims.py`;
* a control-class refusal (`channel send`/`escalate` failing validation) prints to
  stderr and writes no row — `fleet/channel.py`.

## Running it

```bash
bash scripts/check-control-verbs.sh          # the gate (schema + both cross-reference directions + 4 negative controls)
python3 control-plane/control/cli.py validate
python3 -m pytest control-plane/control -q
```

## Wiring

The composite gate covers both halves of this directory: `scripts/verify.sh`
runs `control-verbs` (`bash scripts/check-control-verbs.sh`) and `pytest-control`
(`control-plane/control/tests`), and `scripts/pytest-suites.txt` declares the
suite — so nothing here depends on a caller remembering to run it. RC-8 (#559)
landed that wiring; the paragraph that used to say otherwise here had gone stale,
which is the same class of defect #1548 closed.
