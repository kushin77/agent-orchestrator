# Release plan — the v1.0.0 commitment

## 1. Purpose

This document names the v1.0.0 commitment this product makes to its consumers,
and states the SemVer contract (AO-GR-8, `RELEASING.md`) that binds every
release on top of it: an annotated `vX.Y.Z` tag on `master`, cut only from a
gate-green commit.

**Status line**

| Field | Value |
|---|---|
| Current version (`module.json` → `versions.latest`) | `v0.1.0` |
| Control-plane API version (`identity/cpapi/openapi.yaml` → `info.version`) | `1.0.0` |
| Measured | 2026-09-17 |
| `master` SHA at branch time | `96ae0fba19c36764e1f0251070d7c77bb17b4a5d` |

The module version (`v0.1.0`) and the API's own `info.version` (`1.0.0`) are
two different numbers today. Cutting product `v1.0.0` does not require
touching the API version field; it requires the *exit criteria* in §4 below.

## 2. Surfaces under SemVer contract

Only surfaces with a verified defining artifact **and** a verified defending
gate are listed. Each path below was confirmed present with `test -e` (§7).

| Surface | Defining artifact | Defending gate | Current version |
|---|---|---|---|
| Control-plane API | `identity/cpapi/openapi.yaml` (OpenAPI 3.0.3, 28 paths) | `scripts/check-cpapi-spec-drift.sh` (`verify.sh` gate `cpapi-spec-drift`, issue #816) | `1.0.0` (`info.version`) |
| Generated clients | `identity/cpapi/clients/` (`control_plane_client.py`) | `scripts/check-cpapi-spec-drift.sh` — the same gate; its own header states the spec "is what consumers generate clients from," and it does not carry a version distinct from the spec's | tracks `1.0.0` |
| Pillar boundaries | `docs/ARCHITECTURE.md` (five pillars + cross-cutting) | `scripts/check-surface-class.sh` (`verify.sh` gate `surface-class`), declared against `docs/SURFACE-CLASS.md` | n/a — architectural contract, not a numbered artifact |
| Branch-protection policy | `governance/platform/branch-protection.yaml` | `scripts/check-branch-protection.sh` (`verify.sh` gate `branch-protection`) | schema `ao.branch-protection/v1` |
| Gate-of-record status context | `scripts/gate-status.sh` (posts `ao/gate-of-record`) | `scripts/check-gate-status.sh` (`verify.sh` gate `gate-status`, ADR-0028) | context string, not versioned |

No MCP or agent-facing surface declares its own OpenAPI-equivalent contract
or gate yet, so none is listed here.

## 3. What MAJOR / MINOR / PATCH mean here

`RELEASING.md` states the rule in the abstract (breaking / capability /
fix). Concretely, against the surfaces in §2:

- **MAJOR** — a breaking change to a listed surface's defining artifact:
  a removed or incompatible-changed path/schema in `identity/cpapi/openapi.yaml`,
  a pillar boundary redrawn in `docs/ARCHITECTURE.md` in a way that changes a
  consumer-facing contract, or a change to `required_status_contexts` /
  `protection` in `governance/platform/branch-protection.yaml` that a
  consumer of the repo's own protection guarantee would need to react to.
  **Requires an ADR** in `docs/decision-records/` before or with the change
  (AO-GR-8, RELEASING.md "ADRs record decisions that change the
  architecture; bump MAJOR when one supersedes a prior contract").
- **MINOR** — a new capability: a new path added to `identity/cpapi/openapi.yaml`
  without removing or changing an existing one, a new pillar subsystem or
  `module.json` feature flag shipped flag-gated OFF by default (AO-GR-5).
- **PATCH** — a fix, a doc correction, or a gate hardening that does not
  change a defining artifact's observable contract (e.g. `check-cpapi-spec-drift.sh`
  catching a drift the router already had).

## 4. v1.0.0 exit criteria

One row per acceptance box from issue #803's 2026-09-17 re-measurement
("Re-measured 2026-09-17 against master `96ae0fb` — what is left"), which is
itself the corrected version of the epic body after its two correction
comments (row 10 OpenAPI, row 11 isolation, row 14 infra — all corrected to
DONE/withdrawn; row 12 CODEOWNERS left OPEN).

| Criterion | Evidence gate / command | Measured state today |
|---|---|---|
| Branch protection declared as code, applied, read-back gated | `verify.sh` gate `branch-protection` → `scripts/check-branch-protection.sh` | **Done** (#807) |
| Required-check question decided in an ADR and implemented | `docs/decision-records/ADR-0028-gate-status-without-actions.md` (Accepted) | ADR **accepted**; poster (`scripts/gate-status.sh`) wiring into the merge path is **pending #1072** |
| GR-15 holds (no GitHub Actions) | `verify.sh` gate `no-actions` → `scripts/check-no-actions.sh` | **Done** (#812) |
| Checked OpenAPI contract for the control-plane API | `verify.sh` gate `cpapi-spec-drift` → `scripts/check-cpapi-spec-drift.sh` | **Done** (#816) |
| Cross-tenant isolation proven, in the gate | `engine/memory/tests/test_isolation.py`, `identity/chat/tests/test_isolation.py`, `identity/chat/tests/test_failclosed.py` | **Done** (GR-15/AO-GR-15 evidence; originally reported "unproven" — that row was **withdrawn** in issue #803's correction 1) |
| `CODEOWNERS` + release version named | `.github/CODEOWNERS` (not yet present) | **Pending #1073** for CODEOWNERS; this document is the release-version half |
| Required status check actually required (`required_status_checks`) | `governance/platform/branch-protection.yaml` → `required_status_contexts: [ao/gate-of-record]` (declared, not yet in `protection`) | **Pending owner step** — ADR-0028 step 2, deliberately deferred until a real `ao/gate-of-record` status is observed on a real `master` commit |

## 5. Residual risks named

- **Required status check not yet required.** `governance/platform/branch-protection.yaml`
  declares `required_status_contexts: [ao/gate-of-record]` outside the
  `protection` block on purpose (ADR-0028): requiring a context nothing
  posts yet would deadlock every merge (the #724 defect class). Until
  #1072 lands and a real status is observed, a merge to `master` can still
  happen without a green gate being technically enforced by GitHub — only
  by process discipline (AGENTS.md rule 3, autonomous-merge mandate rule 10).
- **`CODEOWNERS` does not exist.** No per-pillar ownership is enforced by
  GitHub today; #1073 is the open lane. Until it lands, review/ownership is
  a convention, not a platform control — the same gap pattern ADR-0028
  named for branch protection before #807.
- **`enforce_admins: false`.** Recorded deliberately in
  `governance/platform/branch-protection.yaml`: the operator must retain an
  override point on a wedged fleet, per the documented operator role. This
  means the protections in §2 do not bind an operator acting directly on
  `master` — a residual risk accepted, not overlooked.

## 6. How 1.0.0 gets tagged

Mechanics are `RELEASING.md`'s: an annotated `git tag -a v1.0.0` on `master`,
pushed after `make verify` is green on the tagged commit. The v1.0.0-specific
condition this plan adds: **the tag is cut only from a commit whose
`ao/gate-of-record` commit status (ADR-0028) reads `success`**, once ADR-0028
step 2 (making that context a required check) has landed. Until step 2 lands,
`make verify` green plus this document's exit criteria (§4) all reading
"Done" is the bar; after step 2 lands, GitHub itself refuses the merge
otherwise, and the same bar becomes platform-enforced rather than
process-enforced.

See `RELEASING.md` for the full pre-release checklist and tag mechanics.
