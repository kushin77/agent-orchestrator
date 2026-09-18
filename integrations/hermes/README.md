# `integrations/hermes/` — the hermes peer-integration surface

This directory is the hermes half of the fleet's peer-integration boundary. It
is the thin, **read-only projection** of hermes's declared surface, so that the
`integrations` surface root has two declared peers rather than one held at
`faang` and one invisible to the class gate (issue #942).

It is **not** an embedded router and it is **not** the gateway's hermes
provider. The ownership boundary is [ADR-0012](../../docs/decision-records/ADR-0012-hermes-paperclip-boundary.md);
this module only maps the policy — it never couples the runtime.

## Which hermes is bound — and which is not

| Question | Answer |
|---|---|
| **Bound** | the `hermes-agents` **routing service** — the vendored module that owns agent routing / capability registry / escalation / model tiering (ADR-0012). Flask on port **9501**, endpoints `/health`, `/api/capabilities`, `/api/router`, `/api/tiering`. |
| **Runtime state** | `deployable-not-running` — measured in ADR-0012 Context §7: deployable, not wired into this repo, and therefore **never** depended on at dispatch time. |
| **Excluded (namesake)** | `gateway/providers/hermes.py` — an `OllamaProvider` adapter speaking the Ollama-compatible `/api/chat` wire shape for the Hermes-3 **LLM** (`hermes3`), at `http://localhost:8080/api/chat`. It is an inference-only endpoint, not the routing service, and it is named here as excluded so the two can never be silently conflated. |

The projection restates both halves deterministically: `hermes.bound` names the
routing service and `hermes.namesake_excluded` names the gateway provider with
its wire shape, model, endpoint and the reason it is out of scope.

## The files

| File | Role |
|---|---|
| `mapping.py` | the deterministic mapper: persona card + profile seed + FinOps tier table + gateway catalog row -> one canonical projection; carries the inline `CONTRACT`, and re-exports the YAML-subset loader and JSON-Schema-subset validator it shares with `integrations/paperclip/` |
| `policy.py` | the mapped tiering policy: the security floor, the escalation thresholds and the per-capability default/max tiers from `gateway/finops/tiers.yaml`, projected into one frozen value the mapper consumes |
| `audit.py` | the cross-source consistency audit: one journal entry per invariant (capability-set parity, tier parity, floor presence, tier coverage), each with a status and an evidence sha256 |
| `capabilities.schema.json` | the capability-registry schema: the shape of one projected capability tier-entry, enforced per entry by `check` |
| `client.py` | the read-only `HermesClient` over the four declared endpoints, its live `HttpTransport`, and the transport `Protocol` + offline `FixtureTransport` it shares with `integrations/paperclip/` |
| `model.py` | the frozen source shapes and the service-contract constants |
| `cli.py` | `project` (offline print), `check` (tri-state conformance), `probe` (the live path, never run by the gate) |
| `tests/` | the offline fixture transport and the mapper/validator/schema/audit suites, including the negative control |

## The contract

The projection is a single JSON document whose shape is the inline `CONTRACT`
in `mapping.py` (a closed vocabulary; `additionalProperties: false`). The
`check` verb holds it to four cross-source rules, in addition to schema
conformance (the `CONTRACT` plus each `tiering.capabilities` entry against
`capabilities.schema.json`):

1. **capability-set parity** — the persona card's `capabilitySet` and the
   profile seed's `capabilitySet` must agree;
2. **tier parity** — the persona card's `defaultModelTier` and the profile
   seed's `defaultModelTier` must agree;
3. **floor presence** — the projected tiering floor must be present and inside
   the closed tier vocabulary (`L0`/`L1`/`L2`);
4. **tier coverage** — every persona capability must have a `taskClasses`
   entry in `gateway/finops/tiers.yaml`.

A drift in any of the four is refused **by name**. The gate provokes the first
and fourth of these on every run (a phantom capability is planted in a scratch
copy of the surface, the checker must refuse it, and the copy is then removed).

## Offline by construction

`project` and `check` are pure functions of the tree — they read files and
import `mapping` (hashlib / json / pathlib / typing), and never import the
transport. The only network path in the adapter, `HttpTransport`, is
instantiated exclusively by the `probe` verb. The gate calls only `check`; the
tests inject `FixtureTransport`. No live request exists in the gate or the
tests.

## The seam shared with `integrations/paperclip/` (issue #1208)

This adapter was written as a systematic copy of the paperclip one, down to the
private helper bodies — and by the time the copy was cut the two validators had
already drifted (paperclip enforced `format: date-time`, `minimum` and
`maximum`; this one did not). The parts that were the same now live in one
place, `integrations/_seam/`:

| Seam module | What it holds | Re-exported as |
|---|---|---|
| `_seam/yaml_subset.py` | the stdlib-only YAML subset loader (the union of the two copies: the block parser plus the flow collections `gateway/finops/tiers.yaml` writes) | `mapping.load_yaml` |
| `_seam/schema.py` | the stdlib-only JSON-Schema subset validator, the **superset** — the keywords both copies implemented, at paperclip's stricter rules | `mapping.validate` |
| `_seam/wire.py` | `Response`, `decode` and the `error_for_status` rendering, with this adapter's label and status table bound in as `model.BOUNDARY` | `model.Response`, `model.error_for_status` |
| `_seam/transport.py` | the `Transport` protocol and the offline `FixtureTransport` | `client.Transport`, `client.FixtureTransport` |

Nothing else moved: the typed errors, the live `HttpTransport`, the source
shapes and the mappers are still this adapter's own, and neither adapter's wire
behaviour changed. The drift is closed **up**, not down — and
`tests/test_seam.py` provokes it: the three keywords this copy had ignored are
refused now, and `mapping.validate` *is* the seam's object, so a re-forked
helper fails the suite.

## Live sync

`integrations/hermes/sync/` (issue #889, lane L10) is the live counterpart to
the offline `project`/`check` path: `sync.live.serve_live_capabilities` calls
the service through the existing `HermesClient` seam (`HttpTransport` live,
`FixtureTransport` offline in tests — the same seam `probe` uses, no new
transport), validates every returned capability entry against the real
`capabilities.schema.json` (`mapping.validate`, never a hand-rolled check),
and diffs the live capability set against the static, declared projection
(`mapping.build_projection`'s `tiering.capabilities`), reporting
`live_missing` / `live_extra`. A capability entry that fails schema
validation is refused by name (`CapabilityRejected`) — never silently dropped
or accepted. Tests: `integrations/hermes/sync/tests/` (offline
`FixtureTransport`, no network), including the negative control for a
malformed capability entry.

## The gate

[`scripts/check-hermes-integration.sh`](../../scripts/check-hermes-integration.sh)
is the surface's dedicated gate, auto-wired by `scripts/discover-checks.sh`
(#698) as the `hermes-integration` check. Its exit-code contract is the repo
tri-state: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

## Declared class

The surface is declared in
[`governance/conformance/surfaces.yaml`](../../governance/conformance/surfaces.yaml)
as `integrations/hermes`. Its `declared_class` is the rung the tree actually
measures — contract + tests + controls (`policy.py`) + audit (`audit.py`) +
schema (`capabilities.schema.json`) + gate (`scripts/check-hermes-integration.sh`)
= `faang`, the same rung `integrations/paperclip/reporting` holds — never a
higher rung than its evidence supports, and never `elite` (it ships no live-sync
module).
