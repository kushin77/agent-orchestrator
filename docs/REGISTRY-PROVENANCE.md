# Registry provenance — harvested agent-profile / SME / persona assets (issue #145)

This is the provenance record (GR-10, provenance doctrine) for the registry
content backfilled by issue #145, *Cannibalize mature agent-profile / SME /
persona code into the registry*. Everything here is **derived from** the sources
below (vocabulary, field semantics, doctrine), not copied verbatim, and every
harvested asset records its `repo`, `path`, `license` and a `verdict`.

Verdicts follow the issue #145 harvest map: **READY** (usable almost as-is),
**PATTERN** (the shape is worth re-implementing here, the bash/GitHub-bound
machinery is not), **REFERENCE** (informs doctrine, not structure).

## Read `AGENTS.md` + `GOLDEN-RULES.md` first

The repo's own instructions govern; this file is the provenance ledger for the
harvest, not a contract. Safety rules honoured by this change: no secrets, no
signing-key edits, no `TODO`/`FIXME`/`HACK` markers, no trailing whitespace, no
literal `XXX` runs in committed files.

## Harvest table

| Source repo | Source path | Asset | License | Verdict | Feeds |
|---|---|---|---|---|---|
| `kushin77/leaderboard` | `lib/fleet-roster.sh` | ROLE/TIER/MODEL/TRANSPORT/EFFORT separation; `role_capabilities()` per-model JSON (strengths/weaknesses/cost_tier) | Proprietary (kushin77, All Rights Reserved) | PATTERN | schema axes `role`/`model`/`transport`/`effort`/`capabilityTier` in both registry schemas |
| `kushin77/leaderboard` | `docker/worker-fleet/personas.yaml` | 15 container personas (role/description/env/memory_file/cron_lines) | Proprietary (kushin77, All Rights Reserved) | READY | persona set already present; validated by the backfilled vocabulary |
| `kushin77/capital-underwriting` | `config/leaderboard/capability-registry.json` | per-role capabilities, `tools_allowed`, latency/cost tiers, `fallback` chains | Proprietary (kushin77, All Rights Reserved) | PATTERN | schema `fallbackChain` (role-id chain) |
| `kushin77/capital-underwriting` | `config/leaderboard/tier-policy.json` | complexity -> tier -> model mapping with a fallback chain; per-tier max_tokens/timeout | Proprietary (kushin77, All Rights Reserved) | PATTERN | model-tier/worker-model separation; FinOps ceiling motivation |
| `kushin77/capital-underwriting` | `config/leaderboard/route-policy.json` | fast/deep/strict routes -> agent chains + model tier | Proprietary (kushin77, All Rights Reserved) | PATTERN | fallback-chain + tier semantics |
| `kushin77/capital-underwriting` | `infra/docker/worker-fleet/personas.yaml` | ~36 personas (cron_lines/description/env/memory_file/role) | Proprietary (kushin77, All Rights Reserved) | READY | persona coverage; SME role set |
| `kushin77/capital-underwriting` | `scripts/agent/sme/{auditor,security,terraform,test-quality,prisma-db,sniper-generic}.txt` + `sme-dispatch.sh` | DOMAIN -> SME system-prompt library (6 prompt files, not the 10 the issue text estimated) | Proprietary (kushin77, All Rights Reserved) | REFERENCE | SME card expertise/posture for existing cards |
| `kushin77/CMR` | `onboarding/agent-profiles/role.schema.json` | canonical role vocabulary (12 roles), `model.tier`/`model.model`/`floor`/`mode`, `ownedLanes` enum (17 lanes) | No LICENSE file in the checkout (internal, kushin77) | READY | `roleId`/`modelTier`/`workerModel`/`canonicalLane` enums; the parity gate's canonical source, frozen at `registry/parity/canonical/cmr-role-vocabulary.json` |
| `kushin77/CMR` | `onboarding/agent-profiles/profiles/*.json` | per-role cards (`platform-sme`, `pmo-sme`, `sync-sme`, `iac-sme`, `security-sme`, `general`, ...) | No LICENSE file in the checkout (internal, kushin77) | REFERENCE | new SME cards `platform-sme`, `pmo-sme`, `sync-sme` |
| `kushin77/CMR` | `docs/SME-PROFILES.md` | SME card doctrine + lane map | No LICENSE file in the checkout (internal, kushin77) | REFERENCE | SME card structure |
| `kushin77/capital-underwriting` | `.claude/agents/{gcp-gatekeeper-sme,ltc-brain-fleet-sre,qa-playwright-sme}.md` | mature SME cards (frontmatter + Mission/Constraints/Approach/Output-Format) | Proprietary (kushin77, All Rights Reserved) | **ABSENT** | not used — see *Absent sources* |
| `kushin77/capital-underwriting` | `infra/docker/worker-fleet/fleet-roster.conf` + tri-state run-state (required/parked/unverified) | fleet roster config | Proprietary (kushin77, All Rights Reserved) | **ABSENT** | not used — see *Absent sources* |
| `kushin77/CMR` | `catalog/sme-registry.tsv` (with a `weekly_spend_ceiling` column) | per-SME FinOps ceiling table | n/a | **ABSENT** | `weekly_spend_ceiling` defined in-repo instead — see below |
| `kushin77/deepseek` | `config/capabilities.toml` | declared-data per-model capability catalog | MIT | READY | capability/tier modeling reference |
| `kushin77/deepseek` | `docs/operations/sme-card-template.md` + `scripts/sme-card-check.py` | SME-card shape enforcement (two kinds: `sme` vs `agent-profile`; frontmatter keys; description budget) | MIT | PATTERN | `mechanical-sme` card (ephemeral-but-frontloaded mechanical tier) |
| `kushin77/capital-underwriting` | `docs/strategy/gcp-gatekeeper-p0-roadmap.md` + `vendor/leaderboard/config/gcp-gatekeeper.vendor` | pre-merge Cloud Build gate design, trigger policy, hollow-closure audit | Proprietary (kushin77, All Rights Reserved) | REFERENCE | `gcp-gatekeeper-sme` card (the declared `.claude/agents/gcp-gatekeeper-sme.md` is absent) |

