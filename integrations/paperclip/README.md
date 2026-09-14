# `integrations/paperclip/` — the canonical paperclip boundary adapter

This directory is **the one adapter module for the paperclip boundary**. It is
canonical: every lane that works on the paperclip seam extends *this* module as
`integrations/paperclip/<concern>.py` or `integrations/paperclip/<concern>/` —
never as a parallel top-level `paperclip/` module.

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
| #414–#419 | `paperclip/adapters/<name>/**` | `integrations/paperclip/<name>.py` or `integrations/paperclip/<name>/` |
| #447 | `paperclip/reporting/**` | `integrations/paperclip/reporting.py` or `integrations/paperclip/reporting/` |

A second top-level module for this integration is **refused by name** by
[`scripts/check-paperclip-canonical-module.sh`](../../scripts/check-paperclip-canonical-module.sh),
wired into `make verify` as `paperclip-canonical-module` (issue #448). The gate
recognises the separately-owned EPIC #410 parity-adapters tree and reports it as
`KNOWN`; any *undeclared* paperclip module fails the gate.
