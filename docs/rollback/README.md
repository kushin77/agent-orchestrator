# Rollout + rollback runbooks — elite surfaces (EPIC #878)

`governance/conformance/surfaces.yaml` holds 13 surfaces at `declared_class:
elite` plus one (`guardrails`) at `declared_class: faang` — and `enterprise`,
`faang` and `elite` all require the `rollback` evidence (CMR "enterprise": a
documented rollout + rollback procedure), so all 14 rows carry it, not just
the elite ones. `rollback` is `kind: manual`
(`governance/conformance/surfaces.py`): it is not machine-checkable in this
tree, so `scripts/check-surface-class.sh` reports the
`surface-manual-requirement` finding for every one of these surfaces **on
every run, unconditionally**, with no path convention it looks for. Writing
these runbooks does not and cannot silence that finding — see
[`../SURFACE-CLASS.md`](../SURFACE-CLASS.md#measured-evidence-vs-manual-evidence)
and `surfaces.py:14-16`: "never counted as met; silence would be a false
green." That is by design (issue #883 / ADR-0031's manual-evidence discipline
applied to `rollback`): a human records the procedure, a human reviews it, and
the gate keeps reporting because it cannot verify prose.

This directory is the recorded procedure for each of the 14 surfaces (13
`elite` + `guardrails` at `faang`). One file per surface, named after the
`surface:` key in `surfaces.yaml`:

| Surface | Runbook | Declared path |
|---|---|---|
| `portal` | [`portal.md`](portal.md) | `portal` |
| `gateway` | [`gateway.md`](gateway.md) | `gateway` |
| `telemetry` | [`telemetry.md`](telemetry.md) | `telemetry` |
| `registry` | [`registry.md`](registry.md) | `registry` |
| `module-registry` | [`module-registry.md`](module-registry.md) | `governance/modules` |
| `module-brief` | [`module-brief.md`](module-brief.md) | `integrations/paperclip/reporting` |
| `dispatch` | [`dispatch.md`](dispatch.md) | `governance/dispatch` |
| `isolation` | [`isolation.md`](isolation.md) | `governance/isolation` |
| `lifecycle` | [`lifecycle.md`](lifecycle.md) | `governance/lifecycle` |
| `reconcile` | [`reconcile.md`](reconcile.md) | `governance/reconcile` |
| `hermes-integration` | [`hermes-integration.md`](hermes-integration.md) | `integrations/hermes` |
| `knowledge` | [`knowledge.md`](knowledge.md) | `governance/knowledge` |
| `tagging` | [`tagging.md`](tagging.md) | `governance/tagging` |
| `guardrails` | [`guardrails.md`](guardrails.md) | `guardrails` (declared `faang`, not `elite` — included for completeness since `faang` also requires `rollback`) |

This directory is excluded from the top-level `docs/README.md` index by name
(`scripts/check-docs.sh`'s `idx_excluded_dirs`, the same convention used for
`docs/decision-records/`, `docs/spikes/`, `docs/contracts/` and `docs/rca/`):
its members are indexed here, not there.

Each runbook covers, for its surface: how it is normally rolled out, how a
break is detected, concrete rollback steps, and who/what is affected. None of
this changes `declared_class`, re-measures a surface, or touches the 3
class-ceilinged rows (`shell`, `github`, `commit-contract`) — those are
correctly capped below `elite` by shape and are out of scope.
