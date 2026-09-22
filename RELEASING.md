# Releasing agent-orchestrator

The repo is versioned like every governed fleet repo (GR-7): the **annotated
`vX.Y.Z` git tag on `master` is the single source of truth** for a release.
There is no GitHub Actions CI (GR-15 bans it); the gate of record is
`make verify`, run code-native from the ops runner, and published as a
GitHub commit status (`ao/gate-of-record`) by `scripts/gate-status.sh`
(ADR-0028, issue #803 P0-2). That status is not yet a *required* check on
`master` — declared in `governance/platform/branch-protection.yaml` under
`required_status_contexts` but deliberately excluded from `protection` until
a real status is observed on a real commit (ADR-0028 step 2). Until then,
releases are tagged locally from a gate-green commit, exactly as today.

**Release plan:** see [`docs/RELEASE-PLAN.md`](docs/RELEASE-PLAN.md) for the
surfaces under this SemVer contract, the v1.0.0 exit criteria, and the
residual risks named ahead of the v1.0.0 tag.

## How to release

1. Land changes on `master` via a PR (see `CONTRIBUTING.md`).
2. Ensure `make verify` is green (the gate of record).
3. Tag with an annotated tag and push:
   ```bash
   git tag -a vX.Y.Z -m "Release vX.Y.Z"
   git push origin vX.Y.Z
   ```
   - MAJOR = breaking change to a public contract (API / SDK / MCP surface,
     pillar boundary).
   - MINOR = a new capability (a new pillar subsystem, phase feature),
     enabled by default once merged and tested (AO-GR-6).
   - PATCH = a fix / doc correction / hotfix.
4. The tag is the release — no extra artifact step.

## Pre-release checklist

- [ ] `make verify` green on the tagged commit
- [ ] All referenced issues closed with evidence
- [ ] Docs in sync (`docs/ARCHITECTURE.md`, `docs/EXECUTION-PLAN.md`)
- [ ] No secrets in the release history (secret scan green)

## Notes

- New product surfaces ship **enabled by default** once merged and tested
  (AO-GR-6, `policy-gr5-enabled-by-default`, 2026-09-21). The IaC mandate is a
  separate rule (AO-GR-5): infrastructure is declared, never clicked. A surface
  is off only as a named exception citing the owner decision that keeps it off.
- ADRs record decisions that change the architecture; bump MAJOR when one
  supersedes a prior contract.
