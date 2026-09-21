# Built-not-shipped — promotion-ledger disposition (AO)

> **Status:** institutional · **Issue:** #1618 (parent #1510) · **Gate:**
> [`scripts/check-feature-flags.py`](../scripts/check-feature-flags.py) (the
> `promotion-owner` arm + its `--self-test`) · **Rule:** every declared-off
> surface must name its promotion owner.

## What this records

`infra/feature-flags/registry.yaml` is the read-only flag registry; every
control-plane surface ships OFF until a reviewed go-live promotes it. Until
issue #1618 it declared **38 `default: off` surfaces** and not one carried an
owner, a promotion issue or a target date — so a surface *pending a reviewed
go-live* was indistinguishable from a surface *drifting with nobody
responsible* (issue #1540's gap).

This doc records the disposition #1618 assigned to each surface: the open
issue that owns its promotion (`promotion_issue:`), or `posture: hold` when no
promotion owner is yet assigned (the surface is declared-off debt until an
issue is filed and replaces `hold` with a number).

## The two fields

| Field | Meaning |
|-------|---------|
| `promotion_issue: "#<n>"` | The **open** issue whose lane owns the reviewed go-live that promotes this surface. |
| `posture: hold` | Declared off with **no** promotion owner yet assigned — invisible debt, not a decision. |

An entry with neither is refused **by name** by the gate. Entries already
`promoted: true` (`ci_cd.verify_trigger`, `ci_cd.apply_trigger`,
`surfaces.fleet_projection`, `surfaces.remote_control`,
`surfaces.operator_terminal`) are exempt: their promotion is recorded by the
`promoted: true` flag itself, so they owe no owner.

## Disposition

### Owned (`promotion_issue`) — 9 entries

| Surface | promotion_issue | Owning issue |
|---------|-----------------|--------------|
| `services.hermes` | `#1518` | hermes: fix or retire dead gateway provider + real `enable_hermes` gate |
| `services.org_chart` | `#1521` | console: add Org Chart + Skill Studio views |
| `services.skill_studio` | `#1521` | console: add Org Chart + Skill Studio views |
| `services.task_board` | `#1522` | console: add Task Board + fleet board views |
| `services.fleet_cron` | `#706` | EPIC: port the fleet's cron automation into shared-services |
| `surfaces.org_chart` | `#1521` | console: add Org Chart + Skill Studio views |
| `surfaces.skill_studio` | `#1521` | console: add Org Chart + Skill Studio views |
| `surfaces.task_board` | `#1522` | console: add Task Board + fleet board views |
| `surfaces.live_bridge` | `#1523` | console: resolve `/api/v1/bridge` consumer + wire operator verb catalogue |

### Held (`posture: hold`) — 27 entries

| Section | Surfaces |
|---------|----------|
| `services` | `registry`, `gateway`, `engine`, `guardrails`, `telemetry`, `identity`, `portal`, `web`, `paperclip`, `chat`, `mcp_outbound`, `sandbox_runtime`, `erp_module`, `erp_webhooks_bridge` |
| `surfaces` | `edge_cutover`, `portal_surfaces`, `finops_reports`, `telemetry_live_feed`, `ops_health`, `telemetry_exposition`, `fleet_health_export`, `chat`, `cockpit`, `remote_ssh_access`, `mcp_outbound`, `sandbox_runtime`, `erp_module` |

These 27 are declared-off with no open promotion owner. Two of them had a
sweep-resolved owning issue that is now **closed** (`paperclip` → #1515,
`finops_reports`/`ops_health` → #1520), so they are recorded `hold` — their
promotion decision is no longer an open chain; filing a new promotion issue is
the path that replaces `hold` with a number.

## Enforcement

`scripts/check-feature-flags.py` gained a promotion-owner arm (`_owner_errors`)
and a `--self-test` that provokes both directions: a non-promoted entry with
neither field is refused **by name**, and a present `promotion_issue` /
`posture: hold` passes. `scripts/verify.sh` runs the arm (`feature-flags`) and
its self-test (`feature-flags-self-test`) on every `make verify`.