## Absent sources (declared by the issue, verified missing — not substituted silently)

| Declared source | Verified state | Consequence |
|---|---|---|
| `kushin77/capital-underwriting/.claude/agents/{gcp-gatekeeper-sme,ltc-brain-fleet-sre,qa-playwright-sme}.md` | absent; `.claude/agents/` holds only three `session.json` directories (`agent-7bfd1589`, `agent-e418370a`, `relentless-765`) and a `.gitkeep` | `gcp-gatekeeper-sme` was re-derived from the roadmap doc + vendor config above, not from the missing card |
| `kushin77/capital-underwriting/infra/docker/worker-fleet/fleet-roster.conf` | absent (`find` returns nothing) | the tri-state run-state (required/parked/unverified) was not backfilled |
| tri-state run-state in `personas.yaml` | absent — the file carries `cron_lines`/`description`/`env`/`memory_file`/`role` only, no run-state key | not modeled; recorded here rather than invented |
| `kushin77/CMR/catalog/sme-registry.tsv` (and its `weekly_spend_ceiling` column) | **does not exist**; `grep -rn weekly_spend_ceiling vendor/CMR` returns 0 matches | `weekly_spend_ceiling` is **defined in this repository** (schema + catalog + validator), not backfilled from CMR |
| `kushin77/capital-underwriting/scripts/agent/sme/*.txt` | present, but 6 prompt files (`auditor`, `prisma-db`, `security`, `sniper-generic`, `terraform`, `test-quality`) + `README.md` + `sme-dispatch.sh`, not the 10 the issue text estimated | harvested what exists |

## `weekly_spend_ceiling` — defined here, not backfilled

