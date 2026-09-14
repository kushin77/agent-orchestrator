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

## Running it

```bash
bash scripts/check-control-verbs.sh          # the gate (schema + both cross-reference directions + 4 negative controls)
python3 control-plane/control/cli.py validate
python3 -m pytest control-plane/control -q
```

## Not wired yet — deliberately

This lane does not touch `Makefile`, `scripts/verify.sh` or
`scripts/pytest-suites.txt`: **RC-8 (#559) is this EPIC's single writer** for the
shared build files. RC-8 must add `check-control-verbs` to `verify.sh`'s
`checks=()` array and `control-plane/control` to `pytest-suites.txt`. Until then
the gate runs only when invoked directly — and an unwired gate is a formality,
which is exactly what RC-8 exists to fix.
