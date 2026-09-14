# `integrations/paperclip/` — the canonical paperclip boundary adapter

This directory is **the one adapter module for the paperclip boundary**. It is
canonical: every lane that works on the paperclip seam extends *this* module as
`integrations/paperclip/<concern>.py`,
`integrations/paperclip/<concern>/`, or
`integrations/paperclip/adapters/<family>/` — never as a parallel top-level
`paperclip/` module (issue #457, [ADR-0016](../../docs/decision-records/ADR-0016-paperclip-boundary-single-module.md)).

The module landed with issue #428 (PR #433, commit `9cd5794`) and resolves the
mismatch list in [`docs/PAPERCLIP-ING-INTEGRATION.md`](../../docs/PAPERCLIP-ING-INTEGRATION.md)
§5. The mode decision is [ADR-0013](../../docs/decision-records/ADR-0013-paperclip-ing-integration.md);
the ownership boundary is [ADR-0012](../../docs/decision-records/ADR-0012-hermes-paperclip-boundary.md).

## The files

| File | Role |
|---|---|
| `client.py` | the `Transport` protocol, `HttpTransport` and `FixtureTransport`; company scoping, `Authorization: Bearer`, `X-Paperclip-Run-Id` on mutating calls only |
| `mapping.py` | the deterministic mapper: seeds/persona cards → agents, claims + board snapshot → tickets, the budget rail → costs, rung beats → heartbeats |
| `model.py`, `cli.py` | `plan` (offline dry-run sync plan), `check` (tri-state conformance), `push` (the network path) |
| `budget.py` | the budget rail — scope, currency, hard-stop and the ticket receipt (§5.7–§5.10) |
| `adapters/<family>/` | the per-family parity projections (EPIC #410) — `approvals`, `heartbeat`, `secrets`, `skills` — layered over the seam |
| `tests/` | the offline fixture transport and the mapper/validator suites |

## The corrected glob per briefed lane

The lane briefs written before this module landed named paths that never
existed. Read each briefed glob as the corrected path below; a lane that builds
the briefed path verbatim creates a second module for one boundary, which
ADR-0012 forbids.

| Briefed lane | Briefed-but-wrong glob | Corrected glob |
|---|---|---|
| #412 | `paperclip/auth/**` | `integrations/paperclip/auth.py` or `integrations/paperclip/auth/` |
| #413 | `paperclip/api/**` | `integrations/paperclip/api.py` or `integrations/paperclip/api/` |
| #414–#419 | `paperclip/adapters/<name>/**` | `integrations/paperclip/adapters/<name>/` |
| #447 | `paperclip/reporting/**` | `integrations/paperclip/reporting.py` or `integrations/paperclip/reporting/` |

A second module for this integration is **refused by name** by
[`scripts/check-paperclip-canonical-module.sh`](../../scripts/check-paperclip-canonical-module.sh),
wired into `make verify` as `paperclip-canonical-module`. Issue #448 landed the
guard; issue #457 (ADR-0016) removed its escape hatch — the former EPIC #410
`paperclip/` tree was consolidated under `integrations/paperclip/adapters/`, and
*any* second `paperclip/` module now fails the gate rather than being reported as
`KNOWN`.