The issue asked for a per-SME FinOps ceiling as a **first-class profile field**.
The declared CMR source for it (`catalog/sme-registry.tsv`) does not exist, so the
field is **defined in this repository**:

* schema: `weekly_spend_ceiling`, a `number` with `minimum: 0`, optional (absence
  documented as "governed by the platform budget guardrail"), in both
  `registry/profiles/agent-profile.schema.json` and
  `registry/personas/persona-card.schema.json`;
* enforcement: `registry/profiles/validate.py` and
  `registry/personas/registry.py` refuse a non-numeric, boolean or negative value
  fail-closed;
* evidence: the `finops-steward` profile seed and the `platform-sme` persona card
  carry a ceiling; `invalid-negative-spend-ceiling.yaml` is the negative fixture.

## Parity gate direction

`registry/parity/` (library) + `scripts/check-registry-parity.sh` (gate) compare
the registry's *declared* vocabulary against the canonical CMR vocabulary. The
direction is documented as **equality, both ways** — see `registry/parity/README.md`.
The gate is tri-state (0 OK / 1 NOT-OK / 2 CANNOT-ASSESS) and is deliberately not
yet wired into `make verify` (that file is orchestrator-owned).

### The frozen canonical baseline

The canonical CMR source lives in the `vendor/CMR` submodule, which is
**unpopulated in a fresh git worktree**. A gate that reads it directly therefore
returns CANNOT-ASSESS (rc 2) on every clone and can never be enforced
(GR-29 — a rule that cannot run is advisory). So issue #145 **freezes** the
canonical vocabulary as a committed artifact:

| Artifact | Value |
|---|---|
| frozen baseline | `registry/parity/canonical/cmr-role-vocabulary.json` |
| vendor repo | `kushin77/CMR` |
| source path | `onboarding/agent-profiles/role.schema.json` |
| source sha256 | `64966d36dbf62f95a4a526ac78862587e3659eb4b23956a73e05c730f23c4528` |
| vendor commit | `b6c49aa03992dba9fe4b87b46104b8fc2f69f224` |
| extracted | 2026-09-14 |
| axes frozen | 12 roles, 4 tiers, 2 worker models, 17 lanes |

Two modes:

* **default (offline)** — registry vocabulary ↔ the frozen baseline. Deterministic,
  no network, no `vendor/` dependency; this is the mode `make verify` will run.
* **`--verify-source`** — frozen baseline ↔ the live `vendor/CMR` source (content
  sha256 + vocabulary). Reports a **stale freeze** (rc 1) when the live source has
  moved on; rc 2 when the source is unavailable (never 0).

**Refresh the freeze** (only where the submodule is populated), then commit the
result and update the sha256 in this table:

```bash
python3 registry/parity/parity.py --refresh-baseline
```

## New assets added by this issue

| Asset | Path | Provenance |
|---|---|---|
| profile schema backfill | `registry/profiles/agent-profile.schema.json` | CMR role schema + leaderboard fleet-roster |
| persona schema backfill | `registry/personas/persona-card.schema.json` | as above |
| profile seed | `registry/profiles/seeds/finops-steward.1.0.0.yaml` | leaderboard fleet-roster + capital-underwriting tier/capability policy |
| persona cards | `registry/personas/cards/{platform-sme,pmo-sme,sync-sme,gcp-gatekeeper-sme,mechanical-sme}.yaml` | CMR profiles + capital-underwriting sources (see table) |
| parity gate | `registry/parity/` + `scripts/check-registry-parity.sh` | new (this issue) |
| frozen canonical baseline | `registry/parity/canonical/cmr-role-vocabulary.json` | extracted from `vendor/CMR/onboarding/agent-profiles/role.schema.json` (sha256 `64966d36…c4528`, vendor commit `b6c49aa…224`); frozen in-repo because `vendor/CMR` is unpopulated in a fresh worktree |
| seed provenance | `registry/profiles/seeds/PROVENANCE.md` | extended here |
