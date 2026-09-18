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
| `master` SHA this document is based on | `66fa3d4d65dde108234f8b197d57475154859029` (rebased from the original branch point `96ae0fba19c36764e1f0251070d7c77bb17b4a5d` once #1072 and #1073 landed) |
| Source of the §4 acceptance boxes | issue #803's 2026-09-17 re-measurement, taken against `master` `96ae0fb` and updated below against `66fa3d4` where #1072/#1073 changed the state |

The module version (`v0.1.0`) and the API's own `info.version` (`1.0.0`) are
two different numbers today. Cutting product `v1.0.0` does not require
touching the API version field; it requires the *exit criteria* in §4 below.

## 2. Surfaces under SemVer contract

Only surfaces with a verified defining artifact **and** a verified defending
gate are listed. Each path below was confirmed present with `test -e` on this
branch; the full existence check is pasted as PR evidence
(kushin77/agent-orchestrator#1075).

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
DONE/withdrawn; row 12 CODEOWNERS left OPEN at that measurement). This table
updates two of those rows against `66fa3d4` — the SHA this document is
based on (§1) — to record that #1072 and #1073 landed in between.

| Criterion | Evidence gate / command | Measured state today |
|---|---|---|
| Branch protection declared as code, applied, read-back gated | `verify.sh` gate `branch-protection` → `scripts/check-branch-protection.sh` | **Done** (#807) |
| Required-check question decided in an ADR and implemented | `docs/decision-records/ADR-0028-gate-status-without-actions.md` (Accepted) | ADR **accepted**; poster (`scripts/gate-status.sh`) is **wired into `governance/landing`** (#1072, landed at `66fa3d4`) — `governance/landing/ports.py` and `engine.py` call it at the PR boundary. **Now observed posting** (re-measured 2026-09-18): the poster published two real statuses onto ancestors of `master` — both `failure`, from the host-local straggler `governance/platform/branch-protection.yaml` records — and `post --attestation` publishes the gate's own rc and sha from a lane's attestation, so ADR-0028 step 2's precondition (a real status observed on a real commit) has fired |
| GR-15 holds (no GitHub Actions) | `verify.sh` gate `no-actions` → `scripts/check-no-actions.sh` | **Done** (#812) |
| Checked OpenAPI contract for the control-plane API | `verify.sh` gate `cpapi-spec-drift` → `scripts/check-cpapi-spec-drift.sh` | **Done** (#816) |
| Cross-tenant isolation proven, in the gate | `engine/memory/tests/test_isolation.py`, `identity/chat/tests/test_isolation.py`, `identity/chat/tests/test_failclosed.py` | **Done** (GR-15/AO-GR-15 evidence; originally reported "unproven" — that row was **withdrawn** in issue #803's correction 1) |
| `CODEOWNERS` + release version named | `.github/CODEOWNERS` | **Done** (#1073, landed at `66fa3d4` — a per-pillar ownership map, gated by `verify.sh` gate `codeowners` → `scripts/check-codeowners.sh`); this document is the release-version half |
| Required status check actually required (`required_status_checks`) | `governance/platform/branch-protection.yaml` → `protection.required_status_checks.contexts: [ao/gate-of-record]` (read back live: the API returns the same context) | **Done** — this row previously read "Pending owner step"; that was stale. The declaration itself records the #724 precondition as discharged (two real statuses published by `scripts/gate-status.sh post`, both `failure`, both on ancestors of `master`, plus the owner's acceptance of the host-local straggler that produced them). What is *not* done is **automatic** production per head: only a `disabled: true` Cloud Build trigger and a lane invoking the poster can write the status, so every open PR reads `BLOCKED` until its own head carries one (#1350, #1354). |

## 5. Residual risks named

- **A required check needs a status on EVERY PR head, and the producer is not
  yet automatic.** `governance/platform/branch-protection.yaml` records the #724
  precondition as discharged (two real, honest `failure` statuses observed from
  the code-native poster) and the requirement is applied — measured 2026-09-18:
  `required_status_checks.contexts = [ao/gate-of-record]` live, every open PR
  `BLOCKED`. But the requirement is evaluated per head, so a merge needs a
  status on the commit under review, and the only producers are the `verify`
  build and a lane invoking `scripts/gate-status.sh post` (#1350/#1354). The
  build config no longer *requires* the token to run (it would otherwise die
  before step 0 on an absent secret, reporting nothing about the code), so what
  it publishes now depends on `ao-gate-status-token` existing in Secret Manager:
  with the secret it posts the gate's own rc, without it the step logs `SKIPPED`
  and produces nothing (#1350). Until the secret exists, merges pass on the
  operator override (`enforce_admins: false`) — which is exactly how a required
  check degrades into a routinely-bypassed one.
- **A fail-closed producer can wedge the queue.** With the context required, a
  producer that stops publishing (expired token, disabled trigger) makes every
  PR unmergeable rather than merely ungated. The escape is the declared policy
  rather than anyone's memory: re-run `bash scripts/branch-protection.sh apply`
  with `required_status_checks` removed, and record that decision in
  `governance/platform/branch-protection.yaml` (ADR-0028, "Consequences").
- **`CODEOWNERS` covers pillar ownership, not a required review.** `.github/CODEOWNERS`
  now exists and is gated (#1073), but `required_pull_request_reviews` is
  still `null` in `governance/platform/branch-protection.yaml` (deliberately,
  per that file's own comment — a required review would break the
  autonomous-merge doctrine). `CODEOWNERS` therefore documents ownership; it
  does not make GitHub block a merge that skips the named owner.
- **`enforce_admins: false`.** Recorded deliberately in
  `governance/platform/branch-protection.yaml`: the operator must retain an
  override point on a wedged fleet, per the documented operator role. This
  means the protections in §2 do not bind an operator acting directly on
  `master` — a residual risk accepted, not overlooked.

## 6. How 1.0.0 gets tagged

Mechanics are `RELEASING.md`'s: an annotated `git tag -a v1.0.0` on `master`,
pushed after `make verify` is green on the tagged commit. The v1.0.0-specific
condition this plan adds: **the tag is cut only from a commit whose
`ao/gate-of-record` commit status (ADR-0028) reads `success`**. ADR-0028 step 2
has landed and is applied (`required_status_checks` requires the context, §4),
so GitHub itself refuses a merge that does not carry the status; the remaining
process-half is named in §5 — until the `verify` trigger is promoted out of
`disabled: true`, a lane produces that status by invoking the poster.

See `RELEASING.md` for the full pre-release checklist and tag mechanics.
