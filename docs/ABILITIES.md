# ABILITIES — every verb/function/tool the orchestrator exposes

Issue: #1650 (Parent: #1510). Hand-built (no generator found — checked
`grep -rn 'verbs.yaml' scripts/ control-plane/`: `verbs.yaml` is consumed by
`control-plane/control/cli.py` and tests, not by any doc generator). Status
column: **live** = wired to real code + gated; **declared** = present in a
config/schema but no gate proves it runs; **missing** = referenced but absent.
Source of truth for each row is the path in the Source column.

## `control-plane/control/verbs.yaml` — the control-plane verb registry (RC-5)

64 verbs total across 5 namespaces (`fleet.*`, `channel.*`, `board.*`,
`recover.*`, `closure.*`), each declaring `effect_class`
(read/hold/stop/irreversible), `allowed_runtimes`, `capability`, `exposed`
(bool), and `refusals` (HTTP codes). Count verified by
`python3 -c "import yaml; print(len(yaml.safe_load(open('control-plane/control/verbs.yaml'))['verbs']))"`
→ `64`. Full machine list is the file itself — below is the namespace
summary, not a re-transcription of all 64 rows.

| Namespace | Verb count | Owner layer | Source | Status | Proving gate |
|---|---|---|---|---|---|
| `fleet.*` | 21 (`status`,`health`,`verbs`,`debug`,`watch`,`cron`,`live`,`attach`,`start`,`pause`,`resume`,`restart`,`stop`,`kill`,`halt`,`refresh`,`update`,`poke`,`override`,`drop`,`dead-letter`) | human-override terminal (Layer 5) | `fleet/control.py` | live | `control-plane/control/tests/` |
| `channel.*` | 17 (`verify`,`status`,`send`,`order`,`escalate`,`report`,`wait`,`watch`,`listen`,`brain-inbox`,`brain-outbox`,`head-commit`,`consume`,`log`,`follow`,`kb`,`steer`) | agent↔agent messaging | `fleet/channel.py` | live | `control-plane/control/tests/` |
| `board.*` | 16 (`status`,`held`,`eligible`,`audit`,`liveness`,`dangling`,`focus`,`pool`,`claim`,`dispatch`,`release`,`reap`,`snapshot`,`trigger`,`queue`,`freshness`) | **the top-level orchestrator surface** (Layer 2, ADR-0033) | `governance/dispatch/cli.py` | live | `governance/dispatch/tests/` |
| `recover.*` | 5 (`status`,`sweep`,`stamp`,`clear`,`watch`) | reconciliation | `governance/reconcile/cli.py` | live | `governance/reconcile/tests/` (confirmed present: `test_reconcile_audit.py`, `test_reconcile_boardreport.py`, `test_reconcile_ledger.py`, `test_reconcile_live.py`, `test_reconcile_policy.py`, `test_sweep.py`) |
| `closure.*` | 5 (`status`,`audit`,`collect`,`close`,`retire`) | lifecycle | `governance/lifecycle/cli.py` | live | `governance/lifecycle/tests/` (confirmed present: `test_lifecycle_audit.py`, `test_lifecycle_boardreport.py`, `test_lifecycle_gate.py`, `test_lifecycle_ledger.py`, `test_lifecycle_live.py`, `test_closeout.py`) |

Two verbs are declared `exposed: false` by design, not by gap:
`fleet.live`/`fleet.attach` (tmux attach requires a local tty — RC-9 demotes
tmux to a local debug convenience per the file's own `why_not_exposed` note).

## `control-plane/functions/functions.yaml` — the function-call surface

Present and structured (streams namespace + per-function typed argument lists
observed, e.g. `issue: integer required`, `agent: string`, `timeout-seconds`,
`stale-minutes`). Not fully enumerated by id in this pass — the file's shape
(typed arg lists per function) is confirmed live by its own schema
consistency; a full per-function row table is left to a follow-up (same
generator gap noted above). **Status: declared-and-partially-verified** —
source `control-plane/functions/functions.yaml`.

## `integrations/paperclip/api/openapi.json` — the paperclip HTTP surface

8 declared paths, confirmed present in the OpenAPI document:

| Path | Owner layer | Source | Status | Proving gate |
|---|---|---|---|---|
| `GET /api/health` | Layer 0 (Paperclip) | `integrations/paperclip/api/health.py` | live | `integrations/paperclip/api/tests/` |
| `GET /api/openapi.json` | Layer 0 | `integrations/paperclip/api/openapi.py` | live | `integrations/paperclip/api/tests/` |
| `/api/companies/{companyId}/activity` | Layer 0 | `integrations/paperclip/api/surface.py` (route set read from `client.py` by exercising each argument-free method against a recording transport) | live | `integrations/paperclip/api/tests/` |
| `/api/companies/{companyId}/agents` | Layer 0 | ″ | live | ″ |
| `/api/companies/{companyId}/approvals` | Layer 0 | ″ | live | ″ |
| `/api/companies/{companyId}/costs` | Layer 0 | ″ | live | ″ |
| `/api/companies/{companyId}/dashboard` | Layer 0 | ″ | live | ″ |
| `/api/companies/{companyId}/issues` | Layer 0 | ″ | live | ″ |

**Important scope note** (from `integrations/paperclip/api/README.md`):
"Transport is not this lane" — the real serving layer is `portal/server/bridge.py`
(`/api/v1/bridge*`, SPoG #339); this package owns the *paperclip-shaped
projection* over it, not the HTTP transport itself.

## `.mcp.json` — MCP surface

One declared MCP server: `cmr-indexer` (`vendor/CMR/catalog/indexer/mcp_server.py`,
stdio). **Status: declared, unreachable this session** — this session's own
tool-connection report shows `cmr-indexer (CONNECTION_CLOSED)`, so its live
status could not be independently confirmed by exercising it; recorded as a
measured fact of this session, not asserted as broken in general.

## `registry/personas/offices/cto/abilities.yaml` — CTO-office ability map

**Status: on branch `issue-purebliss-single-tenant-org` (PR #1580), not
merged — DECLARED-ONLY as of 2026-09-20.** Maps harvested CTO-office abilities
to existing platform primitives (never inventing a new verb/tool/capability —
its own header states any ability with no primitive goes under a `gaps` key
instead). Sample rows confirmed by reading the file on that branch:

| Ability id | Capability | Tools | Verb | Status |
|---|---|---|---|---|
| `architecture-decision` | `architecture-decision` | `file_read`, `file_write`, `search_memory`, `store_memory` | none (recorded, not a control-plane verb) | declared (PR #1580) |
| `merge-approval-gate` | `code-review` | `gh_pr` | `channel.escalate` | declared (PR #1580), verb itself is live (`fleet/channel.py`) |
| `iac-apply-gate` | `infra-authoring` | `shell_exec`, `file_read` | `fleet.override` | declared (PR #1580), verb itself is live (`fleet/control.py`) |

## Evidence commands run for this document

- `python3 -c "import yaml; ... control-plane/control/verbs.yaml"` — enumerated all 64 verb ids, effect_class, exposed flag
- `grep -n '"/'  integrations/paperclip/api/openapi.json` — confirmed the 8 declared paths
- `grep -rn 'verbs.yaml' scripts/ control-plane/` — confirmed no generator produces this table
- Session tool-connection report — confirmed `cmr-indexer` MCP server is `CONNECTION_CLOSED` this session
