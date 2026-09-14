# control-plane/functions — the cockpit function registry (RC-10, issue #565)

The **one declaration of every cockpit function**: the panels the cockpit
renders, the views it offers and the commands the operator types. Adding a
domain — FinOps, SLO, audit, tickets, paperclip — is a **declaration here**,
never a fork of the client. That is the extensibility contract
[`ADR-0026`](../../docs/decision-records/ADR-0026-control-substrate.md)
decision 10 fixes, and decision 9 fixes the order: this registry lands **before**
the cockpit client (RC-11, issue #566) so the client renders nothing the registry
does not name.

## What a function carries

| Field | What it is |
|---|---|
| `id` | the stable **mnemonic** the operator types (upper case, Bloomberg-function style) |
| `title` | the human name |
| `kind` | `panel` \| `view` \| `command` |
| `endpoints` | the API endpoints it binds, `<family>/<action>` |
| `stream` | the live surface it subscribes to, when it is live |
| `parameters` | `name`, `type`, `required`, `default` (`null` = "the lever's own default") |
| `scope` | the `identity/rbac` capability + scope level + tenant scope it needs |
| `effect_class` | `read` \| `hold` \| `stop` \| `irreversible` — **RC-2's vocabulary** |
| `audit` | `null` for a read; the recorded action for anything else |
| `flags` | every feature flag its visibility depends on |
| `roles` | CTO \| VP-Eng \| Manager \| Analyst — **designed for**, never *permitted by* |

## It consumes; it never re-declares

Every closed set this registry is held to is read from the authority that already
owns it. The gate proves each one is the live article, not a copy:

| Consumed | Authority |
|---|---|
| effect classes, capabilities, every verb a function binds | [`../control/verbs.yaml`](../control/verbs.yaml) (RC-2, #553) |
| the API route set a function may bind | [`../../portal/server/control_api.py`](../../portal/server/control_api.py) (RC-3, #554) |
| the live streams, and the event each carries | [`portal/server/fleet.py`](../../portal/server/fleet.py) and [`portal/server/live_feed.py`](../../portal/server/live_feed.py) — both the surface constant and `SSE_EVENT` |
| the scope levels | [`identity/rbac/model.py`](../../identity/rbac/model.py); the tenant scope is `fleet_authz.PLATFORM_ORG` |
| the feature flags a function names | [`infra/feature-flags/registry.yaml`](../../infra/feature-flags/registry.yaml) |
| the panels the cockpit renders | [`fleet/console.py`](../../fleet/console.py), rendered headlessly section by section |

## Role suitability is a filter, not a permission

`roles` says who a function is **designed for**. It never says who is permitted:
permission is the `capability` in `scope`, enforced per call by `identity/rbac`
through the control API (ADR-0026 decision 9). There is deliberately no function
in this package that answers "may this role use this function" — selecting a
function a role was not designed for is a UX choice, not a refusal, and the gate
fails if role information ever appears inside a `scope` block.

## Running it

```bash
bash scripts/check-control-functions.sh          # the gate (validate + suite + 7 provoked defects)
python3 control-plane/functions/cli.py validate  # the check alone
python3 control-plane/functions/cli.py frames    # render every function headlessly
python3 control-plane/functions/cli.py call LOG tail=20 bogus=1   # refuses `bogus` BY NAME
python3 -m pytest control-plane/functions/tests -q
```

Exit contract, consumed from `guardrails/honesty`: **0 OK / 1 NOT-OK / 2
CANNOT-ASSESS**. A CANNOT-ASSESS (no `python3`, no PyYAML, no `pytest`, an
unreadable fixture) is never a pass.

## The findings, by name

A defect is refused with a stable code, because the gate asserts that a provoked
defect is refused **by name** rather than merely with a non-zero exit:

`ENDPOINT-NOT-DECLARED` · `ENDPOINT-NOT-EXPOSED` · `UNKNOWN-EFFECT-CLASS` ·
`EFFECT-CLASS-MISMATCH` · `AUDIT-FORBIDDEN` · `AUDIT-REQUIRED` · `AUDIT-UNKNOWN` ·
`UNKNOWN-PARAMETER-TYPE` · `UNVERIFIED-PARAMETER` · `PARAMETER-REQUIRED-MISMATCH` ·
`UNKNOWN-PARAMETER` · `MISSING-PARAMETER` · `PARAMETER-TYPE` ·
`UNCLAIMED-MNEMONIC` · `UNDECLARED-PANEL` · `PHANTOM-PANEL` · `UNKNOWN-STREAM` ·
`MISSING-STREAM-FLAG` · `UNKNOWN-FLAG` · `CAPABILITY-UNKNOWN` ·
`CAPABILITY-MISMATCH` · `SCOPE-LEVEL-UNKNOWN` · `TENANT-MISMATCH` ·
`ROLE-AS-PERMISSION` · `UNKNOWN-ROLE` · `CONSUMES-DRIFT`

**The one cross-reference that matters most**: `UNCLAIMED-MNEMONIC`. Every verb
RC-2 marks `exposed: true` — every mnemonic the control API will accept — must be
claimed by a declared function, or the gate names the verb that no function
declares. That is what makes "every function the command line accepts is declared
here" a check rather than a claim. `UNDECLARED-PANEL` is its other direction: the
gate renders `fleet/console.py` headlessly and fails naming any panel the cockpit
renders that no function declares.

## Headless by construction

`cockpit_render.py` renders a declared function against a committed fixture —
no network, no TTY, no tmux, no token. The four honesty obligations of ADR-0026
D10 are branches there, not virtues: a failed read renders `FAILED` with its
reason (never a silently empty panel), `NO_DATA` is rendered as absence (never
as a green state), a disabled surface names the flag that turned it off, and a
command prints the endpoint and audit action it would use — there is no
optimistic `OK`.

## Not wired yet — deliberately

This lane does not touch `Makefile`, `scripts/verify.sh`,
`scripts/pytest-suites.txt` or `docs/README.md`: **RC-8 (#559) is this EPIC's
single writer** for the shared build files. Until RC-8 wires it, the gate runs
only when invoked directly, and `scripts/gate-coverage-baseline.txt` carries one
explicit `uninvoked` row naming #559 as the lane that removes it. An unwired gate
is a formality — which is exactly what RC-8 exists to fix.
